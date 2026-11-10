"""Parsing rules exercised against trimmed real ESPN and OWGR responses."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest
from conftest import load

from vgi_golf import schemas
from vgi_golf.meta import comment_of, result_columns_schema, sql_type

ALL_SCHEMAS = [
    schemas.TOURS_SCHEMA,
    schemas.LEADERBOARD_SCHEMA,
    schemas.SCHEDULE_SCHEMA,
    schemas.SCORECARD_SCHEMA,
    schemas.COURSE_HOLES_SCHEMA,
    schemas.SEASON_STATS_SCHEMA,
    schemas.PLAYER_SCHEMA,
    schemas.WORLD_RANKINGS_SCHEMA,
]


@pytest.mark.parametrize("schema", ALL_SCHEMAS)
def test_every_column_is_commented_and_typed(schema: pa.Schema) -> None:
    assert all(comment_of(value) for value in schema)
    assert len(result_columns_schema(schema)) > 10


def test_sql_type_rejects_unknown_types() -> None:
    assert sql_type(pa.int64()) == "BIGINT"
    with pytest.raises(ValueError, match="Unsupported"):
        sql_type(pa.list_(pa.int32()))


def test_masters_leaderboard() -> None:
    rows = schemas.flatten_leaderboard(load("leaderboard_pga_masters_2026.json"), "pga")
    assert [row["sort_order"] for row in rows] == sorted(row["sort_order"] for row in rows)
    winner = rows[0]
    assert winner["player_name"] == "Rory McIlroy"
    assert winner["player_id"] == 3470
    assert winner["score_to_par"] == -12
    assert (winner["round_1"], winner["round_2"], winner["round_3"], winner["round_4"]) == (67, 65, 73, 71)
    assert winner["total_strokes"] == 276
    assert winner["rounds_played"] == 4
    assert winner["earnings"] == Decimal("4500000.00")
    assert winner["fedex_cup_points"] == 750.0
    assert winner["event_completed"] is True
    assert winner["season"] == 2026
    assert winner["position_number"] == 1
    assert winner["tee_time"] == datetime(2026, 4, 12, 18, 25, tzinfo=UTC)
    tied = rows[2]
    assert tied["position"] == "T3" and tied["position_number"] == 3 and tied["is_tied"] is True
    cut = [row for row in rows if row["status_code"] == "STATUS_CUT"]
    assert cut and all(row["round_3"] is None and row["rounds_played"] == 2 for row in cut)
    assert all(row["position_number"] is None for row in cut)
    batch = schemas.batch_from_rows(rows, schemas.LEADERBOARD_SCHEMA)
    assert batch.num_rows == len(rows) == 8


def test_team_event_entries_have_no_player_id() -> None:
    rows = schemas.flatten_leaderboard(load("leaderboard_pga_zurich_2026.json"), "pga")
    assert rows[0]["entry_type"] == "team"
    assert rows[0]["player_id"] is None
    assert rows[0]["player_name"] == "M. Fitzpatrick / A. Fitzpatrick"
    assert rows[0]["score_to_par"] == -31


def test_liv_leaderboard_has_no_fedex_points() -> None:
    rows = schemas.flatten_leaderboard(load("leaderboard_liv_indianapolis_2026.json"), "liv")
    assert rows[0]["tour"] == "liv"
    assert rows[0]["score_to_par"] == -18
    assert all(row["fedex_cup_points"] is None for row in rows)


def test_scheduled_event_has_null_scores() -> None:
    payload = {
        "events": [
            {
                "id": "1",
                "status": {"type": {"state": "pre", "completed": False, "description": "Scheduled"}},
                "competitions": [
                    {
                        "competitors": [
                            {
                                "id": "7",
                                "sortOrder": 1,
                                "athlete": {"id": "7", "displayName": "A Golfer"},
                                "score": {"value": 0.0, "displayValue": "E"},
                                "status": {"type": {"state": "pre"}},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    [row] = schemas.flatten_leaderboard(payload, "pga")
    assert row["score_to_par"] is None and row["score_to_par_display"] is None
    assert row["total_strokes"] is None


def test_course_holes() -> None:
    rows = schemas.flatten_course_holes(load("leaderboard_pga_masters_2026.json"), "pga")
    assert len(rows) == 18
    assert sum(row["par"] for row in rows) == 72 == rows[0]["course_par"]
    assert rows[0]["course_name"] == "Augusta National Golf Club"
    assert {row["hole"] for row in rows if row["par"] == 3} == {4, 6, 12, 16}


def test_schedule_winners_including_team_and_cup_events() -> None:
    rows = schemas.flatten_schedule(load("scoreboard_pga_2025.json"), "pga")
    by_name = {row["event_name"]: row for row in rows}
    assert [row["start_date"] for row in rows] == sorted(row["start_date"] for row in rows)
    valero = by_name["Valero Texas Open"]
    assert valero["winner_name"] == "Brian Harman"
    assert valero["winning_score_to_par"] == -9
    assert valero["start_date"] == date(2025, 4, 3)
    assert valero["completed"] is True and valero["state"] == "post"
    assert by_name["Zurich Classic of New Orleans"]["winner_name"] == "Novak/Griffin"
    ryder = by_name["Ryder Cup"]
    assert ryder["winner_name"] == "EUR"
    assert ryder["winning_score"] == "15"
    assert ryder["winning_score_to_par"] is None


def test_upcoming_event_has_no_winner() -> None:
    [row] = schemas.flatten_schedule(load("scoreboard_pga_current.json"), "pga")
    assert row["state"] == "pre"
    assert row["winner_name"] is None and row["winner_id"] is None
    assert row["season"] == 2026


def test_scorecard() -> None:
    rows = schemas.flatten_scorecard(
        load("linescores_masters_scheffler.json"), tour="pga", event_id=401811941, player_id=9478
    )
    assert len(rows) == 72
    assert [(row["round"], row["hole"]) for row in rows][:2] == [(1, 1), (1, 2)]
    totals: dict[int, int] = {}
    for row in rows:
        totals[row["round"]] = totals.get(row["round"], 0) + row["strokes"]
        assert row["score_to_par"] == row["strokes"] - row["par"]
    assert totals == {row["round"]: row["round_strokes"] for row in rows}
    assert totals == {1: 70, 2: 74, 3: 65, 4: 68}
    assert rows[1]["score_type"] == "EAGLE"


def test_season_stats() -> None:
    rows = schemas.flatten_season_stats(load("stats_pga_2026.json"))
    leader = rows[0]
    assert leader["player_name"] == "Scottie Scheffler"
    assert leader["season"] == 2026
    assert leader["fedex_cup_rank"] == 1
    assert leader["fedex_cup_points"] == 4986.0
    assert leader["earnings"] == Decimal("30937524.00")
    assert leader["wins"] == 3
    assert leader["events_played"] == 20
    assert leader["scoring_average"] == pytest.approx(67.859, abs=1e-3)
    assert leader["last_tournament_name"] == "Presidents Cup"
    assert leader["last_tournament_end_date"] == date(2026, 9, 26)


def test_season_stats_follow_category_names_not_positions() -> None:
    payload = {
        "categories": [{"name": "general", "names": ["wins", "amount"]}],
        "athletes": [
            {"athlete": {"id": "1"}, "categories": [{"name": "general", "values": [2, 1000.5], "ranks": []}]},
            {"athlete": {}},
        ],
    }
    [row] = schemas.flatten_season_stats(payload)
    assert row["wins"] == 2 and row["earnings"] == Decimal("1000.50")
    assert "fedex_cup_rank" not in row


def test_player_profile() -> None:
    [row] = schemas.flatten_player(load("athlete_scheffler.json"))
    assert row["birth_date"] == date(1996, 6, 21)
    assert row["birth_city"] == "Dallas" and row["birth_state"] == "Texas"
    assert row["college"] == "Texas"
    assert row["turned_pro"] == 2018
    assert row["season_fedex_cup_rank"] == 1
    assert schemas.flatten_player({}) == []


def test_world_rankings() -> None:
    rows = schemas.flatten_world_rankings(load("owgr_rankings_top5.json")["rankingsList"])
    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5]
    top = rows[0]
    assert top["player_name"] == "Scottie Scheffler"
    assert top["owgr_player_id"] == 18417
    assert top["country_code"] == "USA"
    assert top["birth_date"] == date(1996, 6, 21)
    assert top["events_played"] == 42
    assert rows[1]["country_code"] == "NIR"
    assert schemas.flatten_world_rankings([{"rank": None}, "junk"]) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("E", 0), ("+3", 3), ("-12", -12), ("13", None), ("4 & 3", None), (None, None)],
)
def test_to_par(value, expected) -> None:
    assert schemas.to_par(value) == expected


def test_scalar_helpers() -> None:
    assert schemas.to_int(True) is None
    assert schemas.to_int("150.0") == 150
    assert schemas.to_int("x") is None
    assert schemas.to_float("nan") is None
    assert schemas.to_float("") is None
    assert schemas.to_float("1.5") == 1.5
    assert schemas.to_money("$4,500,000") == Decimal("4500000.00")
    assert schemas.to_money("--") is None
    assert schemas.to_money(False) is None
    assert schemas.to_money("abc") is None
    assert schemas.to_timestamp("bad") is None
    assert schemas.to_timestamp("2026-04-12T18:25") == datetime(2026, 4, 12, 18, 25, tzinfo=UTC)
    assert schemas.to_date("2026-13-40") is None
    assert schemas.to_date(None) is None
    assert schemas.to_dmy_date("31/2/2000") is None
    assert schemas.to_dmy_date(None) is None


def test_tour_rows_cover_six_tours() -> None:
    rows = schemas.tour_rows()
    assert [row["tour"] for row in rows] == ["pga", "eur", "liv", "lpga", "champions-tour", "ntw"]
    assert [row["tour"] for row in rows if row["has_season_stats"]] == ["pga"]
