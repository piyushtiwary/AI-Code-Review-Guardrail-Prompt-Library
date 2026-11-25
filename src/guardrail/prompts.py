"""Load and render the versioned prompt library from package resources."""

from collections.abc import Mapping
from datetime import date
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Annotated, Literal, Self

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError, meta
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class PromptError(ValueError):
    """A prompt definition, fixture, or set of render inputs is invalid."""


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
InputName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
FixtureName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]*\.yaml$")]
# Numeric prerelease identifiers, like the three release numbers, forbid leading zeros.
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PRE = rf"(?:{_NUMBER}|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = rf"^{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-{_PRE}(?:\.{_PRE})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
_ENV = Environment(undefined=StrictUndefined, keep_trailing_newline=True)


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Anchor(_Entry):
    file: Text
    line: Annotated[int, Field(strict=True, gt=0)]


class ReviewContract(_Entry):
    anchors: list[Anchor]


class ScaffoldContract(_Entry):
    # Map each example request identifier to its expected HTTP status.
    responses: Annotated[
        dict[Text, Annotated[int, Field(ge=100, le=599)]], Field(min_length=1)
    ]


class CriteriaContract(_Entry):
    scenarios: Annotated[list[Text], Field(min_length=1)]


class Example(_Entry):
    inputs: dict[InputName, Text]
    reference_output: Text
    expected: ReviewContract | ScaffoldContract | CriteriaContract | None = None


class Evaluation(_Entry):
    example: Identifier
    mode: Literal["offline", "live"]
    passed: bool
    reason: Text
    provider: Text | None = None
    model: Text | None = None
    evaluated_on: date | None = None


class Prompt(_Entry):
    id: Identifier
    version: Annotated[str, Field(pattern=_SEMVER)]
    purpose: Text
    inputs: Annotated[dict[InputName, Text], Field(min_length=1)]
    template: Text
    examples: Annotated[dict[Identifier, FixtureName], Field(min_length=1)]
    expected_output_properties: Annotated[list[Text], Field(min_length=1)]
    known_failure_modes: Annotated[list[Text], Field(min_length=1)]
    evaluation_history: list[Evaluation]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        names = meta.find_undeclared_variables(_ENV.parse(self.template))
        if names != self.inputs.keys():
            mismatch = ", ".join(sorted(names ^ self.inputs.keys()))
            raise ValueError(f"template and declared inputs differ: {mismatch}")
        for result in self.evaluation_history:
            if result.example not in self.examples:
                raise ValueError(f"unknown evaluation example: {result.example}")
        return self

    def render(self, inputs: Mapping[str, str]) -> str:
        missing = self.inputs.keys() - inputs.keys()
        extra = inputs.keys() - self.inputs.keys()
        if missing:
            raise PromptError(f"missing template inputs: {', '.join(sorted(missing))}")
        if extra:
            raise PromptError(f"unexpected template inputs: {', '.join(sorted(extra))}")
        return _ENV.from_string(self.template).render(inputs)


class LoadedPrompt(_Entry):
    prompt: Prompt
    fixtures: dict[str, Example]


def load_prompts(
    root: Traversable | None = None,
) -> dict[tuple[str, str], LoadedPrompt]:
    """Read the whole library and check every fixture before returning any entries."""
    if root is None:
        root = files("guardrail").joinpath("prompt_library")
    library = {}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or not entry.name.endswith(".yaml"):
            continue
        source = entry
        try:
            prompt = Prompt.model_validate(yaml.safe_load(entry.read_text("utf-8")))
            key = (prompt.id, prompt.version)
            if key in library:
                raise PromptError(f"duplicate prompt {prompt.id} {prompt.version}")
            fixtures = {}
            for name, filename in prompt.examples.items():
                source = root.joinpath("fixtures", filename)
                example = Example.model_validate(
                    yaml.safe_load(source.read_text("utf-8"))
                )
                prompt.render(example.inputs)
                fixtures[name] = example
            library[key] = LoadedPrompt(prompt=prompt, fixtures=fixtures)
        except (OSError, ValueError, TemplateError, yaml.YAMLError) as exc:
            raise PromptError(f"{source.name}: {exc}") from exc
    if not library:
        raise PromptError("no prompt definitions found")
    return library
