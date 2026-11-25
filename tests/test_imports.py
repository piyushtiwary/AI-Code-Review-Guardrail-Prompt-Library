import subprocess
from difflib import unified_diff

import pytest
from typer.testing import CliRunner

from guardrail.cli import app
from guardrail.patch import parse_patch
from guardrail.review import DEFAULT_CHECKS, run_checks
from guardrail.source import ReviewSource


def findings(before, after, files, path="src/example/use.py"):
    diff = unified_diff(
        before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"
    )
    source = ReviewSource(parse_patch("".join(diff)), {**files, path: after.encode()})
    return [
        (f.line, f.message)
        for f in run_checks(source, DEFAULT_CHECKS)
        if f.check == "imports"
    ]


def test_changed_aliases_use_added_lines_and_original_names():
    before = (
        "import preexisting_bad_import\n"
        "from .helpers import (\n"
        "    preexisting_bad_name,\n"
        ")\n"
    )
    after = (
        before.replace(")\n", "    real as renamed,\n    missing as other,\n)\n")
        + "import json as js, guardrail_nonexistent as absent\n"
    )
    assert findings(before, after, {"src/example/helpers.py": b"real = 1\n"}) == [
        (5, "Local module 'example.helpers' has no name 'missing'."),
        (7, "Cannot resolve module 'guardrail_nonexistent'."),
    ]


def test_relative_imports_from_package_init_and_installed_dependencies():
    imports = (
        "from .. import helpers as h\n"
        "from ..helpers import value\n"
        "from . import sibling\n"
        "import unidiff as ud\n"
        "from pathlib import Path as P\n"
        "from ... import outside\n"
    )
    assert findings(
        "",
        imports,
        {
            "example/__init__.py": b"",
            "example/helpers.py": b"value = 1",
            "example/sub/sibling.py": b"",
        },
        "example/sub/__init__.py",
    ) == [(6, "Relative import goes beyond the top-level package.")]


def test_from_header_change_checks_retained_aliases_at_an_added_line():
    before = "from .old import (ok,\n    missing,\n)\n"
    after = before.replace(".old", ".new")
    assert findings(before, after, {"src/example/new.py": b"ok = 1"}) == [
        (1, "Local module 'example.new' has no name 'missing'.")
    ]


def test_new_multiline_alias_and_absent_namespace_child():
    after = "from .helpers import (\n    missing,\n)\nfrom . import absent\n"
    assert findings("", after, {"src/example/helpers.py": b"ok = True"}) == [
        (2, "Local module 'example.helpers' has no name 'missing'."),
        (4, "Local module 'example' has no name 'absent'."),
    ]


@pytest.mark.parametrize(
    "body",
    [
        "def __getattr__(name): return name",
        "from .another import *",
        "globals()['generated'] = 42",
        "if enabled: generated = 1",
    ],
)
def test_uncertain_exports_do_not_raise_missing_name_findings(body):
    assert (
        findings(
            "",
            "from .helpers import generated\n",
            {
                "src/example/helpers.py": body.encode(),
            },
        )
        == []
    )


def test_default_cli_checks_head_and_matching_checkout_without_importing(
    tmp_path, monkeypatch
):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, check=True
        ).stdout

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    git("init")
    git("config", "user.email", "review@example.invalid")
    git("config", "user.name", "Fixture")
    (tmp_path / "side_effect.py").write_bytes(
        b"from pathlib import Path\nPath('executed').touch()\nvalue = 1\n"
    )
    (tmp_path / "simple.py").write_bytes(b"value = 1\n")
    git("add", ".")
    git("commit", "-m", "Local modules")
    (tmp_path / "use.py").write_bytes(
        b"from side_effect import value\nfrom simple import missing\n"
    )
    git("add", "use.py")
    git("commit", "-m", "Import local names")
    patch = tmp_path / "change.patch"
    patch.write_bytes(git("diff", "HEAD~1", "HEAD"))

    runner = CliRunner()
    saved = runner.invoke(app, ["review", "--diff", str(patch)])
    (tmp_path / "simple.py").write_bytes(b"missing = True\n")
    revision = runner.invoke(app, ["review", "--base", "HEAD~1", "--head", "HEAD"])
    assert revision.exit_code == saved.exit_code == 1
    assert revision.output == saved.output
    assert "use.py:2  WARNING  imports" in saved.output
    assert "Local module 'simple' has no name 'missing'." in saved.output
    assert not (tmp_path / "executed").exists()
