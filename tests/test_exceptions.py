import subprocess
from difflib import unified_diff

import pytest
from typer.testing import CliRunner

from guardrail import cli
from guardrail.patch import parse_patch
from guardrail.review import DEFAULT_CHECKS, run_checks
from guardrail.source import ReviewSource


def source_for(before, after):
    path = "handlers.py"
    diff = "".join(
        unified_diff(
            before.splitlines(True), after.splitlines(True), "a/" + path, "b/" + path
        )
    )
    return ReviewSource(parse_patch(diff), {path: after.encode()})


def test_default_review_distinguishes_swallowed_and_handled_errors():
    source = source_for(
        "",
        """\
import logging

def read(path):
    try:
        return load(path)
    except FileNotFoundError:
        return None
    except Exception:
        logging.exception("Load failed")
        raise

def batch(paths):
    for path in paths:
        try:
            load(path)
        except Exception:
            continue

def lookup():
    try:
        return fetch()
    except (
        ValueError,
        Exception,
    ):
        return None

try:
    work()
except:
    raise
""",
    )
    findings = [
        f for f in run_checks(source, DEFAULT_CHECKS) if f.check == "exception-handling"
    ]
    assert [(f.check, f.file, f.line, f.severity) for f in findings] == [
        ("exception-handling", "handlers.py", line, "warning") for line in (16, 22, 30)
    ]


def test_saved_patch_merges_scanner_hits_at_the_new_body_line(tmp_path):
    path = tmp_path / "handlers.py"
    before = "try:\n    work()\nexcept Exception:\n    raise\n"
    after = before.replace("    raise", "    pass")
    path.write_bytes(after.encode())
    patch = tmp_path / "change.patch"
    patch.write_bytes(
        "".join(
            unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                "a/handlers.py",
                "b/handlers.py",
            )
        ).encode()
    )
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    result = CliRunner().invoke(
        cli.app, ["review", "--diff", str(patch), "--source-root", str(tmp_path)]
    )
    assert result.exit_code == 1, result.output
    assert result.output.count("WARNING  exception-handling") == 1
    assert "handlers.py:4  WARNING  exception-handling" in result.output
    assert "Broad exception handler silently passes." in result.output


def test_preexisting_handlers_ignore_try_body_comments_and_nested_handlers():
    before = """\
try:
    original()
except Exception:
    try:
        recover()
    except LookupError:
        pass
"""
    after = (
        before.replace("original()", "replacement()")
        .replace("    try:", "    # Explain the fallback.\n    try:")
        .replace("except LookupError:", "except KeyError:")
    )
    assert "exception-handling" not in {
        f.check for f in run_checks(source_for(before, after), DEFAULT_CHECKS)
    }


@pytest.mark.parametrize(
    ("failed_tool", "status", "output"),
    [
        ("ruff", 2, b""),
        ("ruff", 0, b"invalid JSON"),
        ("bandit", 0, b'{"errors":[{"reason":"skipped"}],"results":[]}'),
    ],
)
def test_scanner_failure_is_a_cli_operational_error(
    monkeypatch, failed_tool, status, output
):
    source = source_for("", "try:\n    work()\nexcept Exception:\n    pass\n")
    monkeypatch.setattr(cli, "revision_source", lambda *args: source)

    def run(args, **kwargs):
        if failed_tool in args:
            return subprocess.CompletedProcess(args, status, output, b"")
        return subprocess.CompletedProcess(args, 0, b"[]", b"")

    monkeypatch.setattr(subprocess, "run", run)
    result = CliRunner().invoke(cli.app, ["review", "--base", "base", "--head", "head"])
    assert result.exit_code == 3
    assert "Review check error: exception-handling" in result.stderr
    assert failed_tool in result.stderr
    assert "handlers.py" in result.stderr
    assert "No findings" not in result.output
