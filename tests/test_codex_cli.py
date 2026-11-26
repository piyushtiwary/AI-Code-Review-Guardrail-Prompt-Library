import asyncio
import sys
from pathlib import Path

import pytest

from guardrail import codex_cli


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    spawn = asyncio.create_subprocess_exec
    script = tmp_path / "fake executable.py"
    children = []
    monkeypatch.setenv("GUARDRAIL_CODEX_BIN", sys.executable)
    monkeypatch.setenv("GITHUB_TOKEN", "private-fixture")
    monkeypatch.setenv("GH_TOKEN", "private-fixture")

    async def launch(program, *args, **kwargs):
        assert program == sys.executable
        assert not {"GH_TOKEN", "GITHUB_TOKEN"}.intersection(kwargs["env"])
        assert Path(kwargs["cwd"]).is_dir()
        child = await spawn(program, "-I", script, *args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    return script, children


def test_subprocess_contract_and_usage(fake_cli):
    script, _ = fake_cli
    script.write_text(
        """import json, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:5] == ["--no-daemon", "-a", "never", "exec", "--model"]
assert args[5] == "gpt-6-sol" and 'model_reasoning_effort="medium"' in args
assert args[args.index("--sandbox")+1] == "read-only"
assert "--ignore-user-config" in args and "--ephemeral" in args
assert "shell_tool" in args and args[-1] == "-"
assert not any("fixture-data" in arg for arg in args)
assert sys.stdin.buffer.read().decode("utf-8") == "fixture-data $() & café"
Path(args[args.index("--output-last-message")+1]).write_text("[]", encoding="utf-8")
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"progress"}}))
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":7,"cached_input_tokens":2,"output_tokens":1}}))
""",
        encoding="utf-8",
    )
    usage = []
    result = asyncio.run(
        codex_cli.codex_review(
            "review", "fixture-data $() & café", record_usage=usage.append
        )
    )
    assert result == "[]"
    assert usage == [codex_cli.ResponseUsage(None, 7, 2, 1)]


@pytest.mark.parametrize(
    "body",
    [
        "import sys; print('private-fixture', file=sys.stderr); sys.exit(4)",
        "print('invalid events')",
        """print('{"type":"turn.completed"}')""",
    ],
)
def test_failed_process_never_supplies_an_answer(fake_cli, body):
    script, _ = fake_cli
    script.write_text(body, encoding="utf-8")
    with pytest.raises(codex_cli.ProviderError) as error:
        asyncio.run(codex_cli.codex_review("review", "private-fixture"))
    assert "private-fixture" not in str(error.value)


def test_subprocess_deadline_reaps_child(fake_cli, monkeypatch):
    script, children = fake_cli
    script.write_text("import time; time.sleep(30)", encoding="utf-8")
    monkeypatch.setattr(codex_cli, "TIMEOUT_SECONDS", 0.1)
    with pytest.raises(TimeoutError):
        asyncio.run(codex_cli.codex_review("review", "data"))
    assert len(children) == 1 and children[0].returncode is not None
