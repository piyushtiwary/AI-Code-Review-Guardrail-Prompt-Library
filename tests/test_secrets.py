import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guardrail import cli
from guardrail.review import DEFAULT_CHECKS, run_checks
from guardrail.secrets import SecretRedactor
from guardrail.source import patch_source

# Construct synthetic values so the test source itself contains no credential.
GITHUB = "ghp_" + "Ab3" * 12
PASSWORD = "m5!H7/" + "x9#Fs2+R8yd4"
REMOVED = "vL3#bQ" + "5Ew!8MyF1x"
CONTEXT = "ASIA" + "B7" * 8
UUID = "550e8400-e29b-41d4-a716-446655440000"
CHECKSUM = "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592"


@pytest.fixture
def patch(tmp_path: Path):
    path = tmp_path / "settings.patch"
    path.write_text(
        "--- a/settings.env\n+++ b/settings.env\n"
        "@@ -2,2 +2,3 @@\n"
        f" # unchanged {CONTEXT}\n"
        f"-PASSWORD={REMOVED}\n"
        f'+PASSWORD="{PASSWORD}"\n'
        f"+REQUEST_ID={UUID}\n"
        f"@@ -20 +21,4 @@ previous {REMOVED}\n"
        " # second hunk\n"
        f"+# bearer {GITHUB} and {GITHUB}\n"
        f"+SHA256={CHECKSUM}\n"
        f"+# previously configured as {PASSWORD}\n",
        encoding="utf-8",
    )
    return path


def test_check_reports_only_added_secret_lines_and_prepares_redacted_model_hunks(patch):
    source = patch_source(patch, None)
    findings = run_checks(source, DEFAULT_CHECKS)
    assert [(f.check, f.file, f.line, f.severity) for f in findings] == [
        ("secrets", "settings.env", 3, "blocking"),
        ("secrets", "settings.env", 22, "blocking"),
    ]
    (change,) = source.changes
    excerpts = [source.prepare_model_hunk(change.new_path, h) for h in change.hunks]
    assert excerpts == [
        (
            "settings.env\n@@ -2,2 +2,3 @@\n # unchanged [REDACTED]\n"
            f'-PASSWORD=[REDACTED]\n+PASSWORD="[REDACTED]"\n+REQUEST_ID={UUID}\n'
        ),
        (
            "settings.env\n@@ -20,1 +21,4 @@ previous [REDACTED]\n # second hunk\n"
            f"+# bearer [REDACTED] and [REDACTED]\n+SHA256={CHECKSUM}\n"
            "+# previously configured as [REDACTED]\n"
        ),
    ]
    result = CliRunner().invoke(
        cli.app, ["review", "--diff", str(patch), "--patch-only"]
    )
    assert result.exit_code == 2
    assert result.stdout.count("BLOCKING  secrets") == 2
    for value in (GITHUB, PASSWORD, REMOVED, CONTEXT):
        assert value not in result.stdout + result.stderr + repr(findings)


@pytest.mark.parametrize(
    "line",
    [
        f'api_key = "{UUID}"',
        f'checksum = "{CHECKSUM}"',
        'password = "aaaaaaaaaaaaaaaaaaaaaaaa"',
        'token = os.environ["PRODUCTION_SERVICE_TOKEN"]',
        'client_secret = "${PRODUCTION_SERVICE_SECRET}"',
    ],
)
def test_benign_patch_is_clean_and_preserved(tmp_path, line):
    patch = tmp_path / "control.patch"
    patch.write_text(f"--- /dev/null\n+++ b/config\n@@ -0,0 +1 @@\n+{line}\n")
    source = patch_source(patch, None)
    assert run_checks(source, DEFAULT_CHECKS) == []
    assert source.redactor.redact(line) == line


def test_revision_review_redacts_import_diagnostic_and_ignores_python_reference(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Tester",
                "-c",
                "user.email=test@example.org",
                *args,
            ],
            check=True,
            capture_output=True,
        )

    git("init")
    git("commit", "--allow-empty", "-m", "base")
    (tmp_path / "app.py").write_text(
        f"import {GITHUB}\n"
        "password = settings.PRODUCTION_DATABASE_PASSWORD\n"
        f'client_secret = "{PASSWORD}"\n'
    )
    git("add", "app.py")
    git("commit", "-m", "head")
    result = CliRunner().invoke(
        cli.app, ["review", "--base", "HEAD^", "--head", "HEAD"]
    )
    assert result.exit_code == 2
    assert "app.py:1  WARNING  imports" in result.stdout
    assert "Cannot resolve module '[REDACTED]'." in result.stdout
    assert "app.py:1  BLOCKING  secrets" in result.stdout
    assert "app.py:3  BLOCKING  secrets" in result.stdout
    assert "app.py:2" not in result.stdout
    assert PASSWORD not in result.output
    assert GITHUB not in result.output


@pytest.mark.parametrize(
    "line, value",
    [
        (f'{{"clientSecret": "{PASSWORD}"}}', PASSWORD),
        (f"api_key: str = '{PASSWORD}'", PASSWORD),
        (f"SERVICE_AUTH_TOKEN={PASSWORD}", PASSWORD),
        (f'password="{GITHUB}!extra"', f"{GITHUB}!extra"),
        ("header: xoxb-" + "42-ab" * 5, "xoxb-" + "42-ab" * 5),
        ("value=sk_live_" + "X5" * 12, "sk_live_" + "X5" * 12),
    ],
)
def test_redactor_masks_complete_value_and_preserves_surrounding_text(line, value):
    assert SecretRedactor().redact(line) == line.replace(value, "[REDACTED]")


def test_other_check_output_and_errors_cannot_echo_a_known_secret(monkeypatch, patch):
    class EchoCheck:
        name = "echo"
        requires_whole_file = False
        fail = False

        def run(self, source):
            if self.fail:
                raise RuntimeError(f"failed on {PASSWORD}")
            yield {
                "check": self.name,
                "file": "settings.env",
                "line": 3,
                "severity": "info",
                "message": f"Found {PASSWORD}",
                "suggestion": f"Remove {GITHUB}",
            }

    check = EchoCheck()
    monkeypatch.setattr(cli, "DEFAULT_CHECKS", (check,))
    args = ["review", "--diff", str(patch), "--patch-only"]
    report = CliRunner().invoke(cli.app, args)
    assert report.exit_code == 0
    assert "Found [REDACTED]" in report.stdout
    assert "Remove [REDACTED]" in report.stdout
    check.fail = True
    error = CliRunner().invoke(cli.app, args)
    assert error.exit_code == 3
    assert "failed on [REDACTED]" in error.stderr
    assert PASSWORD not in report.output + error.output


def test_malformed_patch_diagnostic_omits_rejected_source(tmp_path):
    patch = tmp_path / "broken.patch"
    patch.write_text(
        f"--- a/f\n+++ b/f\n@@ -1 +1 @@\n-PASSWORD={PASSWORD}\n{PASSWORD}\n"
    )
    result = CliRunner().invoke(
        cli.app, ["review", "--diff", str(patch), "--patch-only"]
    )
    assert result.exit_code == 3
    assert "Malformed patch" in result.stderr
    assert PASSWORD not in result.output
