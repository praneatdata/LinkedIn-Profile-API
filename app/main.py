"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__

app = FastAPI(
    title="LinkedIn Profile API",
    version=__version__,
    description=(
        "Fetches a public LinkedIn profile and returns clean structured JSON.\n\n"
        "Backed by direct HTTP calls to LinkedIn's internal Voyager API — no browser, "
        "no Selenium/Playwright/Puppeteer."
    ),
)


@app.get("/health", tags=["meta"], summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok"}
