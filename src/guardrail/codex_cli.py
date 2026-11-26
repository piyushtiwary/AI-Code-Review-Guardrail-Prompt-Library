"""Run one ephemeral Codex turn using the local CLI's authentication."""

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

MODEL = "gpt-6-sol"
REASONING = "medium"
TIMEOUT_SECONDS = 120
MAX_REPLY_BYTES = 64 * 1024


class ProviderError(Exception):
    """A diagnostic safe to include in the review report."""


@dataclass(frozen=True)
class ResponseUsage:
    model: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None


def executable() -> str:
    override = os.environ.get("GUARDRAIL_CODEX_BIN")
    if override:
        path = Path(override)
        if not path.is_absolute() or not path.is_file():
            raise ProviderError(
                "GUARDRAIL_CODEX_BIN must name an installed executable by absolute path"
            )
        return str(path)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        path = Path(os.environ["LOCALAPPDATA"]) / "Programs/OpenAI/Codex/bin/codex.exe"
        if path.is_file():
            return str(path)
    path = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not path:
        raise ProviderError(
            "Codex CLI was not found; install it or set GUARDRAIL_CODEX_BIN"
        )
    return str(Path(path).resolve())


async def codex_review(
    instructions: str,
    data: str,
    *,
    record_usage: Callable[[ResponseUsage], None] | None = None,
) -> str:
    program = executable()
    # These settings apply only to this process. Keep untrusted text out of argv,
    # personal configuration, repository instructions and executable tools.
    instructions += "\nAnswer using only the supplied input. Do not use tools. Return the requested format without Markdown fences."
    args = [
        program,
        "--no-daemon",
        "-a",
        "never",
        "exec",
        "--model",
        MODEL,
        "-c",
        f'model_reasoning_effort="{REASONING}"',
        "-c",
        "developer_instructions=" + json.dumps(instructions, ensure_ascii=False),
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--disable",
        "shell_tool",
        "--disable",
        "apps",
        "--disable",
        "plugins",
        "--disable",
        "multi_agent",
        "-c",
        'web_search="disabled"',
        "--json",
        "--output-last-message",
        "reply.txt",
        "-",
    ]
    env = os.environ.copy()
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        env.pop(name, None)
    with TemporaryDirectory(prefix="guardrail codex ") as directory:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=directory,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise ProviderError(
                "could not start Codex CLI; check GUARDRAIL_CODEX_BIN"
            ) from None
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                stdout, _ = await process.communicate(data.encode("utf-8"))
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode:
            raise ProviderError(
                f"Codex CLI exited with status {process.returncode}; check codex --no-daemon login status and access to {MODEL}"
            )
        try:
            completed = next(
                event
                for line in reversed(stdout.splitlines())
                if (event := json.loads(line)).get("type") == "turn.completed"
            )
            reply = Path(directory, "reply.txt")
            if reply.stat().st_size > MAX_REPLY_BYTES:
                raise ProviderError("Codex CLI reply exceeded the size limit")
            answer = reply.read_text(encoding="utf-8").strip()
            if not answer:
                raise ProviderError("Codex CLI returned an empty final answer")
        except (OSError, ValueError, AttributeError, StopIteration):
            raise ProviderError(
                "Codex CLI did not return a completed turn and final answer"
            ) from None
        if record_usage and isinstance(usage := completed.get("usage"), dict):
            record_usage(
                ResponseUsage(
                    None,
                    usage.get("input_tokens"),
                    usage.get("cached_input_tokens"),
                    usage.get("output_tokens"),
                )
            )
        return answer
