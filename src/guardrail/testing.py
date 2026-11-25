"""Static test-reference and change-level test-edit checks."""

import ast
import re
import tokenize
from collections import defaultdict
from collections.abc import Iterator
from difflib import SequenceMatcher
from io import BytesIO, StringIO
from itertools import groupby
from pathlib import PurePosixPath

from guardrail.patch import FileChange
from guardrail.source import ReviewSource


def _test_file(path: str | None) -> bool:
    if not path or not path.endswith(".py"):
        return False
    p = PurePosixPath(path)
    return (
        bool({"test", "tests"}.intersection(p.parts[:-1]))
        or p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or p.name in {"tests.py", "conftest.py"}
    )


def _before(change: FileChange, after: bytes) -> bytes:
    """Undo hunks from the bottom so target coordinates remain valid."""
    lines = BytesIO(after.replace(b"\r\n", b"\n")).readlines()
    for hunk in reversed(change.hunks):
        start = hunk.target_start - 1 if hunk.target_length else hunk.target_start
        lines[start : start + hunk.target_length] = [
            line.value.encode("utf-8", "surrogateescape")
            for line in hunk
            if line.is_context or line.is_removed
        ]
    return b"".join(lines)


def _functions(tree: ast.AST, scope: tuple[str, ...] = ()) -> Iterator[tuple[str, int]]:
    for node in ast.iter_child_nodes(tree):
        child_scope = scope
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            child_scope = (*scope, node.name)
            if not isinstance(node, ast.ClassDef):
                yield ".".join(child_scope), node.lineno
        yield from _functions(node, child_scope)


def _references(files: dict[str, bytes]) -> set[str]:
    names = set()
    for path, data in files.items():
        if _test_file(path):
            for node in ast.walk(ast.parse(data, filename=path)):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    names.add(node.attr)
                elif isinstance(node, ast.ImportFrom):
                    names.update(alias.name for alias in node.names)
    return names


class FunctionTestReferenceCheck:
    name = "test-reference"
    requires_whole_file = True

    def run(self, source: ReviewSource) -> Iterator[dict[str, object]]:
        references = None
        for change in source.changes:
            path = change.new_path
            if path not in source.python_files or change.is_binary or _test_file(path):
                continue
            added = {
                line.target_line_no
                for line in change.added_lines
                if re.match(r"\s*(?:async|def)\b", line.value)
            }
            if not added:
                continue
            data = source.python_files[path]
            candidates = [
                (name, line)
                for name, line in _functions(ast.parse(data, path))
                if line in added
            ]
            if not candidates:
                continue
            try:
                existing = dict(_functions(ast.parse(_before(change, data), path)))
            except SyntaxError:
                # A patch may repair an unparsable base. Use the added declarations.
                existing = {}
            for name, line in candidates:
                if name in existing:
                    continue
                if references is None:
                    references = _references(source.python_files)
                if name.rsplit(".", 1)[-1] not in references:
                    yield {
                        "check": self.name,
                        "file": path,
                        "line": line,
                        "severity": "warning",
                        "message": f"Reference heuristic found no test reference to '{name}'.",
                        "suggestion": "Check whether a test exercises this function or method.",
                    }


def _logic_lines(text: str, first_row: int = 1) -> dict[int, tuple]:
    """Fingerprint physical code lines, ignoring comments and literal-only statements."""
    lines = text.split("\n")
    result = defaultdict(list)
    statement = []

    def finish_statement():
        if all(t.type == tokenize.STRING or t.string in ("(", ")") for t in statement):
            statement.clear()
            return
        for tok in statement:
            for offset, part in enumerate(tok.string.split("\n")):
                row = tok.start[0] + offset
                if row not in result:
                    raw = lines[row - 1].expandtabs()
                    result[row].append(len(raw) - len(raw.lstrip()))
                result[row].append((tok.type, part))
        statement.clear()

    brackets = 0
    try:
        for tok in tokenize.generate_tokens(StringIO(text).readline):
            if tok.type == tokenize.NEWLINE or tok.string == ";":
                finish_statement()
            elif tok.type not in {
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            }:
                statement.append(tok)
                if tok.string in {"(", "[", "{"}:
                    brackets += 1
                elif tok.string in {")", "]", "}"}:
                    brackets -= 1
                elif (
                    tok.string == ":"
                    and brackets == 0
                    and statement[0].string in {"def", "class", "async"}
                ):
                    finish_statement()
    except IndentationError as exc:
        # A fragment can dedent to a level whose opening suite is outside the hunk.
        # Lex the remaining lines independently, preserving their original columns.
        for row in range(exc.lineno, len(lines) + 1):
            if parts := _logic_lines(lines[row - 1]).get(1):
                result[row].extend(parts)
    except tokenize.TokenError as exc:
        # Tokens from an open parenthesized expression have already been emitted.
        # A truncated multiline string has no token; keep its visible value when
        # it belongs to an executable statement (e.g. an assignment).
        if statement and "multi-line string" in exc.args[0]:
            for row in range(exc.args[1][0], len(lines) + 1):
                result[row].append(lines[row - 1])
    finish_statement()
    return {first_row + row - 1: tuple(parts) for row, parts in result.items()}


def _decoded(data: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(BytesIO(data).readline)
    return data.decode(encoding).replace("\r\n", "\n")


def _logic_changes(change: FileChange, source: ReviewSource) -> Iterator[int | None]:
    """Yield new-side anchors, or None for a block that only removes logic."""
    head = source.python_files.get(change.new_path)
    if head is not None:
        old = _logic_lines(_decoded(_before(change, head)))
        new = _logic_lines(_decoded(head))
    for hunk in change.hunks:
        if head is None:
            old = _logic_lines(
                "".join(
                    line.value for line in hunk if line.is_removed or line.is_context
                ),
                hunk.source_start,
            )
            new = _logic_lines(
                "".join(
                    line.value for line in hunk if line.is_added or line.is_context
                ),
                hunk.target_start,
            )
        # Context separates edits even when code moves unchanged within one hunk.
        for context, block in groupby(hunk, key=lambda line: line.is_context):
            if context:
                continue
            lines = list(block)
            removed = [
                old[line.source_line_no]
                for line in lines
                if line.is_removed and line.source_line_no in old
            ]
            added = [
                line.target_line_no
                for line in lines
                if line.is_added and line.target_line_no in new
            ]
            matcher = SequenceMatcher(
                None, removed, [new[row] for row in added], autojunk=False
            )
            for tag, _, _, j, end in matcher.get_opcodes():
                if tag != "equal":
                    yield added[j] if j < end else None


class NoTestChangesCheck:
    name = "no-test-changes"
    requires_whole_file = False

    def run(self, source: ReviewSource) -> Iterator[dict[str, object]]:
        if any(
            _test_file(c.new_path) or (c.status != "copied" and _test_file(c.old_path))
            for c in source.changes
        ):
            return
        location = None
        for change in source.changes:
            path = change.new_path or change.old_path
            if change.is_binary or not path.endswith(".py") or not change.hunks:
                continue
            for line in _logic_changes(change, source):
                if line is not None:
                    location = (path, line)
                    break
                if location is None:
                    location = (path, None)
            if location and location[1] is not None:
                break
        if location:
            yield {
                "check": self.name,
                "file": location[0],
                "line": location[1],
                "severity": "warning",
                "message": "Python logic changed without a test-file change.",
                "suggestion": "Check whether the changed behavior needs a test update.",
            }
