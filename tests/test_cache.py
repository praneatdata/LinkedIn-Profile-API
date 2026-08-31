"""Profile cache tests."""

from __future__ import annotations

import threading

from app.cache import ProfileCache
from app.models import Profile


def _profile(name: str) -> Profile:
    return Profile(public_identifier=name, full_name=name)


def test_set_then_get_round_trips() -> None:
    cache = ProfileCache(ttl_seconds=60)
    cache.set("jane", _profile("jane"))
    got = cache.get("jane")
    assert got is not None and got.full_name == "jane"


def test_miss_returns_none() -> None:
    assert ProfileCache(ttl_seconds=60).get("nobody") is None


def test_keys_are_case_and_whitespace_insensitive() -> None:
    cache = ProfileCache(ttl_seconds=60)
    cache.set("Jane-Doe", _profile("jane"))
    assert cache.get("jane-doe") is not None
    assert cache.get("  JANE-DOE  ") is not None


def test_entries_expire_after_the_ttl() -> None:
    now = [1_000.0]
    cache = ProfileCache(ttl_seconds=60, timer=lambda: now[0])

    cache.set("jane", _profile("jane"))
    assert cache.get("jane") is not None

    now[0] += 59  # still inside the window
    assert cache.get("jane") is not None

    now[0] += 2  # 61s elapsed
    assert cache.get("jane") is None
    assert cache.stats()["misses"] == 1


def test_eviction_respects_max_entries() -> None:
    cache = ProfileCache(ttl_seconds=60, max_entries=2)
    for name in ("a", "b", "c"):
        cache.set(name, _profile(name))
    assert cache.stats()["entries"] == 2


def test_stats_track_hits_and_misses() -> None:
    cache = ProfileCache(ttl_seconds=60)
    cache.set("jane", _profile("jane"))
    cache.get("jane")
    cache.get("jane")
    cache.get("absent")

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["entries"] == 1
    assert stats["ttl_seconds"] == 60


def test_clear_empties_the_cache_and_stats() -> None:
    cache = ProfileCache(ttl_seconds=60)
    cache.set("jane", _profile("jane"))
    cache.get("jane")
    cache.clear()
    assert cache.get("jane") is None
    assert cache.stats()["entries"] == 0


def test_zero_ttl_is_clamped_rather_than_crashing() -> None:
    """cachetools rejects ttl=0; a misconfigured env must not take the app down."""
    cache = ProfileCache(ttl_seconds=0)
    cache.set("jane", _profile("jane"))
    assert cache.stats()["entries"] == 1


def test_concurrent_access_is_safe() -> None:
    cache = ProfileCache(ttl_seconds=60, max_entries=4096)
    errors: list[BaseException] = []

    def worker(start: int) -> None:
        try:
            for i in range(start, start + 200):
                cache.set(f"slug-{i}", _profile(f"slug-{i}"))
                cache.get(f"slug-{i}")
                cache.stats()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n * 200,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert cache.stats()["entries"] == 1600
