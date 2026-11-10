"""Official World Golf Ranking functions."""

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
from vgi_golf.schemas import WORLD_RANKINGS_SCHEMA, flatten_world_rankings

CURRENT_RANK_DEPTH = 1_000


@dataclass(kw_only=True)
class RankingsArgs:
    """Arguments accepted by ``world_rankings``."""

    max_rank: Annotated[
        int,
        Arg(
            "max_rank",
            default=CURRENT_RANK_DEPTH,
            arrow_type=pa.int32(),
            ge=1,
            le=api.MAX_OWGR_RANK,
            doc="How deep into the ranking to read; each 1,000 ranks is one upstream request.",
        ),
    ]


def _emit(max_rank: int, schema: pa.Schema, out: OutputCollector) -> None:
    response = api.fetch_world_rankings(max_rank)
    emit_rows(flatten_world_rankings(response.payload), schema, out, response.ttl)


_RANKINGS_SQL = (
    "SELECT rank, player_name, country, average_points, events_played "
    "FROM golf.main.world_rankings(max_rank := 2000) WHERE country_code = 'JPN' ORDER BY rank"
)


@init_single_worker
@bind_fixed_schema
class WorldRankingsFunction(TableFunctionGenerator[RankingsArgs]):
    """The current Official World Golf Ranking to a chosen depth."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = WORLD_RANKINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "world_rankings"
        description = "Current Official World Golf Ranking (OWGR) to a chosen depth"
        categories = ["golf", "rankings"]
        projection_pushdown = True
        tags = function_tags(
            category="rankings",
            title="Official World Golf Ranking",
            llm=(
                "The current men's Official World Golf Ranking from OWGR, one row per golfer: "
                "rank, country, average and total points, events counted, last-week and "
                "end-of-last-year rank. Named argument max_rank (default 1000, max 10000) reads "
                "deeper. The current_world_rankings table is the top 1000. OWGR player ids are "
                "not ESPN ids; join to ESPN tables on player_name."
            ),
            md=(
                "Official World Golf Ranking. `average_points` decides the order; "
                "`points_lost` is the (negative) time decay over the two-year window."
            ),
            schema=WORLD_RANKINGS_SCHEMA,
            examples=[("Japanese golfers in the top 2000.", _RANKINGS_SQL)],
        )
        examples = [FunctionExample(sql=_RANKINGS_SQL, description="Japanese golfers in the top 2000")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """The requested depth, by default 1,000 golfers."""
        return TableCardinality(estimate=CURRENT_RANK_DEPTH, max=api.MAX_OWGR_RANK)

    @classmethod
    def process(cls, params: ProcessParams[RankingsArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the ranking."""
        _emit(params.args.max_rank, params.output_schema, out)


_CURRENT_RANKINGS_SQL = (
    "SELECT rank, player_name, average_points FROM golf.main.all_current_world_rankings() "
    "ORDER BY rank LIMIT 10"
)


@init_single_worker
@bind_fixed_schema
class CurrentWorldRankingsFunction(TableFunctionGenerator[None]):
    """OWGR top 1,000 backing the ``current_world_rankings`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = WORLD_RANKINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_world_rankings"
        description = "Current OWGR top 1,000 (scan behind current_world_rankings)"
        categories = ["golf", "rankings"]
        projection_pushdown = True
        tags = function_tags(
            category="rankings",
            title="Current World Ranking Scan",
            llm=(
                "The scan behind the current_world_rankings table. Prefer the table; use "
                "world_rankings(max_rank := ...) to read beyond the top 1000."
            ),
            md="Backing scan for `current_world_rankings`. Selecting the table is normally clearer.",
            schema=WORLD_RANKINGS_SCHEMA,
            examples=[("Read the backing scan directly.", _CURRENT_RANKINGS_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_RANKINGS_SQL, description="World top ten")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """The top 1,000 golfers."""
        return TableCardinality(estimate=CURRENT_RANK_DEPTH, max=CURRENT_RANK_DEPTH + 50)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the top 1,000."""
        _emit(CURRENT_RANK_DEPTH, params.output_schema, out)


RANKING_FUNCTIONS = [WorldRankingsFunction, CurrentWorldRankingsFunction]
