"""Tour list, schedules, leaderboards, scorecards and course setups."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

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
from vgi_golf.common import TOUR_ARG, emit_rows, function_tags
from vgi_golf.schemas import (
    COURSE_HOLES_SCHEMA,
    LEADERBOARD_SCHEMA,
    SCHEDULE_SCHEMA,
    SCORECARD_SCHEMA,
    TOURS_SCHEMA,
    flatten_course_holes,
    flatten_leaderboard,
    flatten_schedule,
    flatten_scorecard,
    to_int,
    to_timestamp,
    tour_rows,
)

MASTERS_2026 = 401811941

# ---------------------------------------------------------------------------
# Event selection
# ---------------------------------------------------------------------------


MAX_FALLBACK_EVENTS = 3
_INACTIVE_STATUSES = frozenset({"STATUS_CANCELED", "STATUS_POSTPONED"})


def _status_type(event: Mapping[str, Any]) -> Mapping[str, Any]:
    status = event.get("status")
    kind = status.get("type") if isinstance(status, Mapping) else None
    return kind if isinstance(kind, Mapping) else {}


def select_current_event_ids(scoreboard: Mapping[str, Any]) -> tuple[list[int], bool]:
    """Pick the tournament(s) a "current leaderboard" should show.

    Returns ``(event_ids, active)``. Scoreboard events that are in progress or
    final are *active* and are all shown. Otherwise (an upcoming, canceled or
    postponed event) the result is a fallback list, newest first: the calendar
    events that started before the scoreboard's event, then the scoreboard's
    own events. The caller uses the first fallback that has a field.
    """
    events = [value for value in scoreboard.get("events") or [] if isinstance(value, Mapping)]
    active = [
        to_int(event.get("id"))
        for event in events
        if _status_type(event).get("state") in {"in", "post"}
        and _status_type(event).get("name") not in _INACTIVE_STATUSES
    ]
    active_ids = [value for value in active if value is not None]
    if active_ids:
        return active_ids, True

    upcoming_start = min(
        (start for event in events if (start := to_timestamp(event.get("date"))) is not None),
        default=None,
    )
    leagues = scoreboard.get("leagues") or []
    calendar = leagues[0].get("calendar", []) if leagues and isinstance(leagues[0], Mapping) else []
    scoreboard_ids = [value for value in (to_int(event.get("id")) for event in events) if value is not None]
    previous: list[tuple[Any, int]] = []
    for entry in calendar:
        if not isinstance(entry, Mapping):
            continue
        entry_id = to_int(entry.get("id"))
        start = to_timestamp(entry.get("startDate"))
        if entry_id is None or start is None or entry_id in scoreboard_ids:
            continue
        if upcoming_start is None or start < upcoming_start:
            previous.append((start, entry_id))
    newest_first = [entry_id for _start, entry_id in sorted(previous, reverse=True)]
    return [*newest_first[:MAX_FALLBACK_EVENTS], *scoreboard_ids], False


def current_leaderboard_rows(tour: str) -> tuple[list[dict[str, Any]], int]:
    """Rows and freshness for the tour's current (or most recent) tournament."""
    scoreboard = api.fetch_scoreboard(tour)
    event_ids, active = select_current_event_ids(scoreboard.payload)
    rows: list[dict[str, Any]] = []
    ttl = scoreboard.ttl
    for event_id in event_ids:
        response = api.fetch_leaderboard(tour, event_id)
        rows.extend(flatten_leaderboard(response.payload, tour))
        ttl = min(ttl, response.ttl)
        if rows and not active:
            break
    return rows, ttl


def current_season(tour: str) -> int:
    """The season year the tour's scoreboard currently reports."""
    payload = api.fetch_scoreboard(tour).payload
    for league in payload.get("leagues") or []:
        season = league.get("season") if isinstance(league, Mapping) else None
        year = to_int(season.get("year")) if isinstance(season, Mapping) else None
        if year is not None:
            return year
    year = to_int((payload.get("season") or {}).get("year"))
    if year is None:
        raise ValueError(f"ESPN scoreboard for {tour} does not report a current season")
    return year


def schedule_rows(tour: str, season: int | None) -> tuple[list[dict[str, Any]], int]:
    """Rows and freshness for one tour season's tournaments."""
    year = season if season is not None else current_season(tour)
    response = api.fetch_scoreboard(tour, dates=f"{year:04d}")
    return flatten_schedule(response.payload, tour), response.ttl


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class LeaderboardArgs:
    """Arguments accepted by ``leaderboard``."""

    tour: Annotated[str, TOUR_ARG]
    event_id: Annotated[
        int | None,
        Arg(
            "event_id",
            default=None,
            arrow_type=pa.int64(),
            ge=1,
            doc="ESPN tournament id from schedule(). NULL selects the current or most recent event.",
        ),
    ]


@dataclass(kw_only=True)
class ScheduleArgs:
    """Arguments accepted by ``schedule``."""

    tour: Annotated[str, TOUR_ARG]
    season: Annotated[
        int | None,
        Arg(
            "season",
            default=None,
            arrow_type=pa.int32(),
            ge=2000,
            le=2100,
            doc="Season year. NULL selects the tour's current season.",
        ),
    ]


@dataclass(kw_only=True)
class ScorecardArgs:
    """Arguments accepted by ``player_scorecard``."""

    event_id: Annotated[int, Arg(0, arrow_type=pa.int64(), ge=1, doc="ESPN tournament id from schedule().")]
    player_id: Annotated[int, Arg(1, arrow_type=pa.int64(), ge=1, doc="ESPN golfer id from leaderboard().")]
    tour: Annotated[str, TOUR_ARG]


@dataclass(kw_only=True)
class CourseArgs:
    """Arguments accepted by ``course_holes``."""

    event_id: Annotated[int, Arg(0, arrow_type=pa.int64(), ge=1, doc="ESPN tournament id from schedule().")]
    tour: Annotated[str, TOUR_ARG]


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

_TOURS_SQL = "SELECT tour, name, gender, has_season_stats FROM golf.main.tours ORDER BY tour"


@init_single_worker
@bind_fixed_schema
class ToursFunction(TableFunctionGenerator[None]):
    """The tours this worker covers (backs the ``tours`` table)."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = TOURS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_tours"
        description = "Supported golf tours and their slugs (scan behind the tours table)"
        categories = ["golf", "tours"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Supported Golf Tours",
            llm=(
                "The scan behind the tours table. Lists every tour slug accepted by the tour "
                "argument of leaderboard(), schedule(), player_scorecard() and course_holes()."
            ),
            md="Static list of supported tours. Prefer the `tours` table.",
            schema=TOURS_SCHEMA,
            examples=[
                ("List the supported tours.", "SELECT tour, name FROM golf.main.all_tours() ORDER BY tour")
            ],
        )
        examples = [
            FunctionExample(
                sql="SELECT tour, name FROM golf.main.all_tours() ORDER BY tour",
                description="List the supported tours",
            )
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """There is one row per supported tour."""
        return TableCardinality(estimate=len(api.TOURS), max=len(api.TOURS))

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Emit the static tour list."""
        emit_rows(tour_rows(), params.output_schema, out, ttl=86_400)


_LEADERBOARD_SQL = (
    "SELECT position, player_name, score_to_par, round_1, round_2, round_3, round_4, earnings "
    f"FROM golf.main.leaderboard(event_id := {MASTERS_2026}) ORDER BY sort_order"
)
_LIV_SQL = (
    "SELECT position, player_name, score_to_par, total_strokes "
    "FROM golf.main.leaderboard(tour := 'liv', event_id := 401811430) ORDER BY sort_order LIMIT 10"
)


@init_single_worker
@bind_fixed_schema
class LeaderboardFunction(TableFunctionGenerator[LeaderboardArgs]):
    """A tournament leaderboard for any supported tour."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = LEADERBOARD_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "leaderboard"
        description = "Full tournament leaderboard for a tour and ESPN event id"
        categories = ["golf", "leaderboard"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Tournament Leaderboard",
            llm=(
                "One row per entry in a golf tournament: position, score to par, per-round "
                "strokes, status (Finish, Missed Cut, Withdrawn), earnings and FedExCup points. "
                "Pass tour => 'pga'|'eur'|'liv'|'lpga'|'champions-tour'|'ntw' and an event_id "
                "from schedule(). Without event_id it returns the current or most recent event. "
                "Order by sort_order for leaderboard order; position is text with a T tie prefix "
                "and position_number is the numeric rank. Team events have entry_type = 'team'. "
                "in_playoff marks players who went to a playoff."
            ),
            md=(
                "Leaderboard for one tournament, one row per player (or pairing in team events). "
                "`score_to_par` is an integer (E = 0); `round_1`..`round_4` are strokes per round. "
                "Missed-cut players keep their two rounds and have status `Missed Cut`."
            ),
            schema=LEADERBOARD_SCHEMA,
            examples=[
                ("The final 2026 Masters leaderboard.", _LEADERBOARD_SQL),
                ("Top ten at LIV Golf Indianapolis 2026.", _LIV_SQL),
            ],
        )
        examples = [FunctionExample(sql=_LEADERBOARD_SQL, description="The final 2026 Masters leaderboard")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Full-field events list up to about 160 entries."""
        return TableCardinality(estimate=140, max=1_000)

    @classmethod
    def process(cls, params: ProcessParams[LeaderboardArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one leaderboard."""
        args = params.args
        if args.event_id is None:
            rows, ttl = current_leaderboard_rows(args.tour)
        else:
            response = api.fetch_leaderboard(args.tour, args.event_id)
            rows, ttl = flatten_leaderboard(response.payload, args.tour), response.ttl
        emit_rows(rows, params.output_schema, out, ttl)


_CURRENT_LEADERBOARD_SQL = (
    "SELECT event_name, position, player_name, score_to_par, thru "
    "FROM golf.main.all_current_leaderboard() ORDER BY sort_order LIMIT 10"
)


@init_single_worker
@bind_fixed_schema
class CurrentLeaderboardFunction(TableFunctionGenerator[None]):
    """PGA Tour leaderboard backing the ``current_leaderboard`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = LEADERBOARD_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_leaderboard"
        description = "Current PGA Tour leaderboard (scan behind current_leaderboard)"
        categories = ["golf", "leaderboard"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Current PGA Leaderboard Scan",
            llm=(
                "The scan behind the current_leaderboard table. Prefer the table; use "
                "leaderboard(tour := ...) for other tours or a specific event."
            ),
            md="Backing scan for `current_leaderboard`. Selecting the table is normally clearer.",
            schema=LEADERBOARD_SCHEMA,
            examples=[("Read the backing scan directly.", _CURRENT_LEADERBOARD_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_LEADERBOARD_SQL, description="Current PGA Tour top ten")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A full PGA Tour field."""
        return TableCardinality(estimate=140, max=1_000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the current PGA Tour leaderboard."""
        rows, ttl = current_leaderboard_rows("pga")
        emit_rows(rows, params.output_schema, out, ttl)


_SCHEDULE_SQL = (
    "SELECT start_date, event_name, winner_name, winning_score "
    "FROM golf.main.schedule(season := 2025) ORDER BY start_date"
)
_LPGA_SCHEDULE_SQL = (
    "SELECT start_date, event_name, status FROM golf.main.schedule(tour := 'lpga') ORDER BY start_date"
)


@init_single_worker
@bind_fixed_schema
class ScheduleFunction(TableFunctionGenerator[ScheduleArgs]):
    """One tour season's tournaments with status and winners."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = SCHEDULE_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "schedule"
        description = "Tournament schedule and results for a tour season"
        categories = ["golf", "schedule"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Tour Schedule and Results",
            llm=(
                "One row per tournament in a tour season with dates, status, field size, winner "
                "and winning score. Use it to find event_id values for leaderboard() and "
                "player_scorecard(). Named arguments: tour (default 'pga') and season (default "
                "current). winning_score_to_par is NULL for cup and match-play events; playoff marks "
                "events decided in a playoff, and the winning score excludes the playoff holes."
            ),
            md=(
                "Season calendar with results. Completed events carry `winner_name` and "
                "`winning_score`; upcoming events have `state = 'pre'`."
            ),
            schema=SCHEDULE_SCHEMA,
            examples=[
                ("Every 2025 PGA Tour winner.", _SCHEDULE_SQL),
                ("The current LPGA Tour calendar.", _LPGA_SCHEDULE_SQL),
            ],
        )
        examples = [FunctionExample(sql=_SCHEDULE_SQL, description="Every 2025 PGA Tour winner")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Tours schedule roughly 15-55 events a season."""
        return TableCardinality(estimate=45, max=200)

    @classmethod
    def process(cls, params: ProcessParams[ScheduleArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one season's schedule."""
        rows, ttl = schedule_rows(params.args.tour, params.args.season)
        emit_rows(rows, params.output_schema, out, ttl)


_CURRENT_SCHEDULE_SQL = (
    "SELECT start_date, event_name, status FROM golf.main.all_current_schedule() ORDER BY start_date"
)


@init_single_worker
@bind_fixed_schema
class CurrentScheduleFunction(TableFunctionGenerator[None]):
    """Current PGA Tour season schedule backing the ``current_schedule`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = SCHEDULE_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_schedule"
        description = "Current PGA Tour season schedule (scan behind current_schedule)"
        categories = ["golf", "schedule"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Current PGA Tour Schedule Scan",
            llm=(
                "The scan behind the current_schedule table. Prefer the table; use schedule() "
                "for another tour or season."
            ),
            md="Backing scan for `current_schedule`. Selecting the table is normally clearer.",
            schema=SCHEDULE_SCHEMA,
            examples=[("Read the backing scan directly.", _CURRENT_SCHEDULE_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_SCHEDULE_SQL, description="Current PGA Tour calendar")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 50 PGA Tour events a season."""
        return TableCardinality(estimate=49, max=200)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the current PGA Tour schedule."""
        rows, ttl = schedule_rows("pga", None)
        emit_rows(rows, params.output_schema, out, ttl)


_SCORECARD_SQL = (
    "SELECT round, hole, par, strokes, score_type "
    f"FROM golf.main.player_scorecard({MASTERS_2026}, 9478) ORDER BY round, hole"
)
_SCORECARD_SUMMARY_SQL = (
    "SELECT score_type, count(*) AS holes "
    f"FROM golf.main.player_scorecard({MASTERS_2026}, 3470) GROUP BY score_type ORDER BY holes DESC"
)


@init_single_worker
@bind_fixed_schema
class PlayerScorecardFunction(TableFunctionGenerator[ScorecardArgs]):
    """Hole-by-hole scores for one player in one tournament."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = SCORECARD_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player_scorecard"
        description = "Hole-by-hole scorecard for one golfer in one tournament"
        categories = ["golf", "scorecard"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Player Hole-by-Hole Scorecard",
            llm=(
                "One row per hole played by a golfer at a tournament: round, hole, par, strokes, "
                "score_to_par and score_type (BIRDIE, PAR, BOGEY, EAGLE...). Positional arguments "
                "are the ESPN event_id and player_id (from leaderboard()); tour defaults to pga. "
                "Returns no rows when ESPN has no hole detail for that player or event."
            ),
            md=(
                "Hole-by-hole scores from ESPN's core API. Some tours (notably LIV) publish only "
                "round totals; then the result is empty and `leaderboard()` has the round scores."
            ),
            schema=SCORECARD_SCHEMA,
            examples=[
                ("Scottie Scheffler's 2026 Masters scorecard.", _SCORECARD_SQL),
                ("Rory McIlroy's 2026 Masters birdies, pars and bogeys.", _SCORECARD_SUMMARY_SQL),
            ],
        )
        examples = [FunctionExample(sql=_SCORECARD_SQL, description="Scottie Scheffler's 2026 Masters card")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Four rounds of 18 holes, plus room for playoff holes."""
        return TableCardinality(estimate=72, max=120)

    @classmethod
    def process(cls, params: ProcessParams[ScorecardArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one scorecard."""
        args = params.args
        response = api.fetch_linescores(args.tour, args.event_id, args.player_id)
        rows = flatten_scorecard(
            response.payload, tour=args.tour, event_id=args.event_id, player_id=args.player_id
        )
        emit_rows(rows, params.output_schema, out, response.ttl)


_COURSE_SQL = f"SELECT hole, par, yards FROM golf.main.course_holes({MASTERS_2026}) ORDER BY hole"


@init_single_worker
@bind_fixed_schema
class CourseHolesFunction(TableFunctionGenerator[CourseArgs]):
    """Par and yardage for every hole of an event's course(s)."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = COURSE_HOLES_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "course_holes"
        description = "Par and yardage per hole for a tournament's course setup"
        categories = ["golf", "course"]
        projection_pushdown = True
        tags = function_tags(
            category="tournaments",
            title="Tournament Course Holes",
            llm=(
                "One row per hole of each course used by a tournament, with par and yardage as set "
                "up for the event. Positional argument: ESPN event_id; tour defaults to pga. Join "
                "to player_scorecard() on hole for scoring by hole length."
            ),
            md="Course setup for a tournament: par and yards per hole, plus course totals.",
            schema=COURSE_HOLES_SCHEMA,
            examples=[("Augusta National's 2026 Masters setup.", _COURSE_SQL)],
        )
        examples = [FunctionExample(sql=_COURSE_SQL, description="Augusta National's 2026 Masters setup")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Usually one 18-hole course; pro-ams can use three."""
        return TableCardinality(estimate=18, max=108)

    @classmethod
    def process(cls, params: ProcessParams[CourseArgs], state: None, out: OutputCollector) -> None:
        """Fetch the leaderboard and emit its course setup."""
        response = api.fetch_leaderboard(params.args.tour, params.args.event_id)
        emit_rows(
            flatten_course_holes(response.payload, params.args.tour), params.output_schema, out, response.ttl
        )


TOURNAMENT_FUNCTIONS = [
    ToursFunction,
    LeaderboardFunction,
    CurrentLeaderboardFunction,
    ScheduleFunction,
    CurrentScheduleFunction,
    PlayerScorecardFunction,
    CourseHolesFunction,
]
