"""Common contract for review checks and completed-review exit statuses."""

import warnings
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Annotated, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from guardrail.exceptions import ExceptionHandlingCheck
from guardrail.imports import ImportCheck
from guardrail.secrets import SecretCheck, SecretRedactor
from guardrail.source import ReviewSource
from guardrail.testing import FunctionTestReferenceCheck, NoTestChangesCheck


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
    line: Annotated[int, Field(strict=True, gt=0)] | None
    severity: Severity
    message: NonBlank
    suggestion: NonBlank | None = None

    @field_validator("line")
    @classmethod
    def require_line_anchor(cls, value: int | None, info: ValidationInfo) -> int | None:
        if value is None and info.data.get("check") != NoTestChangesCheck.name:
            raise ValueError("expected a new-file line for this check")
        return value

    def redacted(self, redactor: SecretRedactor) -> "Finding":
        return self.model_copy(
            update={
                "file": redactor.redact(self.file),
                "check": redactor.redact(self.check),
                "message": redactor.redact(self.message),
                "suggestion": redactor.redact(self.suggestion)
                if self.suggestion
                else None,
            }
        )

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


DEFAULT_CHECKS: tuple[Check, ...] = (
    ImportCheck(),
    ExceptionHandlingCheck(),
    SecretCheck(),
    FunctionTestReferenceCheck(),
    NoTestChangesCheck(),
)


class CheckError(Exception):
    """A check failed to run or emitted an invalid finding."""


def run_checks(source: ReviewSource, checks: Iterable[Check]) -> list[Finding]:
    findings = []
    for check in checks:
        if check.requires_whole_file and source.skipped_reason is not None:
            continue
        try:
            # Python's parser can print a raw source line with a syntax warning.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                for item in check.run(source):
                    finding = Finding.model_validate(item)
                    if finding.check != check.name:
                        raise ValueError(f"expected check name {check.name!r}")
                    findings.append(finding.redacted(source.redactor))
        except Exception as exc:  # noqa: BLE001 - check failures may contain raw source
            raise CheckError(source.redactor.redact(f"{check.name}: {exc}")) from None
    return sorted_findings(findings)


def sorted_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            f.file,
            f.line or 0,
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
