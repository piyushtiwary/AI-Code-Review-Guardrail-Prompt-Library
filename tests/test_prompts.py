import ast
import json
from importlib.resources import files

import pytest
import yaml
from typer.testing import CliRunner

from guardrail.cli import app
from guardrail.prompts import PromptError, load_prompts
from guardrail.review import Finding
from guardrail.story import parse_story

REVIEW = "diff-edge-case-review"
SCAFFOLD = "endpoint-pytest-scaffold"
CRITERIA = "write-acceptance-criteria"


@pytest.mark.parametrize(
    ("prompt_id", "example"),
    [(REVIEW, "last-seat"), (SCAFFOLD, "lookup"), (CRITERIA, "booking-capacity")],
)
def test_render_packaged_example(prompt_id, example):
    entry = load_prompts()[prompt_id, "1.0.0"]
    fixture = entry.fixtures[example]
    result = CliRunner().invoke(app, ["prompts", "render", prompt_id, "1.0.0", example])
    assert result.exit_code == 0, result.output
    assert result.stdout == entry.prompt.render(fixture.inputs)
    assert all(value in result.stdout for value in fixture.inputs.values())
    assert fixture.reference_output not in result.stdout


def test_reference_outputs_match_the_examples():
    library = load_prompts()
    review = library[REVIEW, "1.0.0"].fixtures["last-seat"]
    (finding,) = map(Finding.model_validate, json.loads(review.reference_output))
    assert (finding.check, finding.file, finding.line) == (REVIEW, "booking.py", 10)

    scaffold = library[SCAFFOLD, "1.0.0"].fixtures["lookup"]
    cases = [
        n
        for n in ast.parse(scaffold.reference_output).body
        if isinstance(n, ast.FunctionDef)
    ]
    assert [case.name for case in cases] == [
        "test_existing_booking",
        "test_unknown_booking",
    ]
    assert all(
        ast.unparse(case.body[-1]) == "pytest.fail('TODO: implement this scenario')"
        for case in cases
    )

    criteria = library[CRITERIA, "1.0.0"].fixtures["booking-capacity"]
    story = parse_story(criteria.reference_output)
    assert story.title == criteria.inputs["title"]
    assert [s.name for s in story.scenarios] == ["Exact capacity", "Over capacity"]


@pytest.fixture
def catalog(tmp_path):
    packaged = files("guardrail").joinpath("prompt_library")
    entry = yaml.safe_load(packaged.joinpath(f"{REVIEW}-1.0.0.yaml").read_text("utf-8"))
    filename = entry["examples"]["last-seat"]
    (tmp_path / "fixtures").mkdir()
    fixture = tmp_path / "fixtures" / filename
    fixture.write_bytes(packaged.joinpath("fixtures", filename).read_bytes())
    (tmp_path / "original.yaml").write_text(yaml.safe_dump(entry), encoding="utf-8")
    return tmp_path, entry, fixture


def test_versions_keep_example_results_separate(catalog):
    root, revised, _ = catalog
    previous_history = load_prompts(root)[REVIEW, "1.0.0"].prompt.evaluation_history
    revised["template"] += "Be concise.\n"
    (root / "revision.yaml").write_text(yaml.safe_dump(revised), encoding="utf-8")
    with pytest.raises(PromptError, match="duplicate prompt"):
        load_prompts(root)

    revised["version"] = "1.1.0"
    revised["evaluation_history"] = [
        {
            "example": "last-seat",
            "mode": "offline",
            "passed": True,
            "reason": "Matched booking.py line 10",
        }
    ]
    (root / "revision.yaml").write_text(yaml.safe_dump(revised), encoding="utf-8")
    library = load_prompts(root)
    original = library[REVIEW, "1.0.0"].prompt
    revision = library[REVIEW, "1.1.0"].prompt
    assert original.evaluation_history == previous_history
    assert revision.evaluation_history != previous_history
    assert revision.evaluation_history[0].example == "last-seat"
    inputs = {"hunks": "{{ literal input }}"}
    assert revision.render(inputs) == original.render(inputs) + "Be concise.\n"
    assert inputs["hunks"] in revision.render(inputs)


def test_missing_fixture_and_missing_fixture_input(catalog):
    root, _, fixture = catalog
    fixture.unlink()
    with pytest.raises(PromptError, match=fixture.name):
        load_prompts(root)
    fixture.write_text("inputs: {}\nreference_output: '[]'\n", encoding="utf-8")
    with pytest.raises(PromptError, match="missing template inputs: hunks"):
        load_prompts(root)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("template", "{{ unknown }}", "template and declared inputs differ"),
        ("version", "1.01.0", "version"),
        ("purpose", " ", "purpose"),
        ("examples", {"escape": "../elsewhere.yaml"}, "examples"),
        ("known_failure_modes", [], "known_failure_modes"),
    ],
)
def test_invalid_definition(catalog, field, value, error):
    root, entry, _ = catalog
    entry[field] = value
    (root / "original.yaml").write_text(yaml.safe_dump(entry), encoding="utf-8")
    with pytest.raises(PromptError, match=error):
        load_prompts(root)


def test_render_requires_all_inputs():
    entry = load_prompts()[CRITERIA, "1.0.0"]
    with pytest.raises(PromptError, match="missing template inputs: requirements"):
        entry.prompt.render({"title": "Book a seat"})
