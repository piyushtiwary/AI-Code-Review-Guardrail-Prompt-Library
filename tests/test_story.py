from pathlib import Path

import pytest

from guardrail.story import Scenario, StoryInputError, parse_story


def test_shipment_rerouting_story():
    path = Path(__file__).resolve().parents[1] / "stories" / "reroute.feature"
    story = parse_story(path.read_text(encoding="utf-8"))

    assert story.title == "SHIP-42: Reroute a shipment"
    assert story.scenarios == (
        Scenario(
            "A shipment has breached its SLA",
            (
                "the shipment has passed its promised delivery time",
                "an alternate carrier has capacity",
            ),
            ("the dispatcher requests a reroute", "confirms the alternate carrier"),
            (
                "the shipment is assigned to the alternate carrier",
                "the customer receives a revised delivery time",
            ),
        ),
        Scenario(
            "A shipment is on time",
            ("the shipment is within its promised delivery window",),
            ("the dispatcher requests a reroute",),
            ("the shipment stays on its original route",),
        ),
    )


@pytest.mark.parametrize(
    ("heading", "title"),
    [
        ("Story: Route parcels", "Route parcels"),
        ("OPS-7 - Route parcels", "OPS-7 - Route parcels"),
    ],
)
def test_original_wording_and_consecutive_continuations(heading, title):
    text = (
        f"{heading}\r\n"
        "  Scenario: Confirm ETA: next-day\r\n"
        '    Given a label reading "A  and B"\r\n'
        "    When I scan it\r\n"
        "    Then show ETA: 10:30.\r\n"
        "    And keep the parcel ID\r\n"
        "    And keep its original ZIP"
    )
    story = parse_story(text)
    assert story.title == title
    assert story.scenarios == (
        Scenario(
            "Confirm ETA: next-day",
            ('a label reading "A  and B"',),
            ("I scan it",),
            ("show ETA: 10:30.", "keep the parcel ID", "keep its original ZIP"),
        ),
    )


@pytest.mark.parametrize("boundary", ["", "\nScenario: Next shipment"])
def test_missing_then_points_to_incomplete_scenario(boundary):
    text = "Story: Routing\n\nScenario: Delayed\n  Given a parcel\n  When it arrives"
    with pytest.raises(
        StoryInputError, match="Scenario 'Delayed' is missing a Then clause"
    ) as error:
        parse_story(text + boundary)
    assert error.value.line == 3


@pytest.mark.parametrize(
    ("clauses", "line", "message"),
    [
        ("And it is late", 3, "And needs a preceding Given"),
        ("When it arrives", 3, "Expected Given before When"),
        ("Given a parcel\nThen notify", 4, "Expected When before Then"),
        (
            "Given a parcel\nWhen it arrives\nGiven a route",
            5,
            "Given cannot follow When",
        ),
        ("Given a parcel\nWhen", 4, "When needs clause text"),
        (
            "Given a parcel\nUnless it arrives",
            4,
            "Expected a Given, When, Then, or And",
        ),
        (
            "Given a parcel\nWhen it arrives\nThen notify\nScenario: Next\nAnd dispatch",
            7,
            "And needs a preceding Given",
        ),
    ],
)
def test_misplaced_or_empty_clause(clauses, line, message):
    with pytest.raises(StoryInputError) as error:
        parse_story(f"Story: Routing\nScenario: Delayed\n{clauses}")
    assert f"Line {line}: {message}" in str(error.value)


@pytest.mark.parametrize(
    ("text", "line", "message"),
    [
        ("", 1, "Expected a story heading"),
        ("\nStory:", 2, "Story needs a title"),
        ("SHIP-42: Routing", 1, "at least one named scenario"),
        ("Story: Routing\nScenario:", 2, "Scenario needs a name"),
        ("Story: Routing\n\nGiven a parcel", 3, "Expected 'Scenario: <name>'"),
    ],
)
def test_incomplete_heading(text, line, message):
    with pytest.raises(StoryInputError) as error:
        parse_story(text)
    assert error.value.line == line
    assert message in str(error.value)
