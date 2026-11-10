"""Table-function processing against stubbed API responses."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pytest
from conftest import load

from vgi_golf import api, players, rankings, tournaments
from vgi_golf.tournaments import select_current_event_ids


class _Out:
    def __init__(self) -> None:
        self.batches: list[pa.RecordBatch] = []
        self.cache: list[Any] = []
        self.finished = False

    def emit(self, batch: pa.RecordBatch, *, cache_control: Any = None) -> None:
        self.batches.append(batch)
        self.cache.append(cache_control)

    def finish(self) -> None:
        self.finished = True


def _run(function: Any, schema: pa.Schema, **args: Any) -> _Out:
    out = _Out()
    params = SimpleNamespace(args=SimpleNamespace(**args) if args else None, output_schema=schema)
    function.process(params, None, out)
    assert out.finished and len(out.batches) == 1
    return out


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"scoreboard": [], "leaderboard": []}

    def scoreboard(tour: str, *, dates: str | None = None) -> api.ApiResponse:
        calls["scoreboard"].append((tour, dates))
        name = "scoreboard_pga_2025.json" if dates else "scoreboard_pga_current.json"
        return api.ApiResponse(payload=load(name), ttl=60)

    def leaderboard(tour: str, event_id: int) -> api.ApiResponse:
        calls["leaderboard"].append((tour, event_id))
        return api.ApiResponse(payload=load("leaderboard_pga_masters_2026.json"), ttl=90)

    monkeypatch.setattr(api, "fetch_scoreboard", scoreboard)
    monkeypatch.setattr(api, "fetch_leaderboard", leaderboard)
    monkeypatch.setattr(
        api,
        "fetch_linescores",
        lambda tour, event_id, player_id: api.ApiResponse(load("linescores_masters_scheffler.json"), 657),
    )
    monkeypatch.setattr(
        api, "fetch_season_stats", lambda season=None: api.ApiResponse(load("stats_pga_2026.json"), 300)
    )
    monkeypatch.setattr(
        api, "fetch_athlete", lambda player_id: api.ApiResponse(load("athlete_scheffler.json"), 600)
    )
    monkeypatch.setattr(
        api,
        "fetch_world_rankings",
        lambda max_rank: api.ApiResponse(load("owgr_rankings_top5.json")["rankingsList"][:max_rank], 600),
    )
    return calls


def test_current_event_falls_back_to_previous_calendar_event() -> None:
    # The recorded scoreboard shows the upcoming Austin Championship; the table should show
    # the World Wide Technology Championship that finished the week before.
    ids, active = select_current_event_ids(load("scoreboard_pga_current.json"))
    assert not active
    assert ids == [401850979, 401850978, 401850917, 401850980]


def test_current_event_prefers_in_progress_events() -> None:
    payload = {"events": [{"id": "5", "status": {"type": {"state": "in"}}}, {"id": "6"}]}
    assert select_current_event_ids(payload) == ([5], True)


def test_canceled_event_is_not_current() -> None:
    payload = {
        "events": [
            {
                "id": "8",
                "date": "2026-08-30T04:00Z",
                "status": {"type": {"state": "post", "name": "STATUS_CANCELED"}},
            }
        ],
        "leagues": [
            {
                "calendar": [
                    {"id": "7", "startDate": "2026-08-27T07:00Z"},
                    {"id": "8", "startDate": "2026-08-30T07:00Z"},
                ]
            }
        ],
    }
    assert select_current_event_ids(payload) == ([7, 8], False)


def test_current_event_without_history_returns_upcoming_field() -> None:
    payload = {"events": [{"id": "9", "date": "2026-01-08T05:00Z", "status": {"type": {"state": "pre"}}}]}
    assert select_current_event_ids(payload) == ([9], False)
    calendar_only = {"events": [], "leagues": [{"calendar": [{"id": "3", "startDate": "2026-01-01T00:00Z"}]}]}
    assert select_current_event_ids(calendar_only) == ([3], False)


def test_current_leaderboard_skips_empty_fallbacks(monkeypatch: pytest.MonkeyPatch, stub_api) -> None:
    def leaderboard(tour: str, event_id: int) -> api.ApiResponse:
        stub_api["leaderboard"].append((tour, event_id))
        if event_id == 401850979:
            return api.ApiResponse(payload={"events": []}, ttl=60)
        return api.ApiResponse(payload=load("leaderboard_pga_masters_2026.json"), ttl=60)

    monkeypatch.setattr(api, "fetch_leaderboard", leaderboard)
    rows, _ttl = tournaments.current_leaderboard_rows("pga")
    assert len(rows) == 8
    assert stub_api["leaderboard"] == [("pga", 401850979), ("pga", 401850978)]


def test_current_leaderboard(stub_api) -> None:
    out = _run(tournaments.CurrentLeaderboardFunction, tournaments.LEADERBOARD_SCHEMA)
    assert out.batches[0].num_rows == 8
    assert stub_api["leaderboard"] == [("pga", 401850979)]
    assert out.cache[0].ttl == 60


def test_leaderboard_with_and_without_event(stub_api) -> None:
    projected = pa.schema([tournaments.LEADERBOARD_SCHEMA.field("player_name")])
    out = _run(tournaments.LeaderboardFunction, projected, tour="pga", event_id=401811941)
    assert out.batches[0].schema.names == ["player_name"]
    assert out.batches[0].column(0)[0].as_py() == "Rory McIlroy"
    _run(tournaments.LeaderboardFunction, tournaments.LEADERBOARD_SCHEMA, tour="pga", event_id=None)
    assert stub_api["leaderboard"] == [("pga", 401811941), ("pga", 401850979)]


def test_schedules(stub_api) -> None:
    out = _run(tournaments.CurrentScheduleFunction, tournaments.SCHEDULE_SCHEMA)
    assert out.batches[0].num_rows == 3
    assert stub_api["scoreboard"] == [("pga", None), ("pga", "2026")]
    _run(tournaments.ScheduleFunction, tournaments.SCHEDULE_SCHEMA, tour="lpga", season=2025)
    assert stub_api["scoreboard"][-1] == ("lpga", "2025")


def test_current_season_requires_a_year(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api, "fetch_scoreboard", lambda tour: api.ApiResponse({"events": [], "season": {"year": 2027}}, 60)
    )
    assert tournaments.current_season("pga") == 2027
    monkeypatch.setattr(api, "fetch_scoreboard", lambda tour: api.ApiResponse({"events": []}, 60))
    with pytest.raises(ValueError, match="current season"):
        tournaments.current_season("pga")


def test_scorecard_course_and_tours(stub_api) -> None:
    out = _run(
        tournaments.PlayerScorecardFunction,
        tournaments.SCORECARD_SCHEMA,
        event_id=401811941,
        player_id=9478,
        tour="pga",
    )
    assert out.batches[0].num_rows == 72
    out = _run(
        tournaments.CourseHolesFunction, tournaments.COURSE_HOLES_SCHEMA, event_id=401811941, tour="pga"
    )
    assert out.batches[0].num_rows == 18
    out = _run(tournaments.ToursFunction, tournaments.TOURS_SCHEMA)
    assert out.batches[0].num_rows == 6


def test_player_functions(stub_api) -> None:
    assert _run(players.CurrentPlayerStatsFunction, players.SEASON_STATS_SCHEMA).batches[0].num_rows == 4
    assert (
        _run(players.SeasonPlayerStatsFunction, players.SEASON_STATS_SCHEMA, season=2026).batches[0].num_rows
        == 4
    )
    assert _run(players.PlayerFunction, players.PLAYER_SCHEMA, player_id=9478).batches[0].num_rows == 1


def test_ranking_functions(stub_api) -> None:
    assert (
        _run(rankings.CurrentWorldRankingsFunction, rankings.WORLD_RANKINGS_SCHEMA).batches[0].num_rows == 5
    )
    assert (
        _run(rankings.WorldRankingsFunction, rankings.WORLD_RANKINGS_SCHEMA, max_rank=2).batches[0].num_rows
        == 2
    )


@pytest.mark.parametrize(
    "function",
    [*tournaments.TOURNAMENT_FUNCTIONS, *players.PLAYER_FUNCTIONS, *rankings.RANKING_FUNCTIONS],
)
def test_cardinality_hints(function) -> None:
    hint = function.cardinality(None)
    assert hint.estimate is not None and hint.max is not None and hint.estimate <= hint.max
