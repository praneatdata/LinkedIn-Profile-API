"""The public response contract.

Every field is nullable and every list defaults to empty: LinkedIn gates different
sections on different profiles, and a partial profile must degrade into `warnings[]`
rather than a 500.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    country: str | None = None
    full: str | None = None


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    company_url: str | None = None
    location: str | None = None
    start_date: str | None = None  # "YYYY-MM" (or "YYYY" when only a year is given)
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_year: int | None = None
    end_year: int | None = None


class Certification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    authority: str | None = None
    url: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class Language(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    proficiency: str | None = None


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_identifier: str | None = None
    linkedin_url: str | None = None
    urn: str | None = None
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    headline: str | None = None
    location: Location = Field(default_factory=Location)
    about: str | None = None
    profile_picture_url: str | None = None
    background_image_url: str | None = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    # Partial-extraction notes. Populated instead of raising, never a hard failure.
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorResponse(BaseModel):
    detail: str
