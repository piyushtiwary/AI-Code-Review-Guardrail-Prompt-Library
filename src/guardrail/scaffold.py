"""Generate a pytest module from a parsed acceptance story."""

import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from guardrail.story import Story


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _render(story: Story) -> str:
    cases = []
    used: set[str] = set()
    for scenario in story.scenarios:
        base = "test_" + (_slug(scenario.name) or "scenario")
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        cases.append((name, scenario))

    env = Environment(
        loader=PackageLoader("guardrail", "templates"),
        undefined=StrictUndefined,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    source = env.get_template("scaffold.py.jinja2").render(cases=cases)
    # Python rejects literal NUL characters even inside comments.
    return source.replace("\x00", r"\x00")


def write_scaffold(story: Story, stem: str, out: Path) -> Path:
    """Create a test module, raising FileExistsError if the target is occupied."""
    source = _render(story)
    target = out / f"test_{_slug(stem) or 'story'}.py"
    out.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(source)
    return target
