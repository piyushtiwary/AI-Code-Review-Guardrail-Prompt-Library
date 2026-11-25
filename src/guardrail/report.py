"""Render a completed review, including source availability, to the terminal."""

from rich.console import Console
from rich.text import Text

from guardrail.review import Finding, Severity
from guardrail.source import ReviewSource


def render_report(
    console: Console,
    source: ReviewSource,
    findings: list[Finding],
    model_status: str | None = None,
) -> None:
    added = sum(1 for change in source.changes for _ in change.added_lines)
    console.print(f"Changed files: {len(source.changes)} | Added lines: {added}")
    if source.skipped_reason is not None:
        reason = source.redactor.redact(source.skipped_reason)
        console.print(Text(f"Partial review. Whole-file checks skipped: {reason}"))
    else:
        console.print(
            f"Whole-file source available: {len(source.python_files)} Python file(s)."
        )

    if model_status:
        console.print(Text(model_status))
    if not findings:
        console.print("No findings.")
        return

    styles = {
        Severity.INFO: "cyan",
        Severity.WARNING: "yellow",
        Severity.BLOCKING: "bold red",
    }
    for finding in findings:
        location = (
            finding.file if finding.line is None else f"{finding.file}:{finding.line}"
        )
        header = Text(f"{location}  ")
        header.append(finding.severity.value.upper(), style=styles[finding.severity])
        header.append(f"  {finding.check}")
        console.print(header)
        console.print(Text(f"  {finding.message}"))
        if finding.suggestion:
            console.print(Text(f"  Suggestion: {finding.suggestion}"))
