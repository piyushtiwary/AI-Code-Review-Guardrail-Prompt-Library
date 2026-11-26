import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from guardrail import cli
from guardrail.prompts import load_prompts


def invoke(*flags):
    return CliRunner().invoke(cli.app, ["prompts", "eval", *flags])


def test_offline_replay_is_repeatable_without_codex(monkeypatch):
    monkeypatch.setattr(cli, "codex_review", lambda *_: pytest.fail("provider called"))
    first, second = invoke(), invoke("--offline")
    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
    records = json.loads(first.stdout)
    assert {r["example"] for r in records} == {
        "last-seat",
        "lookup",
        "booking-capacity",
    }
    for r in records:
        assert r["mode"] == "offline" and r["passed"] and r["reason"]
        assert r["version"] == "1.0.0"
        assert r["fixture"] == f"{r['prompt_id']}-{r['version']}-{r['example']}.yaml"
        assert "provider" not in r and "evaluated_on" not in r


@pytest.mark.parametrize(
    ("example", "original", "replacement", "reason"),
    [
        ("last-seat", '"line": 10', '"line": 9', "booking.py:10"),
        ("last-seat", "booking.py", "other.py", "booking.py:10"),
        ("last-seat", '"file":', '"path":', "invalid fields"),
        ("lookup", "def test_existing", "def !test_existing", "invalid Python syntax"),
        (
            "lookup",
            "test_unknown_booking",
            "test_existing_booking",
            "distinct top-level",
        ),
        (
            "lookup",
            "test_unknown_booking()",
            "test_unknown_booking(client)",
            "parameterless",
        ),
        ("lookup", "TODO: implement this scenario", "unfinished", "pytest.fail TODO"),
        ("lookup", "Expect status 404", "Expect status 200", "HTTP 404"),
        (
            "booking-capacity",
            "Scenario: Over capacity",
            "Scenario: Other",
            "Over capacity",
        ),
        ("booking-capacity", "Then the booking is declined", "", "missing a Then"),
    ],
)
def test_broken_replay_names_fixture_and_exits_nonzero(
    monkeypatch, example, original, replacement, reason
):
    entries = load_prompts()
    for entry in entries.values():
        if example in entry.fixtures:
            fixture = entry.fixtures[example]
            entry.fixtures[example] = fixture.model_copy(
                update={
                    "reference_output": fixture.reference_output.replace(
                        original, replacement
                    )
                }
            )
    monkeypatch.setattr(cli, "load_prompts", lambda: entries)
    result = invoke()
    assert result.exit_code == 1, result.output
    records = json.loads(result.stdout)
    (failed,) = [r for r in records if not r["passed"]]
    assert len(records) == 3
    assert failed["example"] == example and reason in failed["reason"]


def test_live_evaluates_provider_responses_and_records_provenance(monkeypatch):
    outputs = {
        entry.prompt.render(fixture.inputs): fixture.reference_output
        for entry in load_prompts().values()
        for fixture in entry.fixtures.values()
    }
    calls = []

    async def provider(instructions, rendered):
        calls.append(rendered)
        return outputs[rendered] if len(calls) != 1 else "[]"

    monkeypatch.setattr(cli, "codex_review", provider)
    result = invoke("--live")
    assert result.exit_code == 1, result.output
    assert calls == list(outputs)
    records = json.loads(result.stdout)
    assert [r["passed"] for r in records] == [False, True, True]
    for r in records:
        assert (r["mode"], r["provider"], r["model"]) == (
            "live",
            "codex-cli",
            "gpt-6-sol",
        )
        assert r["evaluated_on"] == datetime.now(UTC).date().isoformat()


def test_live_provider_error_is_recorded_and_remaining_examples_run(monkeypatch):
    responses = iter([TimeoutError(), RuntimeError("sensitive request data"), ""])

    async def provider(*_):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(cli, "codex_review", provider)
    result = invoke("--live")
    assert result.exit_code == 3
    records = json.loads(result.stdout)
    assert [r["passed"] for r in records] == [False] * 3
    assert "timed out" in records[0]["reason"]
    assert "sensitive" not in result.output


def test_live_requires_configuration(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CODEX_BIN", "missing")
    result = invoke("--live")
    assert result.exit_code == 3
    assert "GUARDRAIL_CODEX_BIN" in result.output
