"""Parser tests. Pure functions, fixture-driven, no network."""

from __future__ import annotations

import random
from typing import Any

import pytest

from app.linkedin.parser import parse_profile
from app.models import Profile

# =============================================================================
# core contract (Phase 3 Definition of Done)
# =============================================================================


def test_parser_core_fields(sample_raw: dict[str, Any]) -> None:
    p = parse_profile(sample_raw, "some-slug")
    assert p.full_name and p.headline
    assert len(p.experience) >= 1
    assert len(p.education) >= 1
    assert len(p.skills) >= 1


def test_parser_never_crashes_on_empty() -> None:
    p = parse_profile({"included": []}, "x")
    assert p.public_identifier == "x"
    assert p.warnings  # degraded gracefully


# =============================================================================
# identity
# =============================================================================


def test_identity_fields(sample_raw: dict[str, Any]) -> None:
    p = parse_profile(sample_raw, "ignored-slug")

    assert p.first_name == "Jane"
    assert p.last_name == "Doe"
    assert p.full_name == "Jane Doe"
    # The payload's own publicIdentifier wins over the slug we asked for.
    assert p.public_identifier == "jane-doe-example"
    assert p.linkedin_url == "https://www.linkedin.com/in/jane-doe-example"
    assert p.urn == "urn:li:fs_profile:ACoAAAExample01"
    assert p.headline is not None and "Senior Software Engineer" in p.headline
    assert p.about is not None and p.about.startswith("Backend engineer")


def test_headline_falls_back_to_mini_profile_occupation(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        if entity.get("entityUrn") == "urn:li:fs_profile:ACoAAAExample01":
            del entity["headline"]
    p = parse_profile(sample_raw, "jane-doe-example")
    assert p.headline == "Senior Software Engineer at Acme Corp"


def test_public_identifier_falls_back_to_requested_slug(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        entity.pop("publicIdentifier", None)
    p = parse_profile(sample_raw, "requested-slug")
    assert p.public_identifier == "requested-slug"
    assert p.linkedin_url == "https://www.linkedin.com/in/requested-slug"


# =============================================================================
# location
# =============================================================================


def test_location(sample_raw: dict[str, Any]) -> None:
    loc = parse_profile(sample_raw, "x").location
    assert loc.full == "San Francisco, California"
    assert loc.city == "San Francisco"
    assert loc.country == "United States"


def test_location_does_not_invent_a_city_from_a_metro_area(sample_raw: dict[str, Any]) -> None:
    """"San Francisco Bay Area" is a metro; calling it a city would be wrong."""
    for entity in sample_raw["included"]:
        if entity.get("entityUrn") == "urn:li:fs_profile:ACoAAAExample01":
            entity["locationName"] = "San Francisco Bay Area"
            entity["geoLocationName"] = "San Francisco Bay Area"
    loc = parse_profile(sample_raw, "x").location
    assert loc.full == "San Francisco Bay Area"
    assert loc.city is None


def test_location_country_falls_back_to_iso_code(dash_raw: dict[str, Any]) -> None:
    loc = parse_profile(dash_raw, "dash-example").location
    assert loc.full == "Berlin, Germany"
    assert loc.city == "Berlin"
    assert loc.country == "DE"  # only the alpha-2 code was exposed


# =============================================================================
# experience
# =============================================================================


def test_experience_order_and_details(sample_raw: dict[str, Any]) -> None:
    exp = parse_profile(sample_raw, "x").experience
    assert len(exp) == 3

    # LinkedIn's own ordering (from positionView["*elements"]) is preserved.
    assert [e.title for e in exp] == [
        "Senior Software Engineer",
        "Software Engineer II",
        "Backend Engineer",
    ]

    current = exp[0]
    assert current.company == "Acme Corp"
    assert current.company_url == "https://www.linkedin.com/company/acme-corp-example"
    assert current.location == "San Francisco, California, United States"
    assert current.start_date == "2021-03"
    assert current.end_date is None
    assert current.is_current is True
    assert current.description is not None and "p99" in current.description

    past = exp[1]
    assert past.start_date == "2018-08"
    assert past.end_date == "2021-02"
    assert past.is_current is False

    # Year-only start date must not be padded into a fake month.
    assert exp[2].start_date == "2016"
    assert exp[2].end_date == "2018-07"
    assert exp[2].company_url is None  # no companyUrn on this position


def test_company_url_falls_back_to_numeric_id(sample_raw: dict[str, Any]) -> None:
    """If the MiniCompany entity is absent we can still build a working URL."""
    sample_raw["included"] = [
        e for e in sample_raw["included"] if e.get("entityUrn") != "urn:li:fs_miniCompany:1000001"
    ]
    exp = parse_profile(sample_raw, "x").experience
    assert exp[0].company_url == "https://www.linkedin.com/company/1000001"
    assert exp[0].company == "Acme Corp"  # companyName still on the position


def test_position_with_no_dates_is_not_claimed_as_current(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        if entity.get("entityUrn") == "urn:li:fs_position:(ACoAAAExample01,1900000001)":
            del entity["timePeriod"]
    exp = parse_profile(sample_raw, "x").experience
    assert exp[0].start_date is None
    assert exp[0].end_date is None
    assert exp[0].is_current is False


# =============================================================================
# education / skills / certifications / languages
# =============================================================================


def test_education(sample_raw: dict[str, Any]) -> None:
    edu = parse_profile(sample_raw, "x").education
    assert len(edu) == 2
    assert edu[0].school == "Stanford University"
    assert edu[0].degree == "Master of Science - MS"
    assert edu[0].field_of_study == "Computer Science"
    assert edu[0].start_year == 2014
    assert edu[0].end_year == 2016
    assert edu[1].school == "University of Illinois Urbana-Champaign"


def test_education_school_resolved_from_urn(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        if entity.get("entityUrn") == "urn:li:fs_education:(ACoAAAExample01,2900000001)":
            del entity["schoolName"]
    edu = parse_profile(sample_raw, "x").education
    assert edu[0].school == "Stanford University"  # via urn:li:fs_miniSchool:1792


def test_skills(sample_raw: dict[str, Any]) -> None:
    skills = parse_profile(sample_raw, "x").skills
    assert len(skills) == 8
    assert skills[0] == "Distributed Systems"
    assert "Rust" in skills


def test_skills_are_deduped_case_insensitively(sample_raw: dict[str, Any]) -> None:
    sample_raw["included"].append(
        {
            "$type": "com.linkedin.voyager.identity.profile.Skill",
            "entityUrn": "urn:li:fs_skill:(ACoAAAExample01,8)",
            "name": "python",
        }
    )
    skills = parse_profile(sample_raw, "x").skills
    assert len([s for s in skills if s.casefold() == "python"]) == 1


def test_certifications(sample_raw: dict[str, Any]) -> None:
    certs = parse_profile(sample_raw, "x").certifications
    assert len(certs) == 2
    assert certs[0].name == "AWS Certified Solutions Architect - Associate"
    assert certs[0].authority == "Amazon Web Services (AWS)"
    assert certs[0].url is not None and certs[0].url.startswith("https://www.credly.com/")
    assert certs[0].start_date == "2022-06"
    assert certs[0].end_date == "2025-06"
    assert certs[1].end_date is None  # non-expiring


def test_languages_proficiency_is_humanized(sample_raw: dict[str, Any]) -> None:
    langs = parse_profile(sample_raw, "x").languages
    assert len(langs) == 2
    assert langs[0].name == "English"
    assert langs[0].proficiency == "Native or bilingual proficiency"
    assert langs[1].proficiency == "Professional working proficiency"


def test_unknown_proficiency_code_is_still_readable(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        if entity.get("entityUrn") == "urn:li:fs_language:(ACoAAAExample01,0)":
            entity["proficiency"] = "SOME_NEW_CODE"
    langs = parse_profile(sample_raw, "x").languages
    assert langs[0].proficiency == "Some new code"


# =============================================================================
# images
# =============================================================================


def test_picks_highest_resolution_image(sample_raw: dict[str, Any]) -> None:
    p = parse_profile(sample_raw, "x")
    assert p.profile_picture_url == (
        "https://media.licdn.com/dms/image/v2/D5603AQEXAMPLEPHOTO/"
        "profile-displayphoto-shrink_800_800/0/1700000000000"
        "?e=1767225600&v=beta&t=EXAMPLE800"
    )
    assert p.background_image_url == (
        "https://media.licdn.com/dms/image/v2/D5616AQEXAMPLECOVER/"
        "profile-displaybackgroundimage-shrink_1400_425/0/1700000000000"
        "?e=1767225600&v=beta&t=EXAMPLE1400"
    )


def test_image_falls_back_to_mini_profile_picture(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        if entity.get("entityUrn") == "urn:li:fs_profile:ACoAAAExample01":
            del entity["profilePicture"]
    p = parse_profile(sample_raw, "x")
    # MiniProfile.picture tops out at 800 as well.
    assert p.profile_picture_url is not None
    assert "shrink_800_800" in p.profile_picture_url


def test_missing_image_warns_instead_of_failing(sample_raw: dict[str, Any]) -> None:
    for entity in sample_raw["included"]:
        entity.pop("profilePicture", None)
        entity.pop("picture", None)
    p = parse_profile(sample_raw, "x")
    assert p.profile_picture_url is None
    assert any("profile picture" in w for w in p.warnings)


# =============================================================================
# structural robustness — the properties that matter when LinkedIn changes things
# =============================================================================


def test_parsing_is_independent_of_included_array_order(sample_raw: dict[str, Any]) -> None:
    """Never parse by index: a shuffled `included[]` must give an identical result."""
    baseline = parse_profile(sample_raw, "x").model_dump()

    for seed in (1, 7, 42, 1234):
        shuffled = dict(sample_raw)
        entities = list(sample_raw["included"])
        random.Random(seed).shuffle(entities)
        shuffled["included"] = entities
        assert parse_profile(shuffled, "x").model_dump() == baseline


def test_position_urn_is_not_mistaken_for_a_profile(dash_raw: dict[str, Any]) -> None:
    """`urn:li:fsd_profilePosition:` starts with `urn:li:fsd_profile` as a substring.

    Namespace-exact matching is what keeps a position out of the profile bucket.
    """
    p = parse_profile(dash_raw, "dash-example")
    assert p.full_name == "Dash Example"
    assert p.headline == "Product Manager at Hooli"
    assert len(p.experience) == 1
    assert p.experience[0].title == "Product Manager"


def test_handles_legacy_nested_payload(nested_raw: dict[str, Any]) -> None:
    """No `included[]` at all — entities inline under `*View.elements`."""
    p = parse_profile(nested_raw, "nested-example")
    assert p.full_name == "Sam Nested"
    assert p.headline == "Data Engineer at Initech"
    assert p.about == "Short about section."
    assert p.location.full == "Austin, Texas"
    assert p.location.city == "Austin"
    assert p.location.country == "United States"
    assert [e.title for e in p.experience] == ["Data Engineer"]
    assert p.experience[0].is_current is True
    assert p.education[0].school == "University of Texas at Austin"
    assert p.education[0].start_year == 2017
    assert p.skills == ["dbt", "Airflow"]
    assert p.profile_picture_url is not None and "shrink_800_800" in p.profile_picture_url


def test_handles_dash_payload_dates(dash_raw: dict[str, Any]) -> None:
    """`dateRange{start,end}` instead of `timePeriod{startDate,endDate}`."""
    p = parse_profile(dash_raw, "dash-example")
    assert p.experience[0].start_date == "2023-05"
    assert p.experience[0].end_date is None
    assert p.experience[0].is_current is True
    assert p.education[0].start_year == 2016
    assert p.education[0].end_year == 2020
    assert p.skills == ["Product Strategy"]


def test_missing_sections_become_warnings_not_exceptions() -> None:
    minimal = {
        "included": [
            {
                "$type": "com.linkedin.voyager.identity.profile.Profile",
                "entityUrn": "urn:li:fs_profile:ACoAAAMinimal",
                "firstName": "Min",
                "lastName": "Imal",
                "headline": "Just a headline",
            }
        ]
    }
    p = parse_profile(minimal, "min-imal")
    assert p.full_name == "Min Imal"
    assert p.experience == []
    assert p.education == []
    assert p.skills == []
    for section in ("experience", "education", "skills", "certifications", "languages"):
        assert any(section in w for w in p.warnings), f"no warning for {section}"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"included": []},
        {"included": None},
        {"included": "not-a-list"},
        {"included": [None, 42, "junk", [], {}]},
        {"data": None, "included": [{"entityUrn": None, "$type": None}]},
        {"included": [{"$type": "com.linkedin.voyager.identity.profile.Profile"}]},
        # every field present but the wrong type
        {
            "included": [
                {
                    "$type": "com.linkedin.voyager.identity.profile.Profile",
                    "entityUrn": "urn:li:fs_profile:ACoAAAJunk",
                    "firstName": 123,
                    "lastName": ["nope"],
                    "headline": {"nested": "wrong"},
                    "summary": None,
                    "locationName": 5,
                    "profilePicture": "not-a-dict-but-a-string",
                    "location": "not-a-dict",
                }
            ]
        },
        {
            "included": [
                {
                    "$type": "com.linkedin.voyager.identity.profile.Position",
                    "entityUrn": "urn:li:fs_position:(ACoAAAJunk,1)",
                    "title": None,
                    "timePeriod": "not-a-dict",
                    "companyUrn": 999,
                }
            ]
        },
        {
            "included": [
                {
                    "$type": "com.linkedin.voyager.identity.profile.Education",
                    "entityUrn": "urn:li:fs_education:(ACoAAAJunk,1)",
                    "timePeriod": {"startDate": "nope", "endDate": {"year": "not-a-year"}},
                }
            ]
        },
        {"positionView": {"elements": "not-a-list"}},
        {"profileView": {"positionView": {"elements": [None, {"title": "T"}]}}},
    ],
)
def test_parser_never_raises_on_hostile_payloads(payload: Any) -> None:
    p = parse_profile(payload, "slug")
    assert isinstance(p, Profile)
    assert p.public_identifier == "slug"
    assert p.warnings
    # And the result is always serializable, so the API can always answer.
    assert isinstance(p.model_dump_json(), str)


def test_deeply_broken_payload_types_do_not_raise() -> None:
    for payload in (None, [], "string", 42, True):
        p = parse_profile(payload, "slug")  # type: ignore[arg-type]
        assert isinstance(p, Profile)
        assert p.warnings
