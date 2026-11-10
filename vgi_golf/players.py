"""PGA Tour season statistics and golfer profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.metadata import FunctionExample
from vgi.table_function import (
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from vgi_golf import api
from vgi_golf.common import emit_rows, function_tags
from vgi_golf.schemas import PLAYER_SCHEMA, SEASON_STATS_SCHEMA, flatten_player, flatten_season_stats


@dataclass(kw_only=True)
class SeasonStatsArgs:
    """Arguments accepted by ``season_player_stats``."""

    season: Annotated[
        int | None,
        Arg(
            "season",
            default=None,
            arrow_type=pa.int32(),
            ge=2000,
            le=2100,
            doc="PGA Tour season year. NULL selects the current season.",
        ),
    ]


@dataclass(kw_only=True)
class PlayerArgs:
    """Arguments accepted by ``player``."""

    player_id: Annotated[
        int, Arg(0, arrow_type=pa.int64(), ge=1, doc="ESPN golfer id (shared across tours).")
    ]


def _season_stats(season: int | None, schema: pa.Schema, out: OutputCollector) -> None:
    response = api.fetch_season_stats(season)
    emit_rows(flatten_season_stats(response.payload), schema, out, response.ttl)


_STATS_SQL = (
    "SELECT fedex_cup_rank, player_name, fedex_cup_points, earnings, wins, scoring_average "
    "FROM golf.main.season_player_stats(season := 2025) ORDER BY fedex_cup_rank LIMIT 10"
)
_DRIVING_SQL = (
    "SELECT player_name, driving_distance, driving_accuracy_pct "
    "FROM golf.main.season_player_stats(season := 2025) WHERE rounds_played >= 40 "
    "ORDER BY driving_distance DESC LIMIT 10"
)


@init_single_worker
@bind_fixed_schema
class SeasonPlayerStatsFunction(TableFunctionGenerator[SeasonStatsArgs]):
    """PGA Tour season statistics for every ranked player."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = SEASON_STATS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "season_player_stats"
        description = "PGA Tour season statistics: FedExCup, earnings, scoring, driving, greens"
        categories = ["golf", "player-stats"]
        projection_pushdown = True
        tags = function_tags(
            category="player-stats",
            title="PGA Tour Season Statistics",
            llm=(
                "One row per PGA Tour player for a season: FedExCup rank and points, official "
                "earnings, events, cuts made, top-10s, wins, scoring average, driving distance and "
                "accuracy, greens in regulation, putts per hole, sand saves and birdies per round. "
                "Named argument season (default current). PGA Tour only; ESPN publishes no "
                "season statistics for the other tours."
            ),
            md=(
                "Season statistics from ESPN for the PGA Tour. Percentages are on a 0-100 scale. "
                "Order by `fedex_cup_rank` for the FedExCup standings."
            ),
            schema=SEASON_STATS_SCHEMA,
            examples=[
                ("The 2025 FedExCup top ten.", _STATS_SQL),
                ("Longest 2025 drivers with at least 40 rounds.", _DRIVING_SQL),
            ],
        )
        examples = [FunctionExample(sql=_STATS_SQL, description="The 2025 FedExCup top ten")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """ESPN ranks roughly 200-650 players a season."""
        return TableCardinality(estimate=240, max=5_000)

    @classmethod
    def process(cls, params: ProcessParams[SeasonStatsArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one season's statistics."""
        _season_stats(params.args.season, params.output_schema, out)


_CURRENT_STATS_SQL = (
    "SELECT fedex_cup_rank, player_name, fedex_cup_points FROM golf.main.all_current_player_stats() "
    "ORDER BY fedex_cup_rank LIMIT 10"
)


@init_single_worker
@bind_fixed_schema
class CurrentPlayerStatsFunction(TableFunctionGenerator[None]):
    """Current-season PGA Tour statistics backing ``current_player_stats``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = SEASON_STATS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_player_stats"
        description = "Current PGA Tour season statistics (scan behind current_player_stats)"
        categories = ["golf", "player-stats"]
        projection_pushdown = True
        tags = function_tags(
            category="player-stats",
            title="Current PGA Tour Statistics Scan",
            llm=(
                "The scan behind the current_player_stats table. Prefer the table; use "
                "season_player_stats(season := ...) for a past season."
            ),
            md="Backing scan for `current_player_stats`. Selecting the table is normally clearer.",
            schema=SEASON_STATS_SCHEMA,
            examples=[("Read the backing scan directly.", _CURRENT_STATS_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_STATS_SQL, description="Current FedExCup top ten")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """ESPN ranks roughly 200-650 players a season."""
        return TableCardinality(estimate=240, max=5_000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit current-season statistics."""
        _season_stats(None, params.output_schema, out)


_PLAYER_SQL = "SELECT player_name, birth_date, birth_city, college, turned_pro FROM golf.main.player(9478)"


@init_single_worker
@bind_fixed_schema
class PlayerFunction(TableFunctionGenerator[PlayerArgs]):
    """One golfer's ESPN profile."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAYER_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player"
        description = "Biographical profile and season summary for one golfer"
        categories = ["golf", "player-detail"]
        projection_pushdown = True
        tags = function_tags(
            category="player-detail",
            title="Golfer Profile",
            llm=(
                "One row describing a golfer: birth date and place, citizenship, height, weight, "
                "hand, college, year turned pro, plus a current-season summary (scoring average, "
                "earnings, FedExCup points and rank). Positional argument: ESPN player_id, which "
                "is shared across tours. Returns no rows for an unknown id."
            ),
            md="ESPN golfer profile. Find ids in `leaderboard()` or `current_player_stats`.",
            schema=PLAYER_SCHEMA,
            examples=[("Scottie Scheffler's profile.", _PLAYER_SQL)],
        )
        examples = [FunctionExample(sql=_PLAYER_SQL, description="Scottie Scheffler's profile")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """At most one profile."""
        return TableCardinality(estimate=1, max=1)

    @classmethod
    def process(cls, params: ProcessParams[PlayerArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one profile."""
        response = api.fetch_athlete(params.args.player_id)
        emit_rows(flatten_player(response.payload), params.output_schema, out, response.ttl)


PLAYER_FUNCTIONS = [SeasonPlayerStatsFunction, CurrentPlayerStatsFunction, PlayerFunction]
