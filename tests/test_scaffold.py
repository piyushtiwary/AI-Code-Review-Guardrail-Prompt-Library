import ast
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from guardrail.cli import app

REROUTE = Path(__file__).resolve().parents[1] / "stories" / "reroute.feature"


def test_reroute_scaffold(tmp_path, monkeypatch, snapshot):
    monkeypatch.chdir(tmp_path)
    Path("stories").mkdir()
    shutil.copy(REROUTE, "stories")
    result = CliRunner().invoke(
        app, ["storygen", "stories/reroute.feature", "--out", "tests/generated/"]
    )
    assert result.exit_code == 0, result.output
    target = Path("tests/generated/test_reroute.py")
    assert target.read_text(encoding="utf-8") == snapshot

    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    pytest_cmd = [sys.executable, "-m", "pytest", "-q"]
    collected = subprocess.run(
        [*pytest_cmd, "--collect-only"], capture_output=True, text=True, check=True
    )
    assert "2 tests collected" in collected.stdout
    run = subprocess.run(
        [*pytest_cmd, "--junitxml=results.xml"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 1, run.stdout + run.stderr
    failures = ET.parse("results.xml").findall(".//testcase/failure")
    assert [failure.get("message") for failure in failures] == [
        "Failed: TODO: implement this scenario"
    ] * 2


def test_colliding_names_and_edited_output(tmp_path):
    story = tmp_path / "route changes.feature"
    names = ["Late!", "Late?", "Late 2", "123", "!!!", "配送"]
    story.write_text(
        "Story: Routing\n"
        + "".join(
            f"Scenario: {name}\nGiven a parcel\nWhen it arrives\nThen notify\n"
            for name in names
        ),
        encoding="utf-8",
    )
    out = tmp_path / "generated tests"
    args = ["storygen", str(story), "--out", str(out)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    target = out / "test_route_changes.py"
    module = ast.parse(target.read_text(encoding="utf-8"))
    assert [node.name for node in module.body if isinstance(node, ast.FunctionDef)] == [
        "test_late",
        "test_late_2",
        "test_late_2_2",
        "test_123",
        "test_scenario",
        "test_scenario_2",
    ]

    edited = b"def test_delivered():\r\n    assert True\r\n"
    target.write_bytes(edited)
    rerun = CliRunner().invoke(app, args)
    assert rerun.exit_code == 3
    assert str(target) in rerun.stderr
    assert target.read_bytes() == edited
    assert list(out.iterdir()) == [target]


def test_invalid_story_reports_line_before_writing(tmp_path):
    story = tmp_path / "incomplete.feature"
    story.write_text("Story: Routing\nScenario: Late\nAnd notify", encoding="utf-8")
    out = tmp_path / "generated"
    result = CliRunner().invoke(app, ["storygen", str(story), "--out", str(out)])
    assert result.exit_code == 3
    assert "Line 3: And needs a preceding Given" in result.stderr
    assert not out.exists()
