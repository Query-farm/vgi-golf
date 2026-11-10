"""Small, read-only client for ESPN's golf endpoints and the OWGR rankings backend.

Every outbound request in the package goes through :func:`_get_json`. It adds an
honest User-Agent, retries transient failures with bounded backoff, follows
redirects, and keeps a small in-process response cache so repeated SQL queries
do not re-fetch the same upstream document within its freshness window.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from vgi_golf import __version__

DEFAULT_ESPN_SITE_URL = "https://site.api.espn.com/apis/site/v2/sports/golf"
DEFAULT_ESPN_WEB_URL = "https://site.web.api.espn.com/apis"
DEFAULT_ESPN_CORE_URL = "https://sports.core.api.espn.com/v2/sports/golf"
DEFAULT_OWGR_URL = "https://apiweb.owgr.com/api/owgr"

USER_AGENT = f"vgi-golf/{__version__} (+https://github.com/Query-farm/vgi-golf)"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
MAX_RETRY_DELAY = 30.0
QUERY_TIMEOUT = 120.0
MAX_CONCURRENT_REQUESTS = 4
CACHE_MAX_ENTRIES = 256
STATS_PAGE_SIZE = 500
MAX_STATS_PAGES = 10
OWGR_PAGE_SIZE = 1_000
MAX_OWGR_RANK = 10_000

# Politeness floors (seconds). ESPN's site API advertises ``max-age=1`` for
# scoreboards and leaderboards, which is a CDN hint rather than an invitation
# to poll every second; the floor is the minimum time a response is reused.
LIVE_TTL = 60
SCHEDULE_TTL = 900
STATS_TTL = 300
PROFILE_TTL = 600
RANKINGS_TTL = 600

TOURS: dict[str, dict[str, Any]] = {
    "pga": {
        "name": "PGA Tour",
        "abbreviation": "PGA",
        "espn_league_id": 1106,
        "gender": "men",
        "has_season_stats": True,
    },
    "eur": {
        "name": "DP World Tour",
        "abbreviation": "EUR",
        "espn_league_id": 7002,
        "gender": "men",
        "has_season_stats": False,
    },
    "liv": {
        "name": "LIV Golf",
        "abbreviation": "LIV",
        "espn_league_id": 1109,
        "gender": "men",
        "has_season_stats": False,
    },
    "lpga": {
        "name": "LPGA Tour",
        "abbreviation": "LPGA",
        "espn_league_id": 1107,
        "gender": "women",
        "has_season_stats": False,
    },
    "champions-tour": {
        "name": "PGA Tour Champions",
        "abbreviation": "SGA",
        "espn_league_id": 1105,
        "gender": "men",
        "has_season_stats": False,
    },
    "ntw": {
        "name": "Korn Ferry Tour",
        "abbreviation": "KFT",
        "espn_league_id": 7001,
        "gender": "men",
        "has_season_stats": False,
    },
}

logger = logging.getLogger(__name__)
_request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
_cache_lock = threading.Lock()
_cache: OrderedDict[str, tuple[float, Any, int]] = OrderedDict()

_MAX_AGE = re.compile(r"(?:^|[,\s])max-age=(\d+)", re.IGNORECASE)


class GolfApiError(RuntimeError):
    """A failed response from an upstream golf data source."""

    def __init__(self, source: str, status: int, body: str) -> None:
        super().__init__(f"{source} returned HTTP {status}: {body[:400]}")
        self.status = status


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """A decoded JSON document plus how long it may be reused, in seconds."""

    payload: Any
    ttl: int


def _env_url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


def espn_site_url() -> str:
    """ESPN site API golf root (scoreboards); overridable for hermetic tests."""
    return _env_url("GOLF_ESPN_SITE_URL", DEFAULT_ESPN_SITE_URL)


def espn_web_url() -> str:
    """ESPN site.web API root (leaderboards, statistics, athletes)."""
    return _env_url("GOLF_ESPN_WEB_URL", DEFAULT_ESPN_WEB_URL)


def espn_core_url() -> str:
    """ESPN core API golf root (hole-by-hole linescores)."""
    return _env_url("GOLF_ESPN_CORE_URL", DEFAULT_ESPN_CORE_URL)


def owgr_url() -> str:
    """OWGR backend root (world rankings)."""
    return _env_url("GOLF_OWGR_URL", DEFAULT_OWGR_URL)


def validate_tour(tour: str) -> str:
    """Return a supported tour slug or raise a descriptive error."""
    if tour not in TOURS:
        raise ValueError(f"unknown tour {tour!r}; expected one of {', '.join(TOURS)}")
    return tour


def clear_cache() -> None:
    """Drop every cached upstream response (used by tests)."""
    with _cache_lock:
        _cache.clear()


def _max_age(response: httpx.Response) -> int | None:
    match = _MAX_AGE.search(response.headers.get("cache-control", ""))
    return int(match.group(1)) if match else None


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Return a bounded Retry-After delay or exponential backoff with jitter."""
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = -1.0
            if delay >= 0:
                return min(delay, MAX_RETRY_DELAY)
    base = min(0.25 * 2**attempt, MAX_RETRY_DELAY)
    return min(base + random.uniform(0, base * 0.25), MAX_RETRY_DELAY)


def _request(url: str, params: Mapping[str, str | int], *, client: httpx.Client) -> httpx.Response:
    """Issue one GET, retrying transport errors, 429 and transient 5xx responses."""
    deadline = time.monotonic() + QUERY_TIMEOUT
    for attempt in range(MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"golf API query exceeded {QUERY_TIMEOUT:g} seconds")
        started = time.monotonic()
        try:
            if not _request_slots.acquire(timeout=remaining):
                raise TimeoutError(f"golf API query exceeded {QUERY_TIMEOUT:g} seconds")
            try:
                remaining = max(0.1, deadline - time.monotonic())
                response = client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                    timeout=httpx.Timeout(min(30.0, remaining), connect=min(10.0, remaining)),
                    follow_redirects=True,
                )
            finally:
                _request_slots.release()
        except httpx.TransportError:
            logger.warning("golf_api_transport_error url=%s attempt=%d", url, attempt + 1)
            if attempt + 1 == MAX_ATTEMPTS:
                raise
            time.sleep(min(_retry_delay(None, attempt), max(0.0, deadline - time.monotonic())))
            continue
        logger.debug(
            "golf_api_response url=%s status=%d attempt=%d duration_ms=%d",
            url,
            response.status_code,
            attempt + 1,
            round((time.monotonic() - started) * 1_000),
        )
        if response.status_code not in RETRYABLE_STATUSES or attempt + 1 == MAX_ATTEMPTS:
            return response
        delay = min(_retry_delay(response, attempt), max(0.0, deadline - time.monotonic()))
        logger.warning(
            "golf_api_retry url=%s status=%d attempt=%d delay_seconds=%.3f",
            url,
            response.status_code,
            attempt + 1,
            delay,
        )
        time.sleep(delay)
    raise AssertionError("retry loop exited without a response")


def _get_json(
    source: str,
    url: str,
    params: Mapping[str, str | int],
    *,
    min_ttl: int,
    client: httpx.Client | None = None,
    not_found_ok: bool = False,
) -> ApiResponse:
    """GET a JSON document, serving it from the in-process cache while fresh.

    The reuse window is the larger of the origin's ``max-age`` and ``min_ttl``.
    With ``not_found_ok`` an HTTP 404 decodes as ``None`` instead of raising.
    """
    key = f"{url}?{sorted((str(k), str(v)) for k, v in params.items())}"
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] > now:
            _cache.move_to_end(key)
            return ApiResponse(payload=cached[1], ttl=max(1, int(cached[0] - now)))

    if client is None:
        with httpx.Client(timeout=TIMEOUT) as owned:
            response = _request(url, params, client=owned)
    else:
        response = _request(url, params, client=client)

    if response.status_code == 404 and not_found_ok:
        payload: Any = None
    elif not response.is_success:
        raise GolfApiError(source, response.status_code, response.text)
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"{source} returned a non-JSON response") from exc

    ttl = max(_max_age(response) or 0, min_ttl)
    with _cache_lock:
        _cache[key] = (time.monotonic() + ttl, payload, ttl)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
    return ApiResponse(payload=payload, ttl=ttl)


def _require_dict(payload: Any, source: str, what: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{source} {what} response is not a JSON object")
    return payload


def fetch_scoreboard(
    tour: str, *, dates: str | None = None, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch a tour scoreboard: the current event, or every event in ``dates`` (YYYY or YYYYMMDD)."""
    validate_tour(tour)
    params: dict[str, str | int] = {}
    if dates is not None:
        params["dates"] = dates
    response = _get_json(
        "ESPN",
        f"{espn_site_url()}/{tour}/scoreboard",
        params,
        min_ttl=SCHEDULE_TTL if dates and len(dates) == 4 else LIVE_TTL,
        client=client,
    )
    payload = _require_dict(response.payload, "ESPN", "scoreboard")
    if not isinstance(payload.get("events"), list):
        raise ValueError("ESPN scoreboard response has no events array")
    return response


def fetch_leaderboard(tour: str, event_id: int, *, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch one tournament's full leaderboard from ESPN's site.web API.

    The site scoreboard ignores ``?event=``, so the site.web leaderboard is the
    only keyless route to an arbitrary event.
    """
    validate_tour(tour)
    if event_id <= 0:
        raise ValueError("event_id must be positive")
    response = _get_json(
        "ESPN",
        f"{espn_web_url()}/site/v2/sports/golf/leaderboard",
        {"league": tour, "event": event_id},
        min_ttl=LIVE_TTL,
        client=client,
    )
    payload = _require_dict(response.payload, "ESPN", "leaderboard")
    if not isinstance(payload.get("events"), list):
        raise ValueError("ESPN leaderboard response has no events array")
    return response


def fetch_linescores(
    tour: str, event_id: int, player_id: int, *, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch one player's round and hole-by-hole scores for an event (core API)."""
    validate_tour(tour)
    if event_id <= 0 or player_id <= 0:
        raise ValueError("event_id and player_id must be positive")
    response = _get_json(
        "ESPN",
        f"{espn_core_url()}/leagues/{tour}/events/{event_id}/competitions/{event_id}"
        f"/competitors/{player_id}/linescores",
        {"limit": 25},
        min_ttl=LIVE_TTL,
        client=client,
        not_found_ok=True,
    )
    if response.payload is None:
        return ApiResponse(payload={"items": []}, ttl=response.ttl)
    payload = _require_dict(response.payload, "ESPN", "linescores")
    if not isinstance(payload.get("items", []), list):
        raise ValueError("ESPN linescores response has a malformed items array")
    return response


def fetch_season_stats(season: int | None = None, *, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch every PGA Tour player's season statistics, consuming all pages.

    The returned payload is the first page with ``athletes`` replaced by the
    concatenation of every page. Players are ordered by FedExCup points.
    """
    base: dict[str, str | int] = {"sort": "general.cupPoints:desc", "limit": STATS_PAGE_SIZE}
    if season is not None:
        base["season"] = season
    url = f"{espn_web_url()}/common/v3/sports/golf/pga/statistics/byathlete"
    first: dict[str, Any] | None = None
    athletes: list[Any] = []
    ttl: int | None = None
    for page in range(1, MAX_STATS_PAGES + 1):
        response = _get_json("ESPN", url, {**base, "page": page}, min_ttl=STATS_TTL, client=client)
        payload = _require_dict(response.payload, "ESPN", "statistics")
        ttl = response.ttl if ttl is None else min(ttl, response.ttl)
        if first is None:
            first = payload
        page_athletes = payload.get("athletes", [])
        if not isinstance(page_athletes, list):
            raise ValueError("ESPN statistics response has a malformed athletes array")
        athletes.extend(page_athletes)
        pagination = payload.get("pagination")
        pages = pagination.get("pages") if isinstance(pagination, dict) else None
        short_page = len(page_athletes) < STATS_PAGE_SIZE
        if short_page or not isinstance(pages, int) or page >= pages:
            return ApiResponse(payload={**first, "athletes": athletes}, ttl=ttl)
    raise RuntimeError(f"ESPN statistics pagination exceeded {MAX_STATS_PAGES} pages")


def fetch_athlete(player_id: int, *, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch one golfer's ESPN profile; a 404 decodes as an empty payload.

    ESPN golf athlete ids are shared across tours, so the PGA path serves
    LPGA, LIV and DP World players too.
    """
    if player_id <= 0:
        raise ValueError("player_id must be positive")
    response = _get_json(
        "ESPN",
        f"{espn_web_url()}/common/v3/sports/golf/pga/athletes/{player_id}",
        {},
        min_ttl=PROFILE_TTL,
        client=client,
        not_found_ok=True,
    )
    if response.payload is None:
        return ApiResponse(payload={}, ttl=response.ttl)
    _require_dict(response.payload, "ESPN", "athlete")
    return response


def fetch_world_rankings(
    max_rank: int = OWGR_PAGE_SIZE, *, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch the current Official World Golf Ranking down to ``max_rank``."""
    if not 1 <= max_rank <= MAX_OWGR_RANK:
        raise ValueError(f"max_rank must be between 1 and {MAX_OWGR_RANK}")
    page_size = min(max_rank, OWGR_PAGE_SIZE)
    rows: list[Any] = []
    ttl: int | None = None
    page = 1
    while len(rows) < max_rank:
        response = _get_json(
            "OWGR",
            f"{owgr_url()}/rankings/getRankings",
            {"pageSize": page_size, "pageNumber": page, "regionId": 0, "sortString": "Rank ASC"},
            min_ttl=RANKINGS_TTL,
            client=client,
        )
        payload = _require_dict(response.payload, "OWGR", "rankings")
        ttl = response.ttl if ttl is None else min(ttl, response.ttl)
        page_rows = payload.get("rankingsList")
        if not isinstance(page_rows, list):
            raise ValueError("OWGR rankings response has no rankingsList array")
        rows.extend(page_rows)
        total_pages = payload.get("totalNumberOfPages")
        short_page = len(page_rows) < page_size
        if short_page or not isinstance(total_pages, int) or page >= total_pages:
            break
        page += 1
    return ApiResponse(payload=rows[:max_rank], ttl=ttl or RANKINGS_TTL)
