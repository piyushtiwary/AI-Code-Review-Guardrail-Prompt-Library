from dataclasses import dataclass

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from guardrail import cli
from guardrail.review import Finding


@dataclass
class ControlledCheck:
    name: str
    items: list
    requires_whole_file: bool = False

    def run(self, source):
        assert {change.new_path for change in source.changes} == {"a.py", "z.py"}
        yield from self.items


def finding(severity="warning", **fields):
    return {
        "check": "sample",
        "file": "a.py",
        "line": 2,
        "severity": severity,
        "message": "Review this line.",
        **fields,
    }


@pytest.fixture
def review(tmp_path, monkeypatch):
    patch = tmp_path / "changes.patch"
    patch.write_text(
        "--- /dev/null\n+++ b/z.py\n@@ -0,0 +1 @@\n+z = 1\n"
        "--- a/a.py\n+++ b/a.py\n@@ -2 +2 @@\n-old = 1\n+new = 1\n"
        "@@ -10 +10 @@\n-old = 2\n+new = 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "80")

    def invoke(checks, *options):
        monkeypatch.setattr(cli, "DEFAULT_CHECKS", tuple(checks))
        return CliRunner().invoke(
            cli.app, ["review", "--diff", str(patch), "--patch-only", *options]
        )

    return invoke


@pytest.mark.parametrize(
    ("severities", "threshold", "expected"),
    [
        ([], "blocking", 0),
        (["info"], "warning", 0),
        (["warning"], None, 1),
        (["warning"], "blocking", 1),
        (["warning"], "warning", 2),
        (["warning", "blocking"], "blocking", 2),
        (["blocking"], "warning", 2),
    ],
)
def test_cli_exit_policy(review, severities, threshold, expected):
    options = ["--fail-on", threshold] if threshold else []
    result = review(
        [ControlledCheck("sample", [finding(s) for s in severities])], *options
    )
    assert result.exit_code == expected, result.output
    for severity in severities:
        assert severity.upper() in result.output
    if not severities:
        assert "No findings." in result.output


def test_cli_report_order(review, snapshot):
    a = ControlledCheck(
        "sample",
        [
            finding("info", file="z.py", line=1),
            finding("blocking", line=10),
            finding(
                message="Check [bold]literal markup[/bold].", suggestion="Use [value]."
            ),
        ],
    )
    b = ControlledCheck("another", [finding(check="another")])
    first = review([a, b])
    a.items.reverse()
    second = review([b, a])
    assert first.exit_code == second.exit_code == 2
    assert first.output == second.output
    assert first.output == snapshot


def test_patch_only_skips_whole_file_checks(review):
    result = review(
        [
            ControlledCheck("sample", [finding("blocking")], requires_whole_file=True),
            ControlledCheck("sample", [finding("info")]),
        ]
    )
    assert result.exit_code == 0
    assert "Whole-file checks skipped" in result.output
    assert "INFO" in result.output
    assert "BLOCKING" not in result.output


@pytest.mark.parametrize(
    "fields",
    [
        {"file": "/root/file.py"},
        {"file": "C:/file.py"},
        {"file": "../file.py"},
        {"file": "dir/../file.py"},
        {"file": ""},
        {"line": 0},
        {"line": True},
        {"severity": "unknown"},
        {"check": " "},
        {"message": ""},
        {"suggestion": " "},
    ],
)
def test_finding_rejects_invalid_fields(fields):
    with pytest.raises(ValidationError):
        Finding.model_validate(finding(**fields))


def test_invalid_check_output_is_an_operational_error(review):
    result = review([ControlledCheck("sample", [finding(), finding(line=-1)])])
    assert result.exit_code == 3
    assert "Review check error: sample" in result.stderr
    assert "line" in result.stderr
    assert result.stdout == ""


def test_check_execution_error_is_an_operational_error(review):
    class BrokenCheck:
        name = "broken"
        requires_whole_file = False

        def run(self, source):
            yield finding(check=self.name)
            raise RuntimeError("scanner unavailable")

    result = review([BrokenCheck()])
    assert result.exit_code == 3
    assert "Review check error: broken: scanner unavailable" in result.stderr
    assert result.stdout == ""
