"""Tests for the SQL-facing catalog shape and discovery metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path

from vgi_golf.players import CurrentPlayerStatsFunction, SeasonPlayerStatsFunction
from vgi_golf.rankings import CurrentWorldRankingsFunction
from vgi_golf.tournaments import (
    CurrentLeaderboardFunction,
    CurrentScheduleFunction,
    LeaderboardFunction,
    PlayerScorecardFunction,
    ToursFunction,
)
from vgi_golf.worker import GOLF_CATALOG

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = GOLF_CATALOG.schemas[0]


def test_catalog_surface() -> None:
    assert GOLF_CATALOG.name == "golf"
    assert SCHEMA.path == ["main"]
    assert {function.Meta.name for function in SCHEMA.functions} == {
        "all_tours",
        "leaderboard",
        "all_current_leaderboard",
        "schedule",
        "all_current_schedule",
        "player_scorecard",
        "course_holes",
        "season_player_stats",
        "all_current_player_stats",
        "player",
        "world_rankings",
        "all_current_world_rankings",
    }
    assert {table.name: table.function for table in SCHEMA.tables} == {
        "tours": ToursFunction,
        "current_leaderboard": CurrentLeaderboardFunction,
        "current_schedule": CurrentScheduleFunction,
        "current_player_stats": CurrentPlayerStatsFunction,
        "current_world_rankings": CurrentWorldRankingsFunction,
    }


def test_optional_arguments_are_named() -> None:
    leaderboard = {parameter.name: parameter for parameter in LeaderboardFunction.get_metadata().parameters}
    assert set(leaderboard) == {"tour", "event_id"}
    assert not any(parameter.required for parameter in leaderboard.values())
    stats = {parameter.name for parameter in SeasonPlayerStatsFunction.get_metadata().parameters}
    assert stats == {"season"}
    scorecard = {parameter.name: parameter for parameter in PlayerScorecardFunction.get_metadata().parameters}
    assert scorecard["event_id"].required and scorecard["player_id"].required
    assert not scorecard["tour"].required


def _key_of(table, kind: str) -> list[tuple[str, ...]]:
    return [
        tuple(name for path in record.definition.column_paths for name in path)
        for record in table.constraints
        if record.definition.kind == kind
    ]


def test_table_keys() -> None:
    keys = {table.name: _key_of(table, "PRIMARY_KEY") for table in SCHEMA.tables}
    assert keys == {
        "tours": [("tour",)],
        "current_leaderboard": [("event_id", "entry_id")],
        "current_schedule": [("event_id",)],
        "current_player_stats": [("player_id",)],
        "current_world_rankings": [("owgr_player_id",)],
    }


def test_every_object_has_title_docs_and_examples() -> None:
    for table in SCHEMA.tables:
        for tag in ("vgi.title", "vgi.doc_llm", "vgi.doc_md", "vgi.keywords", "vgi.example_queries"):
            assert table.tags.get(tag), (table.name, tag)
        assert table.column_comments
    for function in SCHEMA.functions:
        tags = function.Meta.tags
        for tag in (
            "vgi.title",
            "vgi.doc_llm",
            "vgi.doc_md",
            "vgi.example_queries",
            "vgi.result_columns_schema",
        ):
            assert tags.get(tag), (function.Meta.name, tag)
        assert len(tags["vgi.title"]) <= 32, function.Meta.name
        assert all("golf.main." in example["sql"] for example in json.loads(tags["vgi.example_queries"]))
    for tag in ("vgi.title", "vgi.subtitle", "vgi.doc_llm", "vgi.doc_md", "vgi.keywords", "vgi.copyright"):
        assert GOLF_CATALOG.tags.get(tag), tag


def test_discovery_contract_matches_agent_test_sidecar() -> None:
    examples = json.loads(GOLF_CATALOG.tags["vgi.executable_examples"])
    tasks = json.loads(GOLF_CATALOG.tags["vgi.agent_test_tasks"])
    assert all({"name", "description", "sql", "expected_result"} <= example.keys() for example in examples)
    assert 6 <= len(tasks) <= 12
    assert all(set(task) == {"name", "prompt"} for task in tasks)
    sidecar = (ROOT / "vgi-agent-tests.yaml").read_text()
    graded = re.findall(r"^  - name: (\S+)$", sidecar, re.MULTILINE)
    assert graded == [task["name"] for task in tasks]
    blocks = re.split(r"^  - name: ", sidecar, flags=re.MULTILINE)[1:]
    for block in blocks:
        assert "reference_sql:" in block or "check_sql:" in block
        if "golf.main.current_" in block:
            assert "check_sql:" in block, "live-data tasks must grade through check_sql"
