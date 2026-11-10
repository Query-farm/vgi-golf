"""Shared building blocks for the golf table functions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa
from vgi.arguments import Arg
from vgi.cache_control import CacheControl
from vgi_rpc.rpc import OutputCollector

from vgi_golf.api import TOURS
from vgi_golf.meta import result_columns_schema
from vgi_golf.schemas import batch_from_rows

TOUR_ARG = Arg(
    "tour",
    default="pga",
    arrow_type=pa.string(),
    choices=tuple(TOURS),
    doc=(
        "Tour slug: pga (PGA Tour), eur (DP World Tour), liv (LIV Golf), lpga (LPGA Tour), "
        "champions-tour (PGA Tour Champions) or ntw (Korn Ferry Tour)."
    ),
)


def emit_rows(
    rows: Sequence[Mapping[str, Any]], output_schema: pa.Schema, out: OutputCollector, ttl: int
) -> None:
    """Emit one batch carrying the upstream freshness window, then finish."""
    out.emit(batch_from_rows(rows, output_schema), cache_control=CacheControl(ttl=max(1, ttl)))
    out.finish()


def function_tags(
    *,
    category: str,
    title: str,
    llm: str,
    md: str,
    schema: pa.Schema,
    examples: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """Discovery tags shared by every function: title, docs, examples, result schema."""
    return {
        "provider": "espn-golf" if category != "rankings" else "owgr",
        "vgi.category": category,
        "vgi.title": title,
        "vgi.result_columns_schema": result_columns_schema(schema),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps(
            [{"description": description, "sql": sql} for description, sql in examples]
        ),
    }
