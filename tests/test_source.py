import subprocess

import pytest
from typer.testing import CliRunner

from guardrail.cli import app
from guardrail.source import patch_source, revision_source


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "example checkout"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, check=True
        ).stdout

    git("init", "-b", "base")
    git("config", "user.email", "review@example.invalid")
    git("config", "user.name", "Fixture")
    git("config", "core.autocrlf", "false")
    (repo / "module.py").write_bytes(b"# original\n" + b"\n" * 8 + b"value = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_module.py").write_bytes(b"def test_existing(): pass\n")
    git("add", ".")
    git("commit", "-m", "Common ancestor")
    git("branch", "feature")
    (repo / "base_only.py").write_bytes(b"base = True\n")
    git("add", ".")
    git("commit", "-m", "Advance base independently")
    git("switch", "feature")
    module = repo / "module.py"
    module.write_bytes(module.read_bytes().replace(b"value = 1", b"value = 2"))
    (repo / "new module.py").write_bytes(b"enabled = True")
    git("add", ".")
    git("commit", "-m", "Change module and add another")
    patch = tmp_path / "changes.patch"
    patch.write_bytes(git("diff", "base...feature"))
    return repo, patch


def test_pr_and_exported_patch_resolve_same_changes_and_complete_source(history):
    repo, patch = history
    revision = revision_source(repo / "tests", "base", "feature")
    saved = patch_source(patch, repo)

    def added(source):
        return [
            (
                c.old_path,
                c.new_path,
                [(line.target_line_no, line.value) for line in c.added_lines],
            )
            for c in source.changes
        ]

    assert (
        added(revision)
        == added(saved)
        == [
            ("module.py", "module.py", [(10, "value = 2\n")]),
            (None, "new module.py", [(1, "enabled = True\n")]),
        ]
    )
    assert revision.skipped_reason is saved.skipped_reason is None
    assert (
        revision.python_files
        == saved.python_files
        == {
            path: (repo / path).read_bytes()
            for path in ["module.py", "new module.py", "tests/test_module.py"]
        }
    )

    # The edit is outside every hunk. Only comparing the added line would miss it.
    module = repo / "module.py"
    module.write_bytes(module.read_bytes().replace(b"original", b"uncommitted"))
    (repo / "tests/test_module.py").unlink()
    assert (
        revision_source(repo, "base", "feature").python_files == revision.python_files
    )
    partial = patch_source(patch, repo)
    assert added(partial) == added(saved)
    assert partial.python_files == {}
    assert "module.py" in partial.skipped_reason


def test_cli_reports_source_availability(history, tmp_path, monkeypatch):
    repo, patch = history
    monkeypatch.chdir(repo)
    runner = CliRunner()
    for args in (["--base", "base", "--head", "feature"], ["--diff", str(patch)]):
        result = runner.invoke(app, ["review", *args])
        assert result.exit_code == 0, result.output
        assert "Changed files: 2 | Added lines: 2" in result.output
        assert "3 Python file(s)" in result.output

    monkeypatch.chdir(tmp_path)
    for args in ([], ["--patch-only"]):
        result = runner.invoke(app, ["review", "--diff", str(patch), *args])
        assert result.exit_code == 0
        assert "Whole-file checks skipped" in result.output
    explicit = runner.invoke(
        app, ["review", "--diff", str(patch), "--source-root", str(repo)]
    )
    assert explicit.exit_code == 0
    assert "3 Python file(s)" in explicit.output

    monkeypatch.chdir(repo)
    invalid = runner.invoke(app, ["review", "--base", "bad-ref", "--head", "feature"])
    assert invalid.exit_code == 3
    assert "Review input error" in invalid.output


def test_plain_patch_checks_context_and_exact_coordinates(history):
    repo, patch = history
    patch.write_bytes(
        b"--- a/module.py\n+++ b/module.py\n@@ -9,2 +9,2 @@\n \n-value = 1\n+value = 2\n"
    )
    assert patch_source(patch, repo).skipped_reason is None
    module = repo / "module.py"
    module.write_bytes(b"\n" + module.read_bytes())
    assert "module.py" in patch_source(patch, repo).skipped_reason


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--head", "HEAD"],
        ["--base", "HEAD"],
        ["--diff", "p", "--base", "main", "--head", "HEAD"],
        ["--diff", "p", "--patch-only", "--source-root", "."],
    ],
)
def test_review_requires_one_source_mode(args):
    assert CliRunner().invoke(app, ["review", *args]).exit_code == 2
