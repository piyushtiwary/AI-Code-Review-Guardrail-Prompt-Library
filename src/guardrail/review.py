"""Common contract for review checks and completed-review exit statuses."""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from guardrail.source import ReviewSource


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class FailOn(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Finding(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    check: NonBlank
    file: str
    line: Annotated[int, Field(strict=True, gt=0)]
    severity: Severity
    message: NonBlank
    suggestion: NonBlank | None = None

    @field_validator("file")
    @classmethod
    def repository_relative(cls, value: str) -> str:
        """Use Git's slash-separated paths and preserve literal filename spaces."""
        if (
            not value.strip()
            or PureWindowsPath(value).anchor
            or "\\" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("expected a repository-relative file path")
        return value


class Check(Protocol):
    name: str
    requires_whole_file: bool

    def run(self, source: ReviewSource) -> Iterable[Finding | Mapping[str, object]]: ...


DEFAULT_CHECKS: tuple[Check, ...] = ()


class CheckError(Exception):
    """A check failed to run or emitted an invalid finding."""


def run_checks(source: ReviewSource, checks: Iterable[Check]) -> list[Finding]:
    findings = []
    for check in checks:
        if check.requires_whole_file and source.skipped_reason is not None:
            continue
        try:
            for item in check.run(source):
                finding = Finding.model_validate(item)
                if finding.check != check.name:
                    raise ValueError(f"expected check name {check.name!r}")
                findings.append(finding)
        except Exception as exc:
            raise CheckError(f"{check.name}: {exc}") from exc
    return sorted(
        findings,
        key=lambda f: (
            f.file,
            f.line,
            f.check,
            f.severity,
            f.message,
            f.suggestion or "",
        ),
    )


def review_status(findings: Iterable[Finding], fail_on: FailOn) -> int:
    status = 0
    for finding in findings:
        if finding.severity == Severity.BLOCKING:
            return 2
        if finding.severity == Severity.WARNING:
            if fail_on == FailOn.WARNING:
                return 2
            status = 1
    return status
