"""Parse line-oriented acceptance criteria for pytest scaffold generation."""

import re
from dataclasses import dataclass


class StoryInputError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        self.line = line
        super().__init__(f"Line {line}: {message}")


@dataclass(frozen=True)
class Scenario:
    name: str
    conditions: tuple[str, ...]
    actions: tuple[str, ...]
    outcomes: tuple[str, ...]


@dataclass(frozen=True)
class Story:
    title: str
    scenarios: tuple[Scenario, ...]


_PHASES = ("Given", "When", "Then")
_ISSUE_HEADING = re.compile(r"[A-Z][A-Z0-9_]*-\d+(?:\s*:|\s+-)\s*\S.*")


def _scenario(name: str, header_line: int, lines: list[tuple[int, str]]) -> Scenario:
    groups: dict[str, list[str]] = {phase: [] for phase in _PHASES}
    current = -1
    for number, clause in lines:
        parts = clause.split(maxsplit=1)
        keyword = parts[0]
        if keyword == "And":
            if current < 0:
                raise StoryInputError(number, "And needs a preceding Given clause.")
        elif keyword in _PHASES:
            phase = _PHASES.index(keyword)
            if phase < current:
                raise StoryInputError(
                    number, f"{keyword} cannot follow {_PHASES[current]}."
                )
            if phase > current + 1:
                raise StoryInputError(
                    number, f"Expected {_PHASES[current + 1]} before {keyword}."
                )
            current = phase
        else:
            raise StoryInputError(
                number, "Expected a Given, When, Then, or And clause."
            )
        if len(parts) == 1:
            raise StoryInputError(number, f"{keyword} needs clause text.")
        groups[_PHASES[current]].append(parts[1])

    if current < len(_PHASES) - 1:
        raise StoryInputError(
            header_line,
            f"Scenario '{name}' is missing a {_PHASES[current + 1]} clause.",
        )
    return Scenario(
        name, tuple(groups["Given"]), tuple(groups["When"]), tuple(groups["Then"])
    )


def parse_story(text: str) -> Story:
    """Parse a Story: title or Jira issue heading followed by named scenarios."""
    lines = (
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip()
    )
    header_line, heading = next(lines, (1, ""))
    if heading.startswith("Story:"):
        title = heading.removeprefix("Story:").strip()
        if not title:
            raise StoryInputError(header_line, "Story needs a title.")
    elif _ISSUE_HEADING.fullmatch(heading):
        title = heading
    else:
        raise StoryInputError(
            header_line,
            "Expected a story heading such as 'SHIP-42: Reroute a shipment'.",
        )

    scenarios = []
    name = ""
    scenario_line = header_line
    clauses: list[tuple[int, str]] = []
    for number, line in lines:
        if line.startswith("Scenario:"):
            if name:
                scenarios.append(_scenario(name, scenario_line, clauses))
            name = line.removeprefix("Scenario:").strip()
            if not name:
                raise StoryInputError(number, "Scenario needs a name.")
            scenario_line = number
            clauses = []
        elif name:
            clauses.append((number, line))
        else:
            raise StoryInputError(
                number, "Expected 'Scenario: <name>' before its clauses."
            )

    if not name:
        raise StoryInputError(header_line, "Story needs at least one named scenario.")
    scenarios.append(_scenario(name, scenario_line, clauses))
    return Story(title, tuple(scenarios))
