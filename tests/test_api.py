"""Tests for the HTTP boundary: URLs, headers, retries, caching and pagination."""

from __future__ import annotations

import httpx
import pytest
from conftest import load

from vgi_golf import __version__, api


@pytest.fixture(autouse=True)
def _hermetic_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLF_ESPN_SITE_URL", "https://site.test/golf/")
    monkeypatch.setenv("GOLF_ESPN_WEB_URL", "https://web.test/apis")
    monkeypatch.setenv("GOLF_ESPN_CORE_URL", "https://core.test/golf")
    monkeypatch.setenv("GOLF_OWGR_URL", "https://owgr.test/api/owgr")
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scoreboard_url_headers_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=load("scoreboard_pga_current.json"), headers={"Cache-Control": "max-age=1"}
        )

    with _client(handler) as client:
        first = api.fetch_scoreboard("pga", client=client)
        second = api.fetch_scoreboard("pga", client=client)

    assert len(calls) == 1, "second call must be served from the in-process cache"
    request = calls[0]
    assert str(request.url) == "https://site.test/golf/pga/scoreboard"
    assert (
        request.headers["User-Agent"] == f"vgi-golf/{__version__} (+https://github.com/Query-farm/vgi-golf)"
    )
    assert request.headers["Accept"] == "application/json"
    assert first.ttl == api.LIVE_TTL, "politeness floor beats ESPN's max-age=1"
    assert second.payload == first.payload


def test_season_scoreboard_uses_longer_floor_and_honours_longer_max_age() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["dates"] == "2025"
        return httpx.Response(200, json={"events": []}, headers={"Cache-Control": "public, max-age=4000"})

    with _client(handler) as client:
        assert api.fetch_scoreboard("pga", dates="2025", client=client).ttl == 4000


def test_unknown_tour_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown tour"):
        api.fetch_scoreboard("nba")


def test_leaderboard_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/apis/site/v2/sports/golf/leaderboard"
        assert request.url.params["league"] == "liv"
        assert request.url.params["event"] == "401811430"
        return httpx.Response(200, json=load("leaderboard_liv_indianapolis_2026.json"))

    with _client(handler) as client:
        response = api.fetch_leaderboard("liv", 401811430, client=client)
    assert response.payload["events"][0]["name"] == "LIV Golf Indianapolis"
    with pytest.raises(ValueError, match="positive"):
        api.fetch_leaderboard("pga", 0)


def test_retries_transient_failures_then_succeeds() -> None:
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, json={"events": []})
        return httpx.Response(status, headers={"Retry-After": "1"})

    with _client(handler) as client:
        api.fetch_leaderboard("pga", 1, client=client)


def test_retry_after_parsing() -> None:
    assert api._retry_delay(httpx.Response(429, headers={"Retry-After": "2"}), 0) == 2.0
    assert api._retry_delay(httpx.Response(429, headers={"Retry-After": "999"}), 0) == api.MAX_RETRY_DELAY
    http_date = api._retry_delay(
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), 0
    )
    assert 0 <= http_date <= api.MAX_RETRY_DELAY
    assert 0.25 <= api._retry_delay(httpx.Response(429, headers={"Retry-After": "soon"}), 0) <= 0.3125
    assert api._retry_delay(None, 20) == api.MAX_RETRY_DELAY


def test_persistent_failure_is_descriptive() -> None:
    with (
        _client(lambda request: httpx.Response(500, text="upstream down")) as client,
        pytest.raises(api.GolfApiError, match="ESPN returned HTTP 500.*upstream down"),
    ):
        api.fetch_leaderboard("pga", 1, client=client)


def test_transport_errors_retry_then_raise() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("refused", request=request)

    with _client(handler) as client, pytest.raises(httpx.ConnectError):
        api.fetch_leaderboard("pga", 1, client=client)
    assert attempts == api.MAX_ATTEMPTS


def test_malformed_payloads_are_rejected() -> None:
    with _client(lambda request: httpx.Response(200, json={"nope": 1})) as client:
        with pytest.raises(ValueError, match="no events array"):
            api.fetch_scoreboard("pga", client=client)
        with pytest.raises(ValueError, match="no events array"):
            api.fetch_leaderboard("pga", 5, client=client)
        with pytest.raises(ValueError, match="no rankingsList"):
            api.fetch_world_rankings(5, client=client)
    with (
        _client(lambda request: httpx.Response(200, json=[1, 2])) as client,
        pytest.raises(ValueError, match="not a JSON object"),
    ):
        api.fetch_athlete(1, client=client)
    with (
        _client(lambda request: httpx.Response(200, text="<html>")) as client,
        pytest.raises(ValueError, match="non-JSON"),
    ):
        api.fetch_athlete(2, client=client)


def test_not_found_athlete_and_linescores_are_empty() -> None:
    with _client(lambda request: httpx.Response(404, json={"code": 404})) as client:
        assert api.fetch_athlete(999, client=client).payload == {}
        assert api.fetch_linescores("pga", 1, 2, client=client).payload == {"items": []}
    with pytest.raises(ValueError, match="positive"):
        api.fetch_athlete(0)
    with pytest.raises(ValueError, match="positive"):
        api.fetch_linescores("pga", 1, 0)


def test_linescores_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/golf/leagues/pga/events/401811941/competitions/401811941/competitors/9478/linescores"
        )
        return httpx.Response(200, json=load("linescores_masters_scheffler.json"))

    with _client(handler) as client:
        assert len(api.fetch_linescores("pga", 401811941, 9478, client=client).payload["items"]) == 4


def test_short_pages_end_pagination_even_if_page_counts_lie() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if "pageNumber" in request.url.params:
            return httpx.Response(200, json={**load("owgr_rankings_top5.json"), "totalNumberOfPages": 1905})
        return httpx.Response(200, json={**load("stats_pga_2026.json"), "pagination": {"pages": 99}})

    with _client(handler) as client:
        assert len(api.fetch_world_rankings(1_000, client=client).payload) == 5
        assert len(api.fetch_season_stats(2026, client=client).payload["athletes"]) == 4
    assert calls == 2


def test_season_stats_follow_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "STATS_PAGE_SIZE", 2)
    pages: list[str] = []
    stats = load("stats_pga_2026.json")

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        pages.append(page)
        assert request.url.params["sort"] == "general.cupPoints:desc"
        assert request.url.params["season"] == "2026"
        body = {**stats, "pagination": {"pages": 2}}
        body["athletes"] = stats["athletes"][:2] if page == "1" else stats["athletes"][2:]
        return httpx.Response(200, json=body, headers={"Cache-Control": "max-age=300"})

    with _client(handler) as client:
        response = api.fetch_season_stats(2026, client=client)
    assert pages == ["1", "2"]
    assert len(response.payload["athletes"]) == 4
    assert response.ttl == api.STATS_TTL


def test_season_stats_without_athletes_returns_empty() -> None:
    with _client(lambda request: httpx.Response(200, json={"currentSeason": {"year": 2026}})) as client:
        assert api.fetch_season_stats(1990, client=client).payload["athletes"] == []


def test_world_rankings_paginate_and_truncate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "OWGR_PAGE_SIZE", 3)
    rankings = load("owgr_rankings_top5.json")["rankingsList"]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageNumber"])
        assert request.url.params["pageSize"] == "3"
        assert request.url.params["sortString"] == "Rank ASC"
        rows = rankings[(page - 1) * 3 : page * 3]
        return httpx.Response(200, json={"rankingsList": rows, "totalNumberOfPages": 2})

    with _client(handler) as client:
        assert [row["rank"] for row in api.fetch_world_rankings(4, client=client).payload] == [1, 2, 3, 4]
    with pytest.raises(ValueError, match="max_rank"):
        api.fetch_world_rankings(0)


def test_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "CACHE_MAX_ENTRIES", 2)
    with _client(lambda request: httpx.Response(200, json={"events": []})) as client:
        for event_id in (1, 2, 3):
            api.fetch_leaderboard("pga", event_id, client=client)
    assert len(api._cache) == 2


def test_default_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOLF_ESPN_SITE_URL", "GOLF_ESPN_WEB_URL", "GOLF_ESPN_CORE_URL", "GOLF_OWGR_URL"):
        monkeypatch.delenv(name)
    assert api.espn_site_url() == "https://site.api.espn.com/apis/site/v2/sports/golf"
    assert api.espn_web_url() == "https://site.web.api.espn.com/apis"
    assert api.espn_core_url() == "https://sports.core.api.espn.com/v2/sports/golf"
    assert api.owgr_url() == "https://apiweb.owgr.com/api/owgr"
