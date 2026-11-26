"""Score packaged examples without importing or running generated Python."""

import ast
import asyncio
import io
import json
import re
import tokenize
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime

from pydantic import ValidationError

from guardrail.codex_cli import MODEL, TIMEOUT_SECONDS, ProviderError
from guardrail.prompts import (
    CriteriaContract,
    Evaluation,
    Example,
    LoadedPrompt,
    PromptError,
    ReviewContract,
    ScaffoldContract,
)
from guardrail.review import Finding
from guardrail.story import StoryInputError, parse_story


class Result(Evaluation):
    prompt_id: str
    version: str
    fixture: str


class ContractFailure(ValueError):
    """A model or replay response violated the fixture's output contract."""


def _review(output: str, prompt_id: str, expected: ReviewContract) -> str:
    try:
        items = json.loads(output)
    except (ValueError, RecursionError):
        raise ContractFailure("expected a JSON array of findings") from None
    if not isinstance(items, list):
        raise ContractFailure("expected a JSON array of findings")
    actual = Counter()
    for index, item in enumerate(items, start=1):
        try:
            finding = Finding.model_validate(item)
        except ValidationError:
            raise ContractFailure(f"finding {index} has invalid fields") from None
        if finding.check != prompt_id:
            raise ContractFailure(f"finding {index} must use check {prompt_id}")
        actual[finding.file, finding.line] += 1
    wanted = Counter((a.file, a.line) for a in expected.anchors)
    if actual != wanted:
        missing = [f"{p}:{n}" for p, n in (wanted - actual).elements()]
        extra = [f"{p}:{n}" for p, n in (actual - wanted).elements()]
        raise ContractFailure(
            f"anchor mismatch (missing {missing}, unexpected {extra})"
        )
    return f"matched {len(items)} of {len(items)} expected finding anchors"


def _scaffold(output: str, expected: ScaffoldContract) -> str:
    try:
        tree = ast.parse(output)
        # ast.parse alone accepts some invalid modules, e.g. a top-level return.
        compile(tree, "<scaffold>", "exec")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ContractFailure(
            f"invalid Python syntax at line {getattr(exc, 'lineno', '?')}"
        ) from None
    if not any(
        isinstance(node, ast.Import)
        and any(
            alias.name == "pytest" and alias.asname in (None, "pytest")
            for alias in node.names
        )
        for node in tree.body
    ):
        raise ContractFailure("missing import pytest")
    tests = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    if len(tests) != len(expected.responses) or len({t.name for t in tests}) != len(
        tests
    ):
        raise ContractFailure(
            f"expected {len(expected.responses)} distinct top-level test functions"
        )
    comments = [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(output).readline)
        if token.type == tokenize.COMMENT
    ]
    seen = set()
    for test in tests:
        if isinstance(test, ast.AsyncFunctionDef) or list(
            ast.iter_child_nodes(test.args)
        ):
            raise ContractFailure(
                f"{test.name} must be a synchronous, parameterless test"
            )
        if ast.unparse(test.body[-1]) != "pytest.fail('TODO: implement this scenario')":
            raise ContractFailure(
                f"{test.name} must end with the required pytest.fail TODO"
            )
        body = "\n".join(
            comment
            for line, comment in comments
            if test.lineno < line <= test.end_lineno
        )
        sections = re.split(
            r"^#\s*(arrange|act|assert)\b", body, flags=re.IGNORECASE | re.MULTILINE
        )
        if [name.lower() for name in sections[1::2]] != ["arrange", "act", "assert"]:
            raise ContractFailure(
                f"{test.name} needs Arrange, Act and Assert comments in order"
            )
        arrange, act, assertion = sections[2::2]
        request = arrange + "\n" + act
        matches = [
            key
            for key in expected.responses
            if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", request)
        ]
        if len(matches) != 1 or matches[0] in seen:
            raise ContractFailure(
                f"{test.name} must cover one distinct request from the example"
            )
        key = matches[0]
        status = expected.responses[key]
        if not re.search(rf"\b{status}\b", assertion):
            raise ContractFailure(
                f"{test.name} must expect HTTP {status} for request {key}"
            )
        seen.add(key)
    return f"matched {len(tests)} test functions, HTTP responses and terminal TODOs"


def _score(prompt_id: str, example: Example, output: str) -> str:
    expected = example.expected
    if isinstance(expected, ReviewContract):
        return _review(output, prompt_id, expected)
    if isinstance(expected, ScaffoldContract):
        return _scaffold(output, expected)
    if isinstance(expected, CriteriaContract):
        try:
            story = parse_story(output)
        except StoryInputError as exc:
            raise ContractFailure(str(exc)) from None
        if story.title != example.inputs.get("title"):
            raise ContractFailure("story title differs from the supplied title")
        names = [scenario.name for scenario in story.scenarios]
        if names != expected.scenarios:
            raise ContractFailure(
                f"expected scenarios {expected.scenarios}, found {names}"
            )
        return f"matched {len(names)} ordered scenarios with Given/When/Then clauses"
    raise PromptError(f"{prompt_id} has no fixture output contract")


async def _request(
    provider: Callable[[str, str], Awaitable[str]], rendered: str
) -> str:
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            return await provider(
                "Follow the supplied prompt and its output format.", rendered
            )
    except TimeoutError:
        raise ProviderError("provider request timed out") from None
    except ProviderError:
        raise
    except Exception:  # noqa: BLE001 - Provider diagnostics can contain request data or credentials
        raise ProviderError("could not complete provider request") from None


async def evaluate(
    entries: Iterable[LoadedPrompt],
    provider: Callable[[str, str], Awaitable[str]] | None = None,
) -> tuple[list[Result], int]:
    live = provider is not None
    records = []
    status = 0
    for entry in entries:
        prompt = entry.prompt
        for name, example in entry.fixtures.items():
            passed = False
            try:
                if provider is None:
                    output = example.reference_output
                else:
                    output = await _request(provider, prompt.render(example.inputs))
                reason = _score(prompt.id, example, output)
                passed = True
            except ContractFailure as exc:
                reason = str(exc)
                status = max(status, 1)
            except (ProviderError, PromptError) as exc:
                reason = str(exc)
                status = 3
            records.append(
                Result(
                    prompt_id=prompt.id,
                    version=prompt.version,
                    example=name,
                    fixture=prompt.examples[name],
                    mode="live" if live else "offline",
                    passed=passed,
                    reason=reason,
                    provider="codex-cli" if live else None,
                    model=MODEL if live else None,
                    evaluated_on=datetime.now(UTC).date() if live else None,
                )
            )
    return records, status
