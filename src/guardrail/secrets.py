"""Detect credential-shaped literals and redact their occurrences in review output."""

import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from guardrail.patch import FileChange
    from guardrail.source import ReviewSource


_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{12,}"
    r"|sk_(?:live|test)_[A-Za-z0-9]{16,}"
    r")(?![A-Za-z0-9_])"
)
_ASSIGNMENT = re.compile(
    r"""(?<![\w-])(?:[\w-]+[_-])?"""
    r"""(?:api[_-]?key|(?:access|secret|private)[_-]?key|(?:access|auth|refresh)[_-]?token"""
    r"""|client[_-]?secret|password|passwd|token|secret)["']?"""
    r"""(?:[ \t]*:[ \t]*str\b)?[ \t]*[=:][ \t]*"""
    r"""(?:"(?P<double>(?:\\[^\n]|[^"\\\r\n])*)"|'(?P<single>(?:\\[^\n]|[^'\\\r\n])*)'"""
    r"""|(?P<bare>[A-Za-z0-9_+/=.!@%&#?:*-]+)(?=[ \t\r\n,;}\]]|$))""",
    re.IGNORECASE,
)
_UUID = re.compile(r"[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}", re.IGNORECASE)
_REDACTED = "[REDACTED]"


def _secret_spans(text: str, *, bare_assignments: bool) -> Iterator[tuple[int, int]]:
    for match in _TOKEN.finditer(text):
        yield match.span()
    for match in _ASSIGNMENT.finditer(text):
        if match.lastgroup == "bare" and not bare_assignments:
            continue
        value = match[match.lastgroup]
        if len(value) < 16 or _UUID.fullmatch(value) or value.startswith(("${", "{{")):
            continue
        entropy = -sum(
            (p := count / len(value)) * math.log2(p)
            for count in Counter(value).values()
        )
        if entropy >= 3.5 or _TOKEN.search(value):
            yield match.span(match.lastgroup)


class SecretRedactor:
    """Index added locations and remember credentials across all sides of the patch."""

    def __init__(self, changes: Iterable["FileChange"] = ()):
        self.added_locations: list[tuple[str, int]] = []
        values: set[str] = set()
        for change in changes:
            if change.is_binary:
                continue
            for hunk in change.hunks:
                for line in hunk:
                    path = change.old_path if line.is_removed else change.new_path
                    spans = list(
                        _secret_spans(
                            line.value,
                            bare_assignments=bool(path and not path.endswith(".py")),
                        )
                    )
                    values.update(line.value[start:end] for start, end in spans)
                    if spans and line.is_added and change.new_path:
                        self.added_locations.append(
                            (change.new_path, line.target_line_no)
                        )
        self._known = (
            re.compile("|".join(map(re.escape, sorted(values, key=len, reverse=True))))
            if values
            else None
        )

    def redact(self, text: str, *, bare_assignments: bool = True) -> str:
        spans = list(_secret_spans(text, bare_assignments=bare_assignments))
        if self._known:
            spans.extend(match.span() for match in self._known.finditer(text))
        parts = []
        end = 0
        for start, stop in sorted(spans):
            if stop <= end:
                continue
            if start >= end:
                parts.extend((text[end:start], _REDACTED))
            end = stop
        return "".join([*parts, text[end:]])


class SecretCheck:
    name = "secrets"
    requires_whole_file = False

    def run(self, source: "ReviewSource") -> Iterator[dict[str, object]]:
        for path, line in source.redactor.added_locations:
            yield {
                "check": self.name,
                "file": path,
                "line": line,
                "severity": "blocking",
                "message": "Added line contains a likely hard-coded credential.",
                "suggestion": "Remove the value, rotate the credential, and load it from a secret store.",
            }
