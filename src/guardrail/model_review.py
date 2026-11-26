"""Optional, bounded model review of selected Python hunks."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass

from pydantic import ValidationError
from unidiff import Hunk

from guardrail.codex_cli import TIMEOUT_SECONDS, ProviderError
from guardrail.prompts import load_prompts
from guardrail.review import Finding
from guardrail.source import ReviewSource

PROMPT_ID = "diff-edge-case-review"
PROMPT_VERSION = "1.0.0"
MAX_INPUT_BYTES = 16_384
COMPLEXITY_LINES = 12


@dataclass
class ModelReview:
    findings: list[Finding]
    status: str


def _eligible_hunks(
    source: ReviewSource, deterministic: list[Finding]
) -> Iterator[tuple[str, Hunk, set[int]]]:
    flagged: dict[str, set[int]] = {}
    for finding in deterministic:
        if finding.line is not None:
            flagged.setdefault(finding.file, set()).add(finding.line)
    for change in source.changes:
        path = change.new_path
        if change.is_binary or not path or not path.endswith(".py"):
            continue
        flags = flagged.get(source.redactor.redact(path), set())
        for hunk in change.hunks:
            added = [line for line in hunk if line.is_added]
            locations = {line.target_line_no for line in added}
            code_lines = sum(
                1
                for line in added
                if line.value.strip() and not line.value.lstrip().startswith("#")
            )
            if not locations.isdisjoint(flags) or code_lines >= COMPLEXITY_LINES:
                yield path, hunk, locations


async def review_with_model(
    source: ReviewSource,
    deterministic: list[Finding],
    provider: Callable[[str, str], Awaitable[str]],
) -> ModelReview:
    excerpts = []
    anchors: set[tuple[str, int]] = set()
    skipped = 0
    try:
        prompt = load_prompts()[PROMPT_ID, PROMPT_VERSION].prompt
        # The trusted instructions and untrusted diff travel in separate CLI inputs.
        instructions = prompt.render({"hunks": ""})
        remaining = MAX_INPUT_BYTES - len(instructions.encode("utf-8"))
        for path, hunk, lines in _eligible_hunks(source, deterministic):
            # A masked filename cannot identify the exact repository path.
            if source.redactor.redact(path) != path:
                skipped += 1
                continue
            excerpt = source.prepare_model_hunk(path, hunk)
            try:
                size = len(excerpt.encode("utf-8")) + int(bool(excerpts))
            except UnicodeEncodeError:
                skipped += 1
                continue
            if size > remaining:
                skipped += 1
                continue
            remaining -= size
            excerpts.append(excerpt)
            anchors.update((path, line) for line in lines)
        notes = []
        if skipped:
            notes.append(f"{skipped} eligible hunk(s) omitted")
        if not excerpts:
            if notes:
                return ModelReview([], f"Model review incomplete: {notes[0]}.")
            return ModelReview([], "Model review skipped: no eligible Python hunks.")
        async with asyncio.timeout(TIMEOUT_SECONDS):
            raw = await provider(instructions, "\n".join(excerpts))
    except TimeoutError:
        return ModelReview([], "Model review incomplete: request timed out.")
    except ProviderError as exc:
        return ModelReview([], f"Model review incomplete: {exc}.")
    except Exception:  # noqa: BLE001 - Provider errors may contain credentials or raw input
        return ModelReview(
            [], "Model review incomplete: could not prepare or complete the request."
        )

    try:
        items = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return ModelReview([], "Model review incomplete: invalid JSON response.")
    if not isinstance(items, list):
        return ModelReview([], "Model review incomplete: expected a JSON array.")

    accepted = []
    for item in items:
        try:
            finding = Finding.model_validate(item)
        except ValidationError:
            continue
        if finding.check == PROMPT_ID and (finding.file, finding.line) in anchors:
            accepted.append(finding.redacted(source.redactor))
    rejected = len(items) - len(accepted)
    if rejected:
        notes.append(f"{rejected} invalid or unanchored item(s) discarded")
    if notes:
        return ModelReview(
            accepted, "Model review incomplete: " + ", ".join(notes) + "."
        )
    return ModelReview(
        accepted, f"Model review complete: {len(excerpts)} hunk(s) reviewed."
    )
