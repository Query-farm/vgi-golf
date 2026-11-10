"""VGI worker exposing professional golf data as a read-only SQL catalog."""

from __future__ import annotations

import json
import os
import sys

from vgi import Worker
from vgi.catalog import Catalog, ConstraintInfo, Schema, Table, constraint_record

from vgi_golf.meta import column_comments
from vgi_golf.players import PLAYER_FUNCTIONS, CurrentPlayerStatsFunction
from vgi_golf.rankings import RANKING_FUNCTIONS, CurrentWorldRankingsFunction
from vgi_golf.schemas import (
    LEADERBOARD_SCHEMA,
    SCHEDULE_SCHEMA,
    SEASON_STATS_SCHEMA,
    TOURS_SCHEMA,
    WORLD_RANKINGS_SCHEMA,
)
from vgi_golf.tournaments import (
    TOURNAMENT_FUNCTIONS,
    CurrentLeaderboardFunction,
    CurrentScheduleFunction,
    ToursFunction,
)

SOURCE_URL = "https://github.com/Query-farm/vgi-golf"


def _table_tags(
    *, category: str, title: str, keywords: list[str], llm: str, md: str, examples: list[tuple[str, str]]
) -> dict[str, str]:
    return {
        "provider": "owgr" if category == "rankings" else "espn-golf",
        "vgi.category": category,
        "vgi.title": title,
        "vgi.keywords": json.dumps(keywords),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps(
            [{"description": description, "sql": sql} for description, sql in examples]
        ),
    }


_TOURS_TAGS = _table_tags(
    category="tournaments",
    title="Supported Golf Tours",
    keywords=["tours", "PGA Tour", "DP World Tour", "LIV Golf", "LPGA", "Korn Ferry", "Champions"],
    llm=(
        "The six tours this catalog covers, with the slug every tour argument accepts: pga, eur, "
        "liv, lpga, champions-tour and ntw. has_season_stats marks the tour with season statistics."
    ),
    md="Static list of supported tours and their slugs.",
    examples=[("List tour slugs.", "SELECT tour, name, gender FROM golf.main.tours ORDER BY tour")],
)

_LEADERBOARD_TAGS = _table_tags(
    category="tournaments",
    title="Current PGA Tour Leaderboard",
    keywords=["leaderboard", "PGA Tour", "score to par", "live scores", "tournament"],
    llm=(
        "The PGA Tour leaderboard for the tournament in progress, or the most recently finished "
        "one when the next event has not started. One row per player: position, score to par, "
        "round scores, thru, status and earnings. Order by sort_order. For other tours or a "
        "specific event call leaderboard(tour := ..., event_id := ...)."
    ),
    md=(
        "Live PGA Tour leaderboard as a read-only table. Between events it shows the most recent "
        "finished tournament; check `event_name` and `event_completed`."
    ),
    examples=[
        (
            "Current PGA Tour top ten.",
            "SELECT position, player_name, score_to_par, thru, status FROM golf.main.current_leaderboard "
            "ORDER BY sort_order LIMIT 10",
        ),
        (
            "Lowest single round in the current event.",
            "SELECT player_name, least(round_1, round_2, round_3, round_4) AS best_round "
            "FROM golf.main.current_leaderboard ORDER BY best_round NULLS LAST LIMIT 5",
        ),
    ],
)

_SCHEDULE_TAGS = _table_tags(
    category="tournaments",
    title="Current PGA Tour Schedule",
    keywords=["schedule", "calendar", "tournaments", "winners", "PGA Tour"],
    llm=(
        "Every PGA Tour tournament in the current season with dates, status, field size, winner "
        "and winning score. Use event_id with leaderboard() or player_scorecard(). For another "
        "tour or season call schedule(tour := ..., season := ...)."
    ),
    md="The current PGA Tour season calendar with results so far.",
    examples=[
        (
            "Winners so far this season.",
            "SELECT start_date, event_name, winner_name, winning_score FROM golf.main.current_schedule "
            "WHERE completed ORDER BY start_date",
        ),
        (
            "Multiple winners this season.",
            "SELECT winner_name, count(*) AS wins FROM golf.main.current_schedule WHERE completed "
            "GROUP BY winner_name HAVING count(*) > 1 ORDER BY wins DESC",
        ),
    ],
)

_STATS_TAGS = _table_tags(
    category="player-stats",
    title="Current PGA Tour Statistics",
    keywords=["FedExCup", "earnings", "scoring average", "driving distance", "greens in regulation"],
    llm=(
        "Current-season PGA Tour statistics, one row per player: FedExCup rank and points, "
        "official earnings, events, cuts, top-10s, wins, scoring average, driving distance and "
        "accuracy, greens in regulation, putting, sand saves and birdies per round. Order by "
        "fedex_cup_rank for the FedExCup standings. Use season_player_stats(season := ...) for "
        "past seasons."
    ),
    md="Current PGA Tour season statistics. Percentages use a 0-100 scale.",
    examples=[
        (
            "Current FedExCup standings top ten.",
            "SELECT fedex_cup_rank, player_name, fedex_cup_points, wins, earnings "
            "FROM golf.main.current_player_stats ORDER BY fedex_cup_rank LIMIT 10",
        ),
        (
            "Best scoring averages among regulars.",
            "SELECT player_name, scoring_average, rounds_played FROM golf.main.current_player_stats "
            "WHERE rounds_played >= 40 ORDER BY scoring_average LIMIT 10",
        ),
    ],
)

_RANKINGS_TAGS = _table_tags(
    category="rankings",
    title="Current World Golf Ranking",
    keywords=["OWGR", "world ranking", "world number one", "ranking points"],
    llm=(
        "The current men's Official World Golf Ranking, top 1000, one row per golfer with rank, "
        "country, average and total points and events counted. Use world_rankings(max_rank := "
        "...) to read deeper. OWGR ids are not ESPN ids."
    ),
    md="The OWGR top 1,000 as a read-only table, refreshed weekly upstream.",
    examples=[
        (
            "World top ten.",
            "SELECT rank, player_name, country, average_points FROM golf.main.current_world_rankings "
            "ORDER BY rank LIMIT 10",
        ),
        (
            "Top-100 golfers by country.",
            "SELECT country, count(*) AS golfers FROM golf.main.current_world_rankings WHERE rank <= 100 "
            "GROUP BY country ORDER BY golfers DESC",
        ),
    ],
)

_EXECUTABLE_EXAMPLES = json.dumps(
    [
        {
            "name": "six_tours_supported",
            "description": "The catalog covers six tours.",
            "sql": "SELECT count(*) AS tours FROM golf.main.tours",
            "expected_result": [{"tours": 6}],
        },
        {
            "name": "masters_2026_champion",
            "description": "Rory McIlroy won the 2026 Masters at 12 under par.",
            "sql": (
                "SELECT player_name, score_to_par FROM golf.main.leaderboard(event_id := 401811941) "
                "WHERE sort_order = 1"
            ),
            "expected_result": [{"player_name": "Rory McIlroy", "score_to_par": -12}],
        },
        {
            "name": "scorecard_matches_round_totals",
            "description": "Scottie Scheffler's 2026 Masters hole scores sum to his round totals.",
            "sql": (
                "SELECT bool_and(total = round_strokes) AS consistent FROM ("
                "SELECT round, sum(strokes) AS total, any_value(round_strokes) AS round_strokes "
                "FROM golf.main.player_scorecard(401811941, 9478) GROUP BY round)"
            ),
            "expected_result": [{"consistent": True}],
        },
        {
            "name": "world_rankings_are_populated",
            "description": "The OWGR table has a world number one.",
            "sql": "SELECT count(*) >= 1 AS has_leader FROM golf.main.current_world_rankings WHERE rank = 1",
            "expected_result": [{"has_leader": True}],
        },
    ]
)

_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "masters_2026_top_ten",
            "prompt": "Show the top ten finishers at the 2026 Masters with their scores to par.",
        },
        {
            "name": "masters_2026_birdies",
            "prompt": "How many birdies did Scottie Scheffler (ESPN id 9478) make at the 2026 Masters?",
        },
        {
            "name": "augusta_par_threes",
            "prompt": "List the par-3 holes at Augusta National for the 2026 Masters and their yardages.",
        },
        {"name": "pga_2025_winners", "prompt": "List every 2025 PGA Tour event and who won it."},
        {
            "name": "fedex_2025_leaders",
            "prompt": "Which five PGA Tour players earned the most FedExCup points in 2025?",
        },
        {
            "name": "liv_indianapolis_2026",
            "prompt": "Show the top five at the 2026 LIV Golf Indianapolis event (ESPN event 401811430).",
        },
        {
            "name": "scheffler_profile",
            "prompt": (
                "Where and when was Scottie Scheffler (ESPN id 9478) born, and which college did he attend?"
            ),
        },
        {
            "name": "pga_tour_this_week",
            "prompt": (
                "Which PGA Tour event is in progress or just finished, who leads it, and how many "
                "PGA Tour events have been completed this season?"
            ),
        },
        {
            "name": "fedex_and_world_leaders",
            "prompt": (
                "Who has the most FedExCup points this PGA Tour season, and who is the world "
                "number one golfer?"
            ),
        },
        {
            "name": "japan_world_ranking",
            "prompt": "How many Japanese golfers are inside the top 2000 of the Official World Golf Ranking?",
        },
    ]
)


def _key_constraints(table: str, key: tuple[str, ...]) -> tuple[ConstraintInfo, ...]:
    """Primary-key and NOT NULL records for a read-only table keyed by ``key``."""
    records = [
        constraint_record(
            ["main"],
            table,
            "pk",
            "PRIMARY_KEY",
            [[column] for column in key],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
    ]
    records += [
        constraint_record(
            ["main"],
            table,
            f"{column}_not_null",
            "NOT_NULL",
            [[column]],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
        for column in key
    ]
    return tuple(records)


GOLF_CATALOG = Catalog(
    name="golf",
    default_schema="main",
    source_url=SOURCE_URL,
    comment="Read-only professional golf leaderboards, schedules, statistics and world rankings",
    tags={
        "provider": "espn-golf",
        "domain": "sports",
        "vgi.title": "Professional Golf Data",
        "vgi.subtitle": "PGA Tour, DP World, LIV, LPGA, Champions and Korn Ferry leaderboards plus OWGR.",
        "vgi.source_url": SOURCE_URL,
        "vgi.author": "Query Farm LLC <hello@query.farm>",
        "vgi.copyright": (
            "Worker (c) 2026 Query Farm LLC. Tournament data is fetched on demand from ESPN's "
            "unofficial endpoints and the Official World Golf Ranking; it remains the property of "
            "its owners and is subject to their terms. Not affiliated with ESPN, the tours or OWGR."
        ),
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-golf/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-golf/blob/main/README.md",
        "vgi.keywords": json.dumps(
            ["golf", "PGA Tour", "LIV Golf", "LPGA", "DP World Tour", "OWGR", "Masters"]
        ),
        "vgi.executable_examples": _EXECUTABLE_EXAMPLES,
        "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
        "vgi.doc_llm": (
            "Read-only professional golf data for the PGA Tour, DP World Tour, LIV Golf, LPGA Tour, "
            "PGA Tour Champions and Korn Ferry Tour, plus the men's Official World Golf Ranking. "
            "Current tables give this week's PGA Tour leaderboard, the PGA Tour season calendar "
            "with winners, PGA Tour season statistics and the OWGR top 1000. Parameterized "
            "functions take a tour slug and cover any tour's schedule and leaderboards, past "
            "seasons, hole-by-hole scorecards, course setups and golfer profiles. Find tournament "
            "ids with schedule() and golfer ids on a leaderboard; ESPN ids are shared across tours."
        ),
        "vgi.doc_md": (
            "# Professional golf\n\n"
            "Leaderboards, schedules, hole-by-hole scorecards, PGA Tour season statistics and the "
            "Official World Golf Ranking, fetched on demand.\n\n"
            "## Data model\n\n"
            "Tournaments are keyed by ESPN `event_id` and golfers by ESPN `player_id`, shared "
            "across tours. The ranking uses OWGR's own `owgr_player_id`. Scores to par are "
            "integers (E = 0); round scores are strokes.\n\n"
            "Data comes from ESPN's undocumented golf endpoints and OWGR's public backend. This "
            "worker is not affiliated with ESPN, Disney, any tour or OWGR."
        ),
    },
    schemas=[
        Schema(
            path=["main"],
            comment="Golf tournaments, players and world rankings",
            tags={
                "provider": "espn-golf",
                "domain": "sports",
                "vgi.title": "Golf Tournaments and Players",
                "vgi.keywords": json.dumps(
                    ["leaderboard", "schedule", "scorecard", "FedExCup", "world ranking", "tours"]
                ),
                "vgi.categories": json.dumps(
                    [
                        {
                            "name": "tournaments",
                            "title": "Tournaments",
                            "description": "Tours, schedules, leaderboards, scorecards and course setups.",
                            "keywords": ["leaderboard", "schedule", "scorecard", "course"],
                        },
                        {
                            "name": "player-stats",
                            "title": "Player Statistics",
                            "description": "PGA Tour season statistics and FedExCup standings.",
                            "keywords": ["FedExCup", "earnings", "scoring average", "driving"],
                        },
                        {
                            "name": "player-detail",
                            "title": "Player Detail",
                            "description": "Golfer biographical profiles.",
                            "keywords": ["profile", "biography", "player"],
                        },
                        {
                            "name": "rankings",
                            "title": "World Rankings",
                            "description": "The Official World Golf Ranking.",
                            "keywords": ["OWGR", "world ranking"],
                        },
                    ]
                ),
                "vgi.doc_llm": (
                    "Start with current_leaderboard, current_schedule, current_player_stats or "
                    "current_world_rankings. Use schedule() to find event ids, leaderboard() for any "
                    "tour or past event, player_scorecard() for hole-by-hole detail and "
                    "season_player_stats() for past PGA Tour seasons."
                ),
                "vgi.doc_md": (
                    "## Golf analytical data\n\n"
                    "Rows are entry-, tournament-, hole-, player-season- or golfer-grain.\n\n"
                    "Current tables cover the PGA Tour and OWGR; functions add the DP World Tour, "
                    "LIV Golf, LPGA, PGA Tour Champions, Korn Ferry Tour and past seasons."
                ),
                "vgi.example_queries": _LEADERBOARD_TAGS["vgi.example_queries"],
            },
            functions=[*TOURNAMENT_FUNCTIONS, *PLAYER_FUNCTIONS, *RANKING_FUNCTIONS],
            tables=[
                Table(
                    name="tours",
                    function=ToursFunction,
                    comment="Supported golf tours and the slug each tour argument accepts",
                    tags=_TOURS_TAGS,
                    column_comments=column_comments(TOURS_SCHEMA),
                    constraints=_key_constraints("tours", ("tour",)),
                    cardinality_estimate=6,
                ),
                Table(
                    name="current_leaderboard",
                    function=CurrentLeaderboardFunction,
                    comment="PGA Tour leaderboard for the event in progress or most recently finished",
                    tags=_LEADERBOARD_TAGS,
                    column_comments=column_comments(LEADERBOARD_SCHEMA),
                    constraints=_key_constraints("current_leaderboard", ("event_id", "entry_id")),
                    cardinality_estimate=140,
                ),
                Table(
                    name="current_schedule",
                    function=CurrentScheduleFunction,
                    comment="Current PGA Tour season schedule with winners, one row per tournament",
                    tags=_SCHEDULE_TAGS,
                    column_comments=column_comments(SCHEDULE_SCHEMA),
                    constraints=_key_constraints("current_schedule", ("event_id",)),
                    cardinality_estimate=49,
                ),
                Table(
                    name="current_player_stats",
                    function=CurrentPlayerStatsFunction,
                    comment="Current PGA Tour season statistics, one row per player",
                    tags=_STATS_TAGS,
                    column_comments=column_comments(SEASON_STATS_SCHEMA),
                    constraints=_key_constraints("current_player_stats", ("player_id",)),
                    cardinality_estimate=240,
                ),
                Table(
                    name="current_world_rankings",
                    function=CurrentWorldRankingsFunction,
                    comment="Current Official World Golf Ranking top 1,000, one row per golfer",
                    tags=_RANKINGS_TAGS,
                    column_comments=column_comments(WORLD_RANKINGS_SCHEMA),
                    constraints=_key_constraints("current_world_rankings", ("owgr_player_id",)),
                    cardinality_estimate=1_000,
                ),
            ],
        )
    ],
)


class GolfWorker(Worker):
    """Worker process hosting the read-only golf catalog."""

    catalog = GOLF_CATALOG


def main() -> None:
    """Run over stdio by default, or HTTP when VGI's ``--http`` flag is supplied."""
    GolfWorker.main()


def main_http() -> None:
    """Run HTTP with production-safe configuration requirements."""
    if not os.environ.get("VGI_SIGNING_KEY"):
        raise SystemExit(
            "VGI_SIGNING_KEY is required for HTTP mode so state remains valid across restarts and replicas"
        )
    auth_configured = bool(os.environ.get("VGI_BEARER_TOKENS") or os.environ.get("VGI_JWT_ISSUER"))
    auth_configured = auth_configured or os.environ.get("VGI_PROXY_PROOF_MODE", "").lower() == "require"
    if not auth_configured and os.environ.get("VGI_ALLOW_ANONYMOUS") != "1":
        raise SystemExit(
            "HTTP authentication is required; configure bearer/JWT/proxy proof, or explicitly set "
            "VGI_ALLOW_ANONYMOUS=1 for an intentionally public deployment"
        )
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    if "--describe" not in argv and "--no-describe" not in argv:
        argv = ["--no-describe", *argv]
    if not any(value == "--cors-origins" or value.startswith("--cors-origins=") for value in argv):
        argv = ["--cors-origins", "", *argv]
    sys.argv = [sys.argv[0], *argv]
    GolfWorker.main()
