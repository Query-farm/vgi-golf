"""Typed Arrow schemas and flattening for ESPN golf and OWGR responses.

Every flattener is a pure function from decoded JSON to row dictionaries, so
the parsing rules are unit-testable against recorded fixtures.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from vgi_golf.api import TOURS
from vgi_golf.meta import field

MONEY = pa.decimal128(14, 2)
TIMESTAMP = pa.timestamp("us", tz="UTC")
ID = pa.int64()

TOURS_SCHEMA = pa.schema(
    [
        field(
            "tour",
            pa.string(),
            "Tour slug accepted by every tour argument, for example pga or liv.",
            nullable=False,
        ),
        field("name", pa.string(), "Tour name."),
        field("abbreviation", pa.string(), "Short tour abbreviation."),
        field("espn_league_id", pa.int32(), "ESPN league identifier for the tour (opaque integer key)."),
        field("gender", pa.string(), "Field gender: men or women."),
        field(
            "has_season_stats", pa.bool_(), "Whether season_player_stats() covers this tour (PGA Tour only)."
        ),
    ]
)

LEADERBOARD_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "Tour slug the event belongs to."),
        field("event_id", ID, "ESPN tournament identifier (opaque integer key).", nullable=False),
        field("event_name", pa.string(), "Tournament name."),
        field("season", pa.int32(), "Season year of the tournament."),
        field("event_status", pa.string(), "Tournament status, for example Final, In Progress or Scheduled."),
        field("event_completed", pa.bool_(), "Whether the tournament is final."),
        field(
            "entry_id",
            ID,
            "ESPN competitor identifier: the player id, or a team id in team events.",
            nullable=False,
        ),
        field("entry_type", pa.string(), "athlete for individual entries or team for pairs/team events."),
        field("player_id", ID, "ESPN golfer identifier (opaque integer key); NULL for team entries."),
        field("player_name", pa.string(), "Golfer name, or the pairing name for a team entry."),
        field("player_short_name", pa.string(), "Abbreviated golfer name, for example R. McIlroy."),
        field("country", pa.string(), "Country the golfer represents, as ESPN labels the flag."),
        field("amateur", pa.bool_(), "Whether the golfer is playing as an amateur."),
        field("position", pa.string(), "Displayed finishing or current position, for example 1, T3 or -."),
        field(
            "position_number",
            pa.int32(),
            "Numeric finishing place (1 = first) without the tie marker; NULL when unranked.",
        ),
        field("is_tied", pa.bool_(), "Whether the position is shared."),
        field(
            "sort_order", pa.int32(), "ESPN leaderboard order (1 = leader); ranks every entry including cuts."
        ),
        field("status", pa.string(), "Player status, for example Finish, Missed Cut or Withdrawn."),
        field("status_code", pa.string(), "Machine status code, for example STATUS_FINISH or STATUS_CUT."),
        field(
            "score_to_par",
            pa.int32(),
            "Total score in strokes relative to par; negative is under par; E maps to 0.",
        ),
        field(
            "score_to_par_display", pa.string(), "ESPN's displayed score to par, for example -12, E or +3."
        ),
        field("total_strokes", pa.int32(), "Total strokes (count) across completed rounds."),
        field("round_1", pa.int32(), "Strokes in round 1; NULL when not played."),
        field("round_2", pa.int32(), "Strokes in round 2; NULL when not played."),
        field("round_3", pa.int32(), "Strokes in round 3; NULL when not played (for example after a cut)."),
        field("round_4", pa.int32(), "Strokes in round 4; NULL when not played."),
        field("rounds_played", pa.int32(), "Number of regulation rounds with a recorded score."),
        field(
            "current_round",
            pa.int32(),
            "Round number (1-based) the player is on or last completed; one past the final round "
            "for players who went to a playoff.",
        ),
        field(
            "thru",
            pa.int32(),
            "Holes completed in the current round (or playoff holes played); 18 when a round is finished.",
        ),
        field("in_playoff", pa.bool_(), "Whether the player took part in a playoff for the title."),
        field("tee_time", TIMESTAMP, "Tee time for the player's current or last round, in UTC."),
        field(
            "earnings", MONEY, "Prize money in US dollars for this entry; meaningful once the event is final."
        ),
        field(
            "fedex_cup_points",
            pa.float64(),
            "FedExCup points earned at this event (points); 0 for non-members and wherever ESPN "
            "publishes none (for example FedExCup Fall events).",
        ),
        field("movement", pa.int32(), "Positions gained (positive) or lost during the current round."),
    ]
)

SCHEDULE_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "Tour slug."),
        field("season", pa.int32(), "Season year."),
        field(
            "event_id",
            ID,
            "ESPN tournament identifier; pass to leaderboard(event_id := ...).",
            nullable=False,
        ),
        field("event_name", pa.string(), "Tournament name."),
        field("start_date", pa.date32(), "First scheduled day of the tournament."),
        field("end_date", pa.date32(), "Final scheduled day of the tournament."),
        field("status", pa.string(), "Tournament status, for example Final, In Progress or Scheduled."),
        field("state", pa.string(), "Lifecycle state: pre, in or post."),
        field("completed", pa.bool_(), "Whether the tournament is final."),
        field("field_size", pa.int32(), "Number of entries ESPN lists for the event."),
        field(
            "winner_id", ID, "ESPN id (opaque integer key) of the winning player or team; NULL until final."
        ),
        field("winner_name", pa.string(), "Winning player, pairing or team; NULL until final."),
        field(
            "winning_score",
            pa.string(),
            "Winning score as displayed: to par for stroke play, points for cups.",
        ),
        field(
            "winning_score_to_par",
            pa.int32(),
            "Winning score in strokes relative to par, excluding playoff holes; NULL for match-play "
            "or cup events. Stableford events (the Barracuda Championship) carry points here.",
        ),
        field(
            "playoff",
            pa.bool_(),
            "Whether the winner was decided in a playoff; NULL until the tournament is final.",
        ),
        field("broadcast", pa.string(), "US broadcast networks listed by ESPN."),
    ]
)

SCORECARD_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "Tour slug."),
        field("event_id", ID, "ESPN tournament identifier.", nullable=False),
        field("player_id", ID, "ESPN golfer identifier.", nullable=False),
        field("round", pa.int32(), "Round number, 1-based.", nullable=False),
        field("hole", pa.int32(), "Hole number, 1-18.", nullable=False),
        field("par", pa.int32(), "Par for the hole."),
        field("strokes", pa.int32(), "Strokes the player took on the hole."),
        field("score_to_par", pa.int32(), "Strokes minus par: -1 birdie, 0 par, +1 bogey."),
        field("score_type", pa.string(), "ESPN's result label, for example BIRDIE, PAR, BOGEY or EAGLE."),
        field("round_strokes", pa.int32(), "Total strokes for the round."),
        field("round_score_to_par_display", pa.string(), "Displayed round score to par, for example -5."),
        field("round_out_strokes", pa.int32(), "Strokes on the front nine of the round."),
        field("round_in_strokes", pa.int32(), "Strokes on the back nine of the round."),
        field("tee_time", TIMESTAMP, "Round tee time in UTC."),
        field("course_id", ID, "ESPN course identifier for the round."),
    ]
)

COURSE_HOLES_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "Tour slug."),
        field("event_id", ID, "ESPN tournament identifier.", nullable=False),
        field("course_id", ID, "ESPN course identifier.", nullable=False),
        field("course_name", pa.string(), "Course name."),
        field("host_course", pa.bool_(), "Whether this is the event's host course."),
        field("course_par", pa.int32(), "Total par for the course."),
        field("course_yards", pa.int32(), "Total yardage for the course."),
        field("hole", pa.int32(), "Hole number, 1-18.", nullable=False),
        field("par", pa.int32(), "Par for the hole."),
        field("yards", pa.int32(), "Hole yardage as set up for the event."),
    ]
)

SEASON_STATS_SCHEMA = pa.schema(
    [
        field("season", pa.int32(), "PGA Tour season year."),
        field("player_id", ID, "ESPN golfer identifier (opaque integer key).", nullable=False),
        field("player_name", pa.string(), "Golfer name."),
        field("first_name", pa.string(), "Given name."),
        field("last_name", pa.string(), "Family name."),
        field("country", pa.string(), "Country the golfer represents, as ESPN labels the flag."),
        field("age", pa.int32(), "Current age in years."),
        field(
            "fedex_cup_rank",
            pa.int32(),
            "Rank by FedExCup points (1 = points leader). After the TOUR Championship this is the "
            "points order, not the final FedExCup finish decided by starting strokes.",
        ),
        field("fedex_cup_points", pa.float64(), "FedExCup points accumulated in the season (points)."),
        field("earnings", MONEY, "Official money won in US dollars."),
        field("events_played", pa.int32(), "Count of tournaments started this season."),
        field(
            "rounds_played",
            pa.int32(),
            "Count of measured official rounds this season; NULL (with the per-round averages) "
            "when ESPN tracks none, typically for non-members.",
        ),
        field("cuts_made", pa.int32(), "Count of tournaments in which the player survived the cut."),
        field("top_ten_finishes", pa.int32(), "Count of finishes in tenth place or better."),
        field("wins", pa.int32(), "Count of tournament victories this season."),
        field("scoring_average", pa.float64(), "Weighted scoring average per round."),
        field("driving_distance", pa.float64(), "Average driving distance in yards."),
        field(
            "driving_accuracy_pct", pa.float64(), "Percentage of tee shots finishing in the fairway (0-100)."
        ),
        field("greens_in_regulation_pct", pa.float64(), "Percentage of greens hit in regulation (0-100)."),
        field(
            "putts_per_hole",
            pa.float64(),
            "ESPN's putts-per-hole average (labelled STROKESPERHOLE upstream).",
        ),
        field("sand_save_pct", pa.float64(), "Percentage of greenside bunker saves (0-100)."),
        field("birdies_per_round", pa.float64(), "Average birdies per round."),
        field(
            "last_tournament_name",
            pa.string(),
            "The golfer's most recent tournament on any tour, as ESPN reports it.",
        ),
        field("last_tournament_end_date", pa.date32(), "End date of that tournament."),
    ]
)

PLAYER_SCHEMA = pa.schema(
    [
        field("player_id", ID, "ESPN golfer identifier, shared across tours.", nullable=False),
        field("player_name", pa.string(), "Golfer name."),
        field("first_name", pa.string(), "Given name."),
        field("last_name", pa.string(), "Family name."),
        field("birth_date", pa.date32(), "Date of birth."),
        field("age", pa.int32(), "Current age in years."),
        field("gender", pa.string(), "Gender as reported by ESPN."),
        field("citizenship", pa.string(), "Country of citizenship."),
        field("birth_city", pa.string(), "City of birth."),
        field("birth_state", pa.string(), "State or region of birth."),
        field("birth_country", pa.string(), "Country of birth."),
        field("height", pa.string(), "Displayed height, for example 6' 3\"."),
        field("weight", pa.string(), "Displayed weight, for example 200 lbs."),
        field("hand", pa.string(), "Playing hand: Right or Left."),
        field("turned_pro", pa.int32(), "Year the golfer turned professional."),
        field("debut_year", pa.int32(), "Year of the golfer's tour debut."),
        field("college", pa.string(), "College attended, when any."),
        field("status", pa.string(), "ESPN roster status, for example Active."),
        field("season_scoring_average", pa.float64(), "Current-season scoring average in ESPN's summary."),
        field("season_earnings", MONEY, "Current-season official money in US dollars."),
        field("season_fedex_cup_points", pa.float64(), "Current-season FedExCup points."),
        field("season_fedex_cup_rank", pa.int32(), "Current-season FedExCup rank."),
    ]
)

WORLD_RANKINGS_SCHEMA = pa.schema(
    [
        field(
            "rank", pa.int32(), "Official World Golf Ranking position (1 = world number one).", nullable=False
        ),
        field("is_tied", pa.bool_(), "Whether the ranking position is shared."),
        field("owgr_player_id", ID, "OWGR player identifier (not the ESPN id).", nullable=False),
        field("player_name", pa.string(), "Golfer name."),
        field("first_name", pa.string(), "Given name."),
        field("last_name", pa.string(), "Family name."),
        field("country", pa.string(), "Country the golfer represents."),
        field("country_code", pa.string(), "IOC country code, for example USA or NIR."),
        field("region", pa.string(), "OWGR region, for example Europe or North America."),
        field("birth_date", pa.date32(), "Date of birth."),
        field("is_amateur", pa.bool_(), "Whether the golfer is an amateur."),
        field("total_points", pa.float64(), "Total ranking points over the two-year window."),
        field(
            "average_points",
            pa.float64(),
            "Average ranking points per event (total points divided by the applied divisor).",
        ),
        field("events_played", pa.int32(), "Events counted in the window (actual divisor)."),
        field(
            "divisor_applied",
            pa.int32(),
            "Number of events used as the divisor, after the 40-event minimum and 52-event maximum.",
        ),
        field("points_won", pa.float64(), "Ranking points won in the two-year window before decay (points)."),
        field("points_lost", pa.float64(), "Points lost to the ranking's time decay (negative)."),
        field("last_week_rank", pa.int32(), "Rank in the previous weekly ranking; NULL when unranked."),
        field(
            "end_last_year_rank",
            pa.int32(),
            "Rank at the end of the previous calendar year; NULL when the golfer was unranked then.",
        ),
    ]
)


# ---------------------------------------------------------------------------
# Scalar parsing helpers
# ---------------------------------------------------------------------------


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def to_int(value: Any) -> int | None:
    """Parse an integer-like API value without treating booleans as integers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value)) if isinstance(value, float | str) else int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def to_float(value: Any) -> float | None:
    """Parse a finite float, returning null for display markers."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def to_money(value: Any) -> Decimal | None:
    """Parse a dollar amount (number or "$1,234" string) to cents precision."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result.quantize(Decimal("0.01")) if result.is_finite() else None


def to_par(value: Any) -> int | None:
    """Parse a to-par display value: E is 0, +3 is 3, -12 is -12; other text is null."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.upper() == "E":
        return 0
    if re.fullmatch(r"[+-]\d+", text):
        return int(text)
    return None


def to_timestamp(value: Any) -> datetime | None:
    """Parse ESPN's ``2026-04-12T18:25Z`` style timestamps into UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def to_date(value: Any) -> date | None:
    """Return the calendar date of an ISO-8601 date or timestamp string."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def to_dmy_date(value: Any) -> date | None:
    """Parse ESPN's day/month/year date of birth, for example ``21/6/1996``."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _rank(value: Any) -> int | None:
    """A ranking position; OWGR uses 0 for "not ranked", which becomes NULL."""
    rank = to_int(value)
    return rank if rank is not None and rank > 0 else None


def _stat(statistics: Any, name: str) -> Mapping[str, Any]:
    return next(
        (_mapping(value) for value in _list(statistics) if _mapping(value).get("name") == name),
        {},
    )


# ---------------------------------------------------------------------------
# Flatteners
# ---------------------------------------------------------------------------


def tour_rows() -> list[dict[str, Any]]:
    """One row per supported tour."""
    return [{"tour": slug, **info} for slug, info in TOURS.items()]


def _round_strokes(linescores: Any) -> dict[int, int]:
    rounds: dict[int, int] = {}
    for raw in _list(linescores):
        line = _mapping(raw)
        if line.get("isPlayoff"):
            continue
        period = to_int(line.get("period"))
        strokes = to_int(line.get("value"))
        if period is None or not strokes or line.get("displayValue") == "-":
            continue
        rounds[period] = strokes
    return rounds


def flatten_leaderboard(payload: Mapping[str, Any], tour: str) -> list[dict[str, Any]]:
    """Flatten an ESPN site.web leaderboard to one row per entry, in leaderboard order."""
    rows: list[dict[str, Any]] = []
    for raw_event in _list(payload.get("events")):
        event = _mapping(raw_event)
        event_status = _mapping(_mapping(event.get("status")).get("type"))
        event_id = to_int(event.get("id"))
        if event_id is None:
            continue
        for raw_competition in _list(event.get("competitions"))[:1]:
            competition = _mapping(raw_competition)
            competitors = [_mapping(value) for value in _list(competition.get("competitors"))]
            competitors.sort(key=lambda value: to_int(value.get("sortOrder")) or 1_000_000)
            for competitor in competitors:
                entry_id = to_int(competitor.get("id"))
                if entry_id is None:
                    continue
                athlete = _mapping(competitor.get("athlete"))
                team = _mapping(competitor.get("team"))
                is_team = bool(team) and not athlete
                status = _mapping(competitor.get("status"))
                status_type = _mapping(status.get("type"))
                position = _mapping(status.get("position"))
                score = _mapping(competitor.get("score"))
                display = _clean(score.get("displayValue"))
                pre = status_type.get("state") == "pre" or event_status.get("state") == "pre"
                score_stat = _stat(competitor.get("statistics"), "scoreToPar")
                to_par_value = to_int(score_stat.get("value"))
                if to_par_value is None:
                    to_par_value = to_par(display)
                rounds = _round_strokes(competitor.get("linescores"))
                position_display = _clean(position.get("displayName"))
                position_number = (
                    to_int(position_display.removeprefix("T"))
                    if position_display and position_display.removeprefix("T").isdigit()
                    else None
                )
                total = to_int(score.get("value"))
                cup_points = _stat(competitor.get("statistics"), "cupPoints")
                rows.append(
                    {
                        "tour": tour,
                        "event_id": event_id,
                        "event_name": event.get("name"),
                        "season": to_int(_mapping(event.get("season")).get("year")),
                        "event_status": event_status.get("description"),
                        "event_completed": event_status.get("completed"),
                        "entry_id": entry_id,
                        "entry_type": "team" if is_team else "athlete",
                        "player_id": None if is_team else to_int(athlete.get("id")) or entry_id,
                        "player_name": team.get("displayName") if is_team else athlete.get("displayName"),
                        "player_short_name": None if is_team else athlete.get("shortName"),
                        "country": _mapping(athlete.get("flag")).get("alt"),
                        "amateur": competitor.get("amateur", athlete.get("amateur")),
                        "position": position_display,
                        "position_number": position_number,
                        "is_tied": position.get("isTie"),
                        "sort_order": to_int(competitor.get("sortOrder")),
                        "status": status_type.get("description"),
                        "status_code": status_type.get("name"),
                        "score_to_par": None if pre else to_par_value,
                        "score_to_par_display": None if pre else display,
                        "total_strokes": total if total else None,
                        "round_1": rounds.get(1),
                        "round_2": rounds.get(2),
                        "round_3": rounds.get(3),
                        "round_4": rounds.get(4),
                        "rounds_played": len([key for key in rounds if 1 <= key <= 4]),
                        "current_round": to_int(status.get("period")),
                        "thru": to_int(status.get("thru")),
                        "in_playoff": bool(status.get("playoff")),
                        "tee_time": to_timestamp(status.get("teeTime")),
                        "earnings": to_money(competitor.get("earnings")),
                        "fedex_cup_points": to_float(cup_points.get("value")),
                        "movement": to_int(competitor.get("movement")),
                    }
                )
    return rows


def flatten_course_holes(payload: Mapping[str, Any], tour: str) -> list[dict[str, Any]]:
    """Flatten the course setup carried on a leaderboard to one row per course hole."""
    rows: list[dict[str, Any]] = []
    for raw_event in _list(payload.get("events")):
        event = _mapping(raw_event)
        event_id = to_int(event.get("id"))
        for raw_course in _list(event.get("courses")):
            course = _mapping(raw_course)
            course_id = to_int(course.get("id"))
            if event_id is None or course_id is None:
                continue
            for raw_hole in _list(course.get("holes")):
                hole = _mapping(raw_hole)
                number = to_int(hole.get("number"))
                if number is None:
                    continue
                rows.append(
                    {
                        "tour": tour,
                        "event_id": event_id,
                        "course_id": course_id,
                        "course_name": course.get("name"),
                        "host_course": course.get("host"),
                        "course_par": to_int(course.get("shotsToPar")),
                        "course_yards": to_int(course.get("totalYards")),
                        "hole": number,
                        "par": to_int(hole.get("shotsToPar")),
                        "yards": to_int(hole.get("totalYards")),
                    }
                )
    return rows


def _winner(competitors: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The tournament winner on a season scoreboard.

    ESPN flags the winner on some tours. Elsewhere ``order`` is the finishing
    order, with rare exceptions: the 2026 Zurich Classic lists the winning -31
    team at order 35. When the leading entries' scores rise with ``order``
    (ordinary stroke play), the lowest score to par among entries that
    completed every regulation round wins, ``order`` breaking ties for
    playoffs. Stableford (higher is better), match-play and cup events do not
    order that way and keep ``order`` 1.
    """
    flagged = [value for value in competitors if value.get("winner") is True]
    if flagged:
        return flagged[0]
    if any("winner" in value for value in competitors):
        return {}
    ranked = sorted(competitors, key=lambda value: to_int(value.get("order")) or 1_000_000)
    first = ranked[0] if ranked else {}
    leading = [to_par(value.get("score")) for value in ranked[:5]]
    scores = [value for value in leading if value is not None]
    stroke_play = len(scores) == len(leading) >= 2 and scores == sorted(scores)
    regulation = _regulation_rounds(competitors)
    if not stroke_play or regulation is None:
        return first if to_int(first.get("order")) == 1 else {}
    finishers = [
        value
        for value in ranked
        if to_par(value.get("score")) is not None
        and len([period for period in _scored_periods(value) if period <= regulation]) == regulation
    ]
    return min(finishers, key=lambda value: to_par(value.get("score")) or 0) if finishers else first


def _scored_periods(competitor: Mapping[str, Any]) -> list[int]:
    """Round numbers (including playoff periods) with a recorded stroke total."""
    periods = []
    for raw in _list(competitor.get("linescores")):
        line = _mapping(raw)
        period = to_int(line.get("period"))
        if period is not None and period < 100 and to_int(line.get("value")):
            periods.append(period)
    return periods


def _regulation_rounds(competitors: Sequence[Mapping[str, Any]]) -> int | None:
    """The usual number of scored rounds among the top finishers (the field's regulation length).

    Playoff participants carry an extra scored period, but they are a small
    minority of the top 20, so the most common round count is regulation.
    """
    ranked = sorted(competitors, key=lambda value: to_int(value.get("order")) or 1_000_000)[:20]
    counts = Counter(max(periods) for value in ranked if (periods := _scored_periods(value)))
    # The most common round count wins; a tie goes to the shorter (regulation) length.
    return min(counts, key=lambda rounds: (-counts[rounds], rounds)) if counts else None


def _format_par(value: int) -> str:
    return "E" if value == 0 else f"{value:+d}"


def _winning_score(
    winner: Mapping[str, Any], competitors: Sequence[Mapping[str, Any]]
) -> tuple[str | None, bool]:
    """The winner's regulation score and whether the event went to a playoff.

    ESPN's season scoreboard folds playoff holes into many winners' ``score``
    (for example -25 for a -23 total plus a two-hole playoff at -2), but not
    all (the 2025 Masters shows -11). When there was a playoff, the score is
    rebuilt from the regulation rounds' to-par values so it matches the
    official result.
    """
    display = _clean(winner.get("score"))
    regulation = _regulation_rounds(competitors)
    if regulation is None:
        return display, False
    lines = [_mapping(raw) for raw in _list(winner.get("linescores"))]
    periods = {to_int(line.get("period")): line for line in lines}
    # Some tours list a playoff period without strokes (value 0, display "-").
    playoff = any(period is not None and regulation < period < 100 for period in periods)
    round_to_par = [
        to_par(periods.get(number, {}).get("displayValue")) for number in range(1, regulation + 1)
    ]
    if playoff and to_par(display) is not None and all(value is not None for value in round_to_par):
        display = _format_par(sum(value for value in round_to_par if value is not None))
    return display, playoff


def flatten_schedule(payload: Mapping[str, Any], tour: str) -> list[dict[str, Any]]:
    """Flatten a season scoreboard to one row per tournament, in date order."""
    season_info = _mapping(payload.get("season"))
    rows: list[dict[str, Any]] = []
    for raw_event in _list(payload.get("events")):
        event = _mapping(raw_event)
        event_id = to_int(event.get("id"))
        if event_id is None:
            continue
        status = _mapping(_mapping(event.get("status")).get("type"))
        competitions = [_mapping(value) for value in _list(event.get("competitions"))]
        first = competitions[0] if competitions else {}
        competitors = [_mapping(value) for value in _list(first.get("competitors"))]
        completed = bool(status.get("completed"))
        winner = _winner(competitors) if completed else {}
        winner_entity = _mapping(winner.get("athlete")) or _mapping(winner.get("team"))
        winning_score, playoff = _winning_score(winner, competitors) if winner else (None, None)
        rows.append(
            {
                "tour": tour,
                "season": to_int(_mapping(event.get("season")).get("year"))
                or to_int(season_info.get("year")),
                "event_id": event_id,
                "event_name": event.get("name"),
                "start_date": to_date(event.get("date")),
                "end_date": to_date(event.get("endDate")),
                "status": status.get("description"),
                "state": status.get("state"),
                "completed": completed,
                "field_size": len(competitors) if competitors else None,
                "winner_id": to_int(winner.get("id")) if winner else None,
                "winner_name": winner_entity.get("displayName") if winner else None,
                "winning_score": winning_score,
                "winning_score_to_par": to_par(winning_score),
                "playoff": playoff,
                "broadcast": _clean(first.get("broadcast")),
            }
        )
    rows.sort(key=lambda row: (row["start_date"] or date.max, row["event_id"]))
    return rows


def flatten_scorecard(
    payload: Mapping[str, Any], *, tour: str, event_id: int, player_id: int
) -> list[dict[str, Any]]:
    """Flatten core-API linescores to one row per hole played."""
    rows: list[dict[str, Any]] = []
    for raw_round in _list(payload.get("items")):
        round_line = _mapping(raw_round)
        round_number = to_int(round_line.get("period"))
        if round_number is None:
            continue
        for raw_hole in _list(round_line.get("linescores")):
            hole = _mapping(raw_hole)
            number = to_int(hole.get("period"))
            strokes = to_int(hole.get("value"))
            par = to_int(hole.get("par"))
            if number is None:
                continue
            rows.append(
                {
                    "tour": tour,
                    "event_id": event_id,
                    "player_id": player_id,
                    "round": round_number,
                    "hole": number,
                    "par": par,
                    "strokes": strokes,
                    "score_to_par": strokes - par if strokes is not None and par is not None else None,
                    "score_type": _mapping(hole.get("scoreType")).get("name"),
                    "round_strokes": to_int(round_line.get("value")),
                    "round_score_to_par_display": _clean(round_line.get("displayValue")),
                    "round_out_strokes": to_int(round_line.get("outScore")),
                    "round_in_strokes": to_int(round_line.get("inScore")),
                    "tee_time": to_timestamp(round_line.get("teeTime")),
                    "course_id": to_int(round_line.get("courseId")),
                }
            )
    rows.sort(key=lambda row: (row["round"], row["hole"]))
    return rows


_STAT_COLUMNS = {
    "amount": ("earnings", to_money),
    "cupPoints": ("fedex_cup_points", to_float),
    "tournamentsPlayed": ("events_played", to_int),
    "roundsPlayed": ("rounds_played", to_int),
    "cutsMade": ("cuts_made", to_int),
    "topTenFinishes": ("top_ten_finishes", to_int),
    "wins": ("wins", to_int),
    "scoringAverage": ("scoring_average", to_float),
    "yardsPerDrive": ("driving_distance", to_float),
    "driveAccuracyPct": ("driving_accuracy_pct", to_float),
    "greensInRegPct": ("greens_in_regulation_pct", to_float),
    "strokesPerHole": ("putts_per_hole", to_float),
    "savePct": ("sand_save_pct", to_float),
    "birdiesPerRound": ("birdies_per_round", to_float),
}


_MEASURED_COLUMNS = (
    "rounds_played",
    "scoring_average",
    "driving_distance",
    "driving_accuracy_pct",
    "greens_in_regulation_pct",
    "putts_per_hole",
    "sand_save_pct",
    "birdies_per_round",
)


def flatten_season_stats(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten ESPN's byathlete statistics to one row per golfer.

    Values are matched to columns by the category's ``names`` list, never by
    position alone, so a reordered upstream category cannot shift columns.
    """
    category_names: dict[str, list[str]] = {
        str(_mapping(value).get("name")): [str(name) for name in _list(_mapping(value).get("names"))]
        for value in _list(payload.get("categories"))
    }
    season = to_int(_mapping(payload.get("requestedSeason")).get("year"))
    rows: list[dict[str, Any]] = []
    for raw in _list(payload.get("athletes")):
        entry = _mapping(raw)
        athlete = _mapping(entry.get("athlete"))
        player_id = to_int(athlete.get("id"))
        if player_id is None:
            continue
        row: dict[str, Any] = {
            "season": season,
            "player_id": player_id,
            "player_name": athlete.get("displayName"),
            "first_name": athlete.get("firstName"),
            "last_name": athlete.get("lastName"),
            "country": _mapping(athlete.get("flag")).get("alt"),
            "age": to_int(athlete.get("age")),
        }
        for raw_category in _list(entry.get("categories")):
            category = _mapping(raw_category)
            names = category_names.get(str(category.get("name")), [])
            values = _list(category.get("values"))
            ranks = _list(category.get("ranks"))
            for index, name in enumerate(names):
                if name in _STAT_COLUMNS and index < len(values):
                    column, parse = _STAT_COLUMNS[name]
                    row[column] = parse(values[index])
                if name == "cupPoints" and index < len(ranks):
                    row["fedex_cup_rank"] = to_int(ranks[index])
        if not row.get("rounds_played"):
            # Golfers without measured rounds (mostly non-members) carry 0 for
            # every per-round statistic; 0 is not a real average, so emit NULL.
            row.update(dict.fromkeys(_MEASURED_COLUMNS))
        last = _mapping(entry.get("lastTournament"))
        row["last_tournament_name"] = last.get("name")
        row["last_tournament_end_date"] = to_date(last.get("endDate"))
        rows.append(row)
    return rows


def flatten_player(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten an ESPN golf athlete profile to a single row (or none)."""
    athlete = _mapping(payload.get("athlete"))
    player_id = to_int(athlete.get("id"))
    if player_id is None:
        return []
    birth = _mapping(athlete.get("birthPlace"))
    summary = {
        str(_mapping(value).get("name")): _mapping(value)
        for value in _list(_mapping(athlete.get("statsSummary")).get("statistics"))
    }
    return [
        {
            "player_id": player_id,
            "player_name": athlete.get("displayName"),
            "first_name": athlete.get("firstName"),
            "last_name": athlete.get("lastName"),
            "birth_date": to_dmy_date(athlete.get("displayDOB")),
            "age": to_int(athlete.get("age")),
            "gender": _clean(athlete.get("gender")),
            "citizenship": _clean(athlete.get("citizenship")),
            "birth_city": _clean(birth.get("city")),
            "birth_state": _clean(birth.get("state")),
            "birth_country": _clean(birth.get("country")),
            "height": _clean(athlete.get("displayHeight")),
            "weight": _clean(athlete.get("displayWeight")),
            "hand": _clean(_mapping(athlete.get("hand")).get("displayValue")),
            "turned_pro": to_int(athlete.get("turnedPro")),
            "debut_year": to_int(athlete.get("debutYear")),
            "college": _clean(_mapping(athlete.get("college")).get("name")),
            "status": _clean(_mapping(athlete.get("status")).get("name")),
            "season_scoring_average": to_float(summary.get("scoringAverage", {}).get("value")),
            "season_earnings": to_money(summary.get("amount", {}).get("value")),
            "season_fedex_cup_points": to_float(summary.get("cupPoints", {}).get("value")),
            "season_fedex_cup_rank": to_int(summary.get("cupPoints", {}).get("rank")),
        }
    ]


def flatten_world_rankings(rankings: Iterable[Any]) -> list[dict[str, Any]]:
    """Flatten OWGR ranking entries to one row per ranked golfer."""
    rows: list[dict[str, Any]] = []
    for raw in rankings:
        entry = _mapping(raw)
        player = _mapping(entry.get("player"))
        country = _mapping(player.get("country"))
        rank = to_int(entry.get("rank"))
        player_id = to_int(player.get("id"))
        if rank is None or player_id is None:
            continue
        rows.append(
            {
                "rank": rank,
                "is_tied": entry.get("isTied"),
                "owgr_player_id": player_id,
                "player_name": player.get("fullName"),
                "first_name": player.get("firstName"),
                "last_name": player.get("lastName"),
                "country": country.get("name"),
                "country_code": _clean(country.get("iocCode")),
                "region": _mapping(country.get("region")).get("name"),
                "birth_date": to_date(player.get("birthDate")),
                "is_amateur": player.get("isAmateur"),
                "total_points": to_float(entry.get("pointsTotal")),
                "average_points": to_float(entry.get("pointsAverage")),
                "events_played": to_int(entry.get("divisorActual")),
                "divisor_applied": to_int(entry.get("divisorApplied")),
                "points_won": to_float(entry.get("pointsWon")),
                "points_lost": to_float(entry.get("pointsLost")),
                "last_week_rank": _rank(entry.get("lastWeekRank")),
                "end_last_year_rank": _rank(entry.get("endLastYearRank")),
            }
        )
    return rows


def batch_from_rows(rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> pa.RecordBatch:
    """Build a record batch in the declared (possibly projected) column order."""
    return pa.RecordBatch.from_arrays(
        [pa.array([row.get(value.name) for row in rows], type=value.type) for value in schema],
        schema=schema,
    )
