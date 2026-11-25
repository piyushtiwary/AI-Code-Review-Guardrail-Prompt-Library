import subprocess
from difflib import unified_diff

import pytest
from typer.testing import CliRunner

from guardrail.cli import app
from guardrail.patch import parse_patch
from guardrail.review import DEFAULT_CHECKS, run_checks
from guardrail.source import ReviewSource


def diff(path, before, after):
    return "".join(
        unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            f"a/{path}" if before else "/dev/null",
            f"b/{path}" if after else "/dev/null",
        )
    )


def locations(patch, files=None):
    source = ReviewSource(
        parse_patch(patch), files or {}, "patch only" if files is None else None
    )
    return [(f.check, f.file, f.line) for f in run_checks(source, DEFAULT_CHECKS)]


@pytest.mark.parametrize("edit_test", [False, True])
def test_existing_references_and_unrelated_test_edit_are_independent(edit_test):
    code = (
        "def deliver(): pass\n"
        "class Parcel:\n"
        "    async def track(self): pass\n"
        "    def cancel(self): pass\n"
    )
    tests = (
        "from shipping import deliver as send\n"
        "def test_route(parcel):\n"
        "    send()\n"
        "    parcel.track()\n"
        "    # cancel()\n"
        "    assert 'cancel' != 'cancelled'\n"
    )
    patch = diff("shipping.py", "", code)
    if edit_test:
        patch += diff("tests/feature_test.py", tests, tests + "    assert True\n")
        tests += "    assert True\n"
    expected = [("test-reference", "shipping.py", 4)]
    if not edit_test:
        expected.insert(0, ("no-test-changes", "shipping.py", 1))
    assert (
        locations(
            patch,
            {"shipping.py": code.encode(), "tests/feature_test.py": tests.encode()},
        )
        == expected
    )


def test_existing_function_with_a_signature_change_and_new_method_of_same_name():
    before = "class Existing:\n    def run(self): return 1\n"
    after = before.replace("run(self)", "run(self, enabled=True)") + (
        "class New:\n    def run(self): return 2\n"
    )
    assert locations(diff("tasks.py", before, after), {"tasks.py": after.encode()}) == [
        ("no-test-changes", "tasks.py", 2),
        ("test-reference", "tasks.py", 4),
    ]


@pytest.mark.parametrize("with_source", [False, True])
def test_docs_comments_and_formatting_stay_quiet_and_anchor_skips_them(with_source):
    before = '"""Overview.\nOld details.\n"""\nvalue = "#1"  # old\n'
    after = before.replace("Old details.", "New details.").replace(
        ' = "#1"  # old', '="#1"  # new'
    )
    docs = diff("README.md", "Old.\n", "New.\n")
    for code, expected in [
        (after, []),
        (after + "deliver()\n", [("no-test-changes", "app.py", 5)]),
    ]:
        patch = docs + diff("app.py", before, code)
        assert (
            locations(patch, {"app.py": code.encode()} if with_source else None)
            == expected
        )


def test_docstring_edit_with_delimiters_outside_the_hunk_uses_complete_source():
    before = '"""Overview\n' + "a line\n" * 10 + "old\n" + "more\n" * 10 + '"""\n'
    after = before.replace("old", "different")
    assert locations(diff("app.py", before, after), {"app.py": after.encode()}) == []


@pytest.mark.parametrize(
    ("before", "after", "line"),
    [
        ("# prior\n", "# updated\n# additional detail\n", None),
        ('def f(x: int): "old"\n', 'def f(x: int): "new"\n', None),
        ('sql = """select\n# old\n"""\n', 'sql = """select\n# new\n"""\n', 2),
    ],
)
def test_distinguishes_documentation_from_executable_string_values(before, after, line):
    expected = [] if line is None else [("no-test-changes", "app.py", line)]
    patch = diff("app.py", before, after)
    assert locations(patch) == expected
    assert locations(patch, {"app.py": after.encode()}) == expected


def test_deletion_uses_file_location_and_later_added_logic_takes_precedence(tmp_path):
    removed = diff("removed.py", "old()\n", "")
    assert locations(removed) == [("no-test-changes", "removed.py", None)]
    assert locations(removed + diff("app.py", "", "# header\nrun()\n")) == [
        ("no-test-changes", "app.py", 2)
    ]
    assert locations(diff("app.py", "# removed comment\n", "")) == []
    assert (
        locations(removed + diff("tests/test_old.py", "def test_old(): pass\n", ""))
        == []
    )
    patch = tmp_path / "delete.patch"
    patch.write_text(removed)
    result = CliRunner().invoke(app, ["review", "--patch-only", "--diff", str(patch)])
    assert result.exit_code == 1
    assert "removed.py  WARNING  no-test-changes" in result.stdout


def test_cli_uses_committed_tests_and_marks_patch_only_review_partial(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Tester", "-c", "user.email=t@example.org", *args],
            check=True,
            capture_output=True,
        ).stdout

    git("init")
    (tmp_path / "test_app.py").write_text("def test_existing(): assert app.run()\n")
    git("add", ".")
    git("commit", "-m", "existing reference")
    (tmp_path / "app.py").write_text(
        "def run(): return True\ndef stop(): return False\n"
    )
    git("add", "app.py")
    git("commit", "-m", "add functions")
    patch = tmp_path / "app.patch"
    patch.write_bytes(git("diff", "HEAD^", "HEAD"))
    runner = CliRunner()
    saved = runner.invoke(app, ["review", "--diff", str(patch)])
    (tmp_path / "test_app.py").write_text("def test_existing(): assert app.stop()\n")
    revision = runner.invoke(app, ["review", "--base", "HEAD^", "--head", "HEAD"])
    assert saved.exit_code == revision.exit_code == 1
    assert saved.output == revision.output
    assert "app.py:1  WARNING  no-test-changes" in revision.output
    assert "app.py:2  WARNING  test-reference" in revision.output
    assert revision.output.count("WARNING  test-reference") == 1
    assert "Reference heuristic" in revision.output
    partial = runner.invoke(
        app, ["review", "--diff", str(patch), "--patch-only", "--fail-on", "warning"]
    )
    assert partial.exit_code == 2
    assert "Whole-file checks skipped" in partial.output
    assert "WARNING  no-test-changes" in partial.output
    assert "WARNING  test-reference" not in partial.output
