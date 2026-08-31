"""Turn a raw Voyager payload into a `Profile`.

Voyager's normalized responses are a flat `included[]` array in which every entity —
the profile, each position, each school, each skill — is a sibling, cross-referenced
by `entityUrn`. Nothing about the array's *order or length* is contractual, so this
module classifies entities by URN namespace (with `$type` as a fallback) and never by
array index.

Three payload shapes are handled, because that is what you actually get in the wild:

1. normalized `profileView`  — `{"data": {...}, "included": [...]}`   (fs_*  URNs)
2. legacy   `profileView`  — `{"positionView": {"elements": [...]}}` (inline entities)
3. modern   `dash/profiles` — `included[]` with fsd_* URNs and `dateRange{start,end}`

Contract: **`parse_profile` never raises.** A section that cannot be read produces an
entry in `Profile.warnings` and an empty value, because a half-readable profile is far
more useful to a caller than a 500.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from app.models import (
    Certification,
    Education,
    Experience,
    Language,
    Location,
    Profile,
)

T = TypeVar("T")

LINKEDIN_PROFILE_BASE = "https://www.linkedin.com/in/"
LINKEDIN_COMPANY_BASE = "https://www.linkedin.com/company/"

# --- entity classification ---------------------------------------------------
# Keyed on the URN *namespace* — the third colon-segment of `urn:li:<ns>:<id>`.
# Matching the namespace exactly (rather than substring-searching the whole URN)
# is what stops `urn:li:fsd_profilePosition:(...)` being mistaken for a profile.
_NAMESPACE_KIND: dict[str, str] = {
    "fs_profile": "profile",
    "fsd_profile": "profile",
    "fs_miniProfile": "mini_profile",
    "fsd_miniProfile": "mini_profile",
    "fs_position": "position",
    "fsd_position": "position",
    "fsd_profilePosition": "position",
    "fs_education": "education",
    "fsd_education": "education",
    "fsd_profileEducation": "education",
    "fs_skill": "skill",
    "fsd_skill": "skill",
    "fsd_profileSkill": "skill",
    "fs_certification": "certification",
    "fsd_certification": "certification",
    "fsd_profileCertification": "certification",
    "fs_language": "language",
    "fsd_language": "language",
    "fsd_profileLanguage": "language",
    "fs_miniCompany": "company",
    "fsd_company": "company",
    "fs_miniSchool": "school",
    "fsd_school": "school",
}

# Fallback when the URN namespace is unknown: the last segment of `$type`.
_TYPE_LEAF_KIND: dict[str, str] = {
    "Profile": "profile",
    "MiniProfile": "mini_profile",
    "Position": "position",
    "Education": "education",
    "Skill": "skill",
    "Certification": "certification",
    "Language": "language",
    "MiniCompany": "company",
    "Company": "company",
    "MiniSchool": "school",
    "School": "school",
}

# Ordered URN lists live under these keys; used to preserve LinkedIn's own ordering.
_KIND_VIEW: dict[str, str] = {
    "position": "positionView",
    "education": "educationView",
    "skill": "skillView",
    "certification": "certificationView",
    "language": "languageView",
}

_PROFICIENCY_LABELS: dict[str, str] = {
    "NATIVE_OR_BILINGUAL": "Native or bilingual proficiency",
    "FULL_PROFESSIONAL": "Full professional proficiency",
    "PROFESSIONAL_WORKING": "Professional working proficiency",
    "LIMITED_WORKING": "Limited working proficiency",
    "ELEMENTARY": "Elementary proficiency",
}


# =============================================================================
# small helpers
# =============================================================================


def _urn_namespace(urn: object) -> str:
    """`urn:li:fs_position:(ACoAAA,1)` -> `fs_position`."""
    if not isinstance(urn, str):
        return ""
    parts = urn.split(":", 3)
    return parts[2] if len(parts) >= 3 else ""


def _urn_id(urn: object) -> str:
    """`urn:li:fs_miniCompany:1234` -> `1234`."""
    if not isinstance(urn, str):
        return ""
    parts = urn.split(":", 3)
    return parts[3] if len(parts) >= 4 else ""


def _type_leaf(type_name: object) -> str:
    if not isinstance(type_name, str):
        return ""
    return type_name.rsplit(".", 1)[-1]


def _classify(entity: dict[str, Any]) -> str | None:
    kind = _NAMESPACE_KIND.get(_urn_namespace(entity.get("entityUrn")))
    if kind:
        return kind
    return _TYPE_LEAF_KIND.get(_type_leaf(entity.get("$type")))


def _clean(value: object) -> str | None:
    """Normalize a text field: strings only, whitespace-trimmed, empty -> None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _format_date(date: object) -> str | None:
    """A Voyager `Date` (`{"year": 2021, "month": 3}`) -> `"2021-03"` / `"2021"`."""
    if not isinstance(date, dict):
        return None
    year = _as_int(date.get("year"))
    if year is None:
        return None
    month = _as_int(date.get("month"))
    if month is not None and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


def _date_bounds(entity: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract (start, end) from either `timePeriod` (fs_*) or `dateRange` (fsd_*)."""
    span = entity.get("timePeriod")
    if not isinstance(span, dict):
        span = entity.get("dateRange")
    if not isinstance(span, dict):
        return None, None
    start = span.get("startDate") if isinstance(span.get("startDate"), dict) else span.get("start")
    end = span.get("endDate") if isinstance(span.get("endDate"), dict) else span.get("end")
    return (
        start if isinstance(start, dict) else None,
        end if isinstance(end, dict) else None,
    )


def _vector_image_url(vector_image: object) -> str | None:
    """`VectorImage` -> absolute URL of the **highest-resolution** artifact."""
    if not isinstance(vector_image, dict):
        return None
    root = _clean(vector_image.get("rootUrl")) or ""
    artifacts = vector_image.get("artifacts")
    if not isinstance(artifacts, list):
        return None

    best_segment: str | None = None
    best_width = -1
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        segment = _clean(artifact.get("fileIdentifyingUrlPathSegment"))
        if not segment:
            continue
        width = _as_int(artifact.get("width")) or _as_int(artifact.get("height")) or 0
        if width >= best_width:
            best_width, best_segment = width, segment

    if not best_segment:
        return None
    if best_segment.startswith("http://") or best_segment.startswith("https://"):
        return best_segment
    if not root:
        return None
    return f"{root.rstrip('/')}/{best_segment.lstrip('/')}"


def _image_url(container: object, _depth: int = 0) -> str | None:
    """Pull an image URL out of any of the several wrappers LinkedIn uses."""
    if _depth > 4:
        return None
    if isinstance(container, str):
        return _clean(container)
    if not isinstance(container, dict):
        return None

    if "artifacts" in container or "rootUrl" in container:
        direct = _vector_image_url(container)
        if direct:
            return direct

    for key in ("displayImageReference", "vectorImage", "image", "picture", "croppedImage"):
        nested = container.get(key)
        if isinstance(nested, (dict, str)):
            found = _image_url(nested, _depth + 1)
            if found:
                return found

    for key in ("displayImageUrl", "url", "rootUrl"):
        maybe = _clean(container.get(key))
        if maybe and maybe.startswith("http"):
            return maybe
    return None


def _proficiency_label(raw: object) -> str | None:
    code = _clean(raw)
    if not code:
        return None
    known = _PROFICIENCY_LABELS.get(code.upper())
    if known:
        return known
    return code.replace("_", " ").capitalize()


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


# =============================================================================
# payload traversal
# =============================================================================


def _containers(raw: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The dicts that may hold `*View` objects, across all three payload shapes."""
    yield raw
    for key in ("data", "profileView"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            yield nested
            deeper = nested.get("profileView")
            if isinstance(deeper, dict):
                yield deeper


def _collect_entities(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every entity in the payload, whichever shape it arrived in."""
    collected: list[dict[str, Any]] = []

    included = raw.get("included")
    if isinstance(included, list):
        collected.extend(item for item in included if isinstance(item, dict))

    # Legacy / non-normalized shape: entities are inline under `<name>View.elements`,
    # and the profile sits at the top level with a `miniProfile` child.
    for container in _containers(raw):
        for key, value in container.items():
            if not isinstance(value, dict):
                continue
            if key.endswith("View"):
                elements = value.get("elements")
                if isinstance(elements, list):
                    collected.extend(item for item in elements if isinstance(item, dict))
            elif key in ("profile", "miniProfile"):
                collected.append(value)
                child = value.get("miniProfile")
                if isinstance(child, dict):
                    collected.append(child)

    # Collapse duplicates by URN, keeping the richest copy but its first position.
    by_urn: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for entity in collected:
        urn = entity.get("entityUrn")
        if not isinstance(urn, str) or not urn:
            anonymous.append(entity)
            continue
        existing = by_urn.get(urn)
        if existing is None or len(entity) > len(existing):
            by_urn[urn] = entity
    return [*by_urn.values(), *anonymous]


def _view_order(raw: dict[str, Any], view_name: str) -> list[str]:
    """The `*elements` URN list for a view — LinkedIn's own ordering, when present."""
    for container in _containers(raw):
        view = container.get(view_name)
        if isinstance(view, dict):
            elements = view.get("*elements")
            if isinstance(elements, list):
                return [urn for urn in elements if isinstance(urn, str)]
    return []


def _apply_order(entities: list[dict[str, Any]], order: list[str]) -> list[dict[str, Any]]:
    if not order:
        return entities
    rank = {urn: index for index, urn in enumerate(order)}
    # `sorted` is stable, so entities absent from the list keep their relative order.
    return sorted(entities, key=lambda e: rank.get(e.get("entityUrn", ""), len(rank)))


def _pick_profile_entity(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """The richest profile-ish entity that actually carries a name."""
    named = [c for c in candidates if _clean(c.get("firstName")) or _clean(c.get("lastName"))]
    pool = named or candidates
    if not pool:
        return {}
    return max(pool, key=len)


# =============================================================================
# section extractors
# =============================================================================


def _extract_location(profile: dict[str, Any], mini: dict[str, Any]) -> Location:
    geo = profile.get("geoLocation")
    dash_geo = None
    if isinstance(geo, dict):
        inner = geo.get("geo")
        if isinstance(inner, dict):
            dash_geo = _clean(inner.get("defaultLocalizedName")) or _clean(inner.get("name"))

    location_name = _clean(profile.get("locationName")) or _clean(mini.get("locationName"))
    geo_location_name = _clean(profile.get("geoLocationName")) or dash_geo

    full = geo_location_name or location_name

    # Only claim a `city` when the string is actually comma-delimited; a bare
    # "San Francisco Bay Area" is a metro, not a city, and guessing would be wrong.
    city = None
    for candidate in (location_name, geo_location_name):
        if candidate and "," in candidate:
            city = candidate.split(",", 1)[0].strip() or None
            break

    country = _clean(profile.get("geoCountryName"))
    if not country:
        basic = profile.get("location")
        if isinstance(basic, dict):
            inner = basic.get("basicLocation")
            source = inner if isinstance(inner, dict) else basic
            code = _clean(source.get("countryCode"))
            if code:
                # LinkedIn sometimes only exposes the ISO-3166 alpha-2 code.
                country = code.upper()

    return Location(city=city, country=country, full=full)


def _extract_experience(
    positions: list[dict[str, Any]], by_urn: dict[str, dict[str, Any]]
) -> list[Experience]:
    out: list[Experience] = []
    for position in positions:
        start, end = _date_bounds(position)

        company = _clean(position.get("companyName"))
        company_urn = position.get("companyUrn") or position.get("*company")
        company_url = None
        if isinstance(company_urn, str) and company_urn:
            mini_company = by_urn.get(company_urn, {})
            universal = _clean(mini_company.get("universalName"))
            company = company or _clean(mini_company.get("name"))
            identifier = universal or _urn_id(company_urn)
            if identifier:
                company_url = f"{LINKEDIN_COMPANY_BASE}{identifier}"

        out.append(
            Experience(
                title=_clean(position.get("title")),
                company=company,
                company_url=company_url,
                location=(
                    _clean(position.get("locationName"))
                    or _clean(position.get("geoLocationName"))
                ),
                start_date=_format_date(start),
                end_date=_format_date(end),
                # An open-ended range means "current" only if there is a start to
                # anchor it; a position with no dates at all asserts nothing.
                is_current=end is None and start is not None,
                description=_clean(position.get("description")),
            )
        )
    return out


def _extract_education(
    educations: list[dict[str, Any]], by_urn: dict[str, dict[str, Any]]
) -> list[Education]:
    out: list[Education] = []
    for education in educations:
        start, end = _date_bounds(education)

        school = _clean(education.get("schoolName"))
        if not school:
            school_urn = education.get("schoolUrn") or education.get("*school")
            if isinstance(school_urn, str):
                school = _clean(by_urn.get(school_urn, {}).get("schoolName"))

        out.append(
            Education(
                school=school,
                degree=_clean(education.get("degreeName")),
                field_of_study=_clean(education.get("fieldOfStudy")),
                start_year=_as_int((start or {}).get("year")),
                end_year=_as_int((end or {}).get("year")),
            )
        )
    return out


def _extract_skills(skills: list[dict[str, Any]]) -> list[str]:
    names = [_clean(skill.get("name")) for skill in skills]
    return _dedupe([name for name in names if name])


def _extract_certifications(certifications: list[dict[str, Any]]) -> list[Certification]:
    out: list[Certification] = []
    for certification in certifications:
        start, end = _date_bounds(certification)
        out.append(
            Certification(
                name=_clean(certification.get("name")),
                authority=_clean(certification.get("authority")),
                url=_clean(certification.get("url")),
                start_date=_format_date(start),
                end_date=_format_date(end),
            )
        )
    return out


def _extract_languages(languages: list[dict[str, Any]]) -> list[Language]:
    return [
        Language(
            name=_clean(language.get("name")),
            proficiency=_proficiency_label(language.get("proficiency")),
        )
        for language in languages
    ]


# =============================================================================
# entrypoint
# =============================================================================


def parse_profile(raw: dict[str, Any], public_id: str) -> Profile:
    """Parse a raw Voyager payload into a `Profile`. Never raises."""
    try:
        return _parse_profile(raw, public_id)
    except Exception as exc:  # pragma: no cover - last-resort safety net
        return Profile(
            public_identifier=public_id or None,
            linkedin_url=f"{LINKEDIN_PROFILE_BASE}{public_id}" if public_id else None,
            warnings=[f"parser failed unexpectedly ({type(exc).__name__}); returned empty profile"],
        )


def _parse_profile(raw: dict[str, Any], public_id: str) -> Profile:
    warnings: list[str] = []

    if not isinstance(raw, dict) or not raw:
        warnings.append("empty upstream payload; no fields could be extracted")
        raw = {}

    def guard(section: str, extract: Callable[[], T], fallback: T) -> T:
        """Run a section extractor; downgrade any failure to a warning."""
        try:
            return extract()
        except Exception as exc:
            warnings.append(f"could not parse {section} ({type(exc).__name__})")
            return fallback

    entities = _collect_entities(raw)
    if not entities:
        warnings.append(
            "no recognizable Voyager entities in payload "
            "(profile may be private, or LinkedIn changed the response shape)"
        )

    by_urn = {
        entity["entityUrn"]: entity
        for entity in entities
        if isinstance(entity.get("entityUrn"), str)
    }

    buckets: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        kind = _classify(entity)
        if kind:
            buckets.setdefault(kind, []).append(entity)

    profile = _pick_profile_entity(buckets.get("profile", []))
    mini = _pick_profile_entity(buckets.get("mini_profile", []))
    if not profile and mini:
        profile = mini

    # --- identity ---
    first_name = _clean(profile.get("firstName")) or _clean(mini.get("firstName"))
    last_name = _clean(profile.get("lastName")) or _clean(mini.get("lastName"))
    full_name = " ".join(part for part in (first_name, last_name) if part) or None

    identifier = (
        _clean(profile.get("publicIdentifier"))
        or _clean(mini.get("publicIdentifier"))
        or _clean(public_id)
    )
    urn = _clean(profile.get("entityUrn")) or _clean(mini.get("entityUrn"))

    headline = _clean(profile.get("headline")) or _clean(mini.get("occupation"))
    about = _clean(profile.get("summary")) or _clean(profile.get("about"))

    if not full_name:
        warnings.append("name not found in payload")
    if not headline:
        warnings.append("headline not found in payload")

    location = guard("location", lambda: _extract_location(profile, mini), Location())
    if not location.full:
        warnings.append("location not found in payload")
    if not about:
        warnings.append("about/summary section is empty or not visible")

    # --- images ---
    picture = guard(
        "profile picture",
        lambda: (
            _image_url(profile.get("profilePicture"))
            or _image_url(mini.get("picture"))
            or _image_url(profile.get("picture"))
        ),
        None,
    )
    background = guard(
        "background image",
        lambda: (
            _image_url(profile.get("backgroundPicture"))
            or _image_url(mini.get("backgroundImage"))
            or _image_url(profile.get("backgroundImage"))
        ),
        None,
    )
    if not picture:
        warnings.append("profile picture not available")

    # --- repeated sections, in LinkedIn's own order ---
    def ordered(kind: str) -> list[dict[str, Any]]:
        return _apply_order(buckets.get(kind, []), _view_order(raw, _KIND_VIEW[kind]))

    experience = guard(
        "experience", lambda: _extract_experience(ordered("position"), by_urn), []
    )
    education = guard(
        "education", lambda: _extract_education(ordered("education"), by_urn), []
    )
    skills = guard("skills", lambda: _extract_skills(ordered("skill")), [])
    certifications = guard(
        "certifications", lambda: _extract_certifications(ordered("certification")), []
    )
    languages = guard("languages", lambda: _extract_languages(ordered("language")), [])

    for name, section in (
        ("experience", experience),
        ("education", education),
        ("skills", skills),
        ("certifications", certifications),
        ("languages", languages),
    ):
        if not section:
            warnings.append(f"{name} section is empty or not visible on this profile")

    return Profile(
        public_identifier=identifier,
        linkedin_url=f"{LINKEDIN_PROFILE_BASE}{identifier}" if identifier else None,
        urn=urn,
        full_name=full_name,
        first_name=first_name,
        last_name=last_name,
        headline=headline,
        location=location,
        about=about,
        profile_picture_url=picture,
        background_image_url=background,
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
        warnings=warnings,
    )
