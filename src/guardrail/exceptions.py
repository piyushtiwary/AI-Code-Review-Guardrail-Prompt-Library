"""Review exception handlers touched by a patch using AST and scanner locations."""

import ast
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from io import BytesIO
from tempfile import TemporaryDirectory
from tokenize import detect_encoding

from guardrail.source import ReviewSource


def _scanner_rows(tool: str, data: bytes, path: str, cwd: str) -> dict[int, set[str]]:
    if tool == "ruff":
        args = [
            "check",
            "--isolated",
            "--no-cache",
            "--ignore-noqa",
            "--target-version",
            "py312",
            "--select",
            "BLE001,E722",
            "--output-format",
            "json",
            "-",
        ]
        # Ruff expects UTF-8 on stdin. Bandit accepts the original Python encoding.
        encoding, _ = detect_encoding(BytesIO(data).readline)
        data = data.decode(encoding).encode("utf-8")
    else:
        args = ["-q", "--ignore-nosec", "-t", "B110,B112", "-f", "json", "-"]

    try:
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-m", tool, *args],
            cwd=cwd,
            input=data,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{tool} could not scan {path}: {exc}") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(f"{tool} failed scanning {path} (exit {result.returncode}).")
    # Bandit can log an internal plugin error and still return an empty report.
    if tool == "bandit" and b"\tERROR\t" in result.stderr:
        raise RuntimeError(f"bandit reported a scan error for {path}.")

    rows = defaultdict(set)
    try:
        report = json.loads(result.stdout)
        if tool == "bandit":
            if report["errors"]:
                raise RuntimeError(f"bandit reported a scan error for {path}.")
            report = report["results"]
        if not isinstance(report, list) or bool(report) != bool(result.returncode):
            raise ValueError("unexpected diagnostic list or exit status")
        for item in report:
            if tool == "ruff":
                code, line = item["code"], item["location"]["row"]
                expected = {"E722", "BLE001"}
            else:
                code, line = item["test_id"], item["line_number"]
                expected = {"B110", "B112"}
            if code not in expected or type(line) is not int or line < 1:
                raise ValueError("unexpected rule or line")
            rows[line].add(code)
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(f"Invalid {tool} report for {path}: {exc}") from exc
    return rows


def _anchor(handler: ast.ExceptHandler, added: set[int]) -> int | None:
    lines = added.intersection(range(handler.lineno, handler.end_lineno + 1))
    if lines:
        # Edits inside another handler or function belong to that inner scope.
        scopes = (
            ast.ExceptHandler,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
        )
        for node in ast.walk(handler):
            if node is not handler and isinstance(node, scopes):
                lines.difference_update(range(node.lineno, node.end_lineno + 1))
    return min(lines, default=None)


class ExceptionHandlingCheck:
    name = "exception-handling"
    requires_whole_file = True

    def run(self, source: ReviewSource) -> Iterator[dict[str, object]]:
        for path, added in source.changed_python_lines():
            data = source.python_files[path]
            tree = ast.parse(data, filename=path)
            handlers = {
                node: line
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)
                and (line := _anchor(node, added)) is not None
            }
            if not handlers:
                continue
            with TemporaryDirectory() as cwd:
                rows = _scanner_rows("ruff", data, path, cwd)
                for line, codes in _scanner_rows("bandit", data, path, cwd).items():
                    rows.setdefault(line, set()).update(codes)

            for node, anchor in handlers.items():
                # Bandit reports the except keyword. Ruff can point at a type
                # several lines later in a parenthesized exception tuple.
                end = node.type.end_lineno if node.type else node.lineno
                codes = set()
                for line in range(node.lineno, end + 1):
                    codes.update(rows.get(line, ()))
                if node.type is None and (node.lineno in added or codes):
                    message = "Bare except also catches interrupts and process exits."
                elif "B110" in codes:
                    message = "Broad exception handler silently passes."
                elif "B112" in codes:
                    message = "Broad exception handler silently continues."
                elif "BLE001" in codes:
                    message = "Broad exception handler may discard unexpected errors."
                else:
                    continue
                yield {
                    "check": self.name,
                    "file": path,
                    "line": anchor,
                    "severity": "warning",
                    "message": message,
                    "suggestion": "Catch the expected exceptions or re-raise unexpected errors.",
                }
