"""Opt-in smoke tests against the public ESPN golf endpoints and OWGR."""

from __future__ import annotations

from datetime import date

import pytest

from vgi_golf import api, schemas
from vgi_golf.tournaments import current_leaderboard_rows, schedule_rows

pytestmark = pytest.mark.live


def test_masters_2026_final_leaderboard() -> None:
    rows = schemas.flatten_leaderboard(api.fetch_leaderboard("pga", 401811941).payload, "pga")
    assert len(rows) >= 85
    winner = rows[0]
    assert (winner["player_name"], winner["score_to_par"], winner["total_strokes"]) == (
        "Rory McIlroy",
        -12,
        276,
    )
    assert rows[1]["player_name"] == "Scottie Scheffler"
    assert all(row["event_completed"] for row in rows)


def test_masters_2026_scorecard_sums_to_round_totals() -> None:
    payload = api.fetch_linescores("pga", 401811941, 9478).payload
    rows = schemas.flatten_scorecard(payload, tour="pga", event_id=401811941, player_id=9478)
    assert len(rows) == 72
    totals: dict[int, int] = {}
    for row in rows:
        totals[row["round"]] = totals.get(row["round"], 0) + row["strokes"]
    assert totals == {1: 70, 2: 74, 3: 65, 4: 68}


def test_pga_2025_schedule_has_known_winners() -> None:
    rows, _ttl = schedule_rows("pga", 2025)
    by_name = {row["event_name"]: row for row in rows}
    assert len(rows) >= 40
    assert by_name["Masters Tournament"]["winner_name"] == "Rory McIlroy"
    assert by_name["Masters Tournament"]["start_date"] == date(2025, 4, 10)
    assert by_name["Ryder Cup"]["winner_name"] == "EUR"
    # Playoff wins report the regulation score, whether or not ESPN's raw
    # score already folded in the playoff holes.
    assert by_name["Masters Tournament"]["winning_score_to_par"] == -11
    assert by_name["Masters Tournament"]["playoff"] is True
    assert by_name["Sony Open in Hawaii"]["winning_score_to_par"] == -16
    assert by_name["TOUR Championship"]["playoff"] is False


def test_season_stats_never_report_zero_averages() -> None:
    rows = schemas.flatten_season_stats(api.fetch_season_stats(2026).payload)
    assert all(row["scoring_average"] is None or row["scoring_average"] > 55 for row in rows)
    assert all(row["driving_distance"] is None or row["driving_distance"] > 200 for row in rows)


def test_pga_2025_season_stats() -> None:
    rows = schemas.flatten_season_stats(api.fetch_season_stats(2025).payload)
    assert len(rows) >= 200
    leader = min(rows, key=lambda row: row["fedex_cup_rank"] or 10_000)
    assert leader["player_name"] == "Scottie Scheffler"
    assert leader["fedex_cup_points"] == 7456.0
    assert len({row["player_id"] for row in rows}) == len(rows)


@pytest.mark.parametrize("tour", list(api.TOURS))
def test_every_tour_has_a_current_leaderboard_and_schedule(tour: str) -> None:
    rows, _ttl = current_leaderboard_rows(tour)
    assert rows and all(row["tour"] == tour for row in rows)
    schedule, _ttl = schedule_rows(tour, None)
    assert len(schedule) >= 10


def test_liv_indianapolis_2026() -> None:
    rows = schemas.flatten_leaderboard(api.fetch_leaderboard("liv", 401811430).payload, "liv")
    assert rows[0]["event_name"] == "LIV Golf Indianapolis"
    assert len(rows) >= 50


def test_player_profile() -> None:
    [row] = schemas.flatten_player(api.fetch_athlete(9478).payload)
    assert row["birth_date"] == date(1996, 6, 21)
    assert schemas.flatten_player(api.fetch_athlete(999_999_999).payload) == []


def test_world_rankings_top_1000() -> None:
    rows = schemas.flatten_world_rankings(api.fetch_world_rankings(1_000).payload)
    assert len(rows) == 1_000
    assert rows[0]["rank"] == 1
    assert len({row["owgr_player_id"] for row in rows}) == 1_000
