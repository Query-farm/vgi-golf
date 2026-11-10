"""Hermetic end-to-end coverage through Haybarn and the VGI transport.

A local HTTP server replays the recorded ESPN and OWGR fixtures, and every
upstream base URL is pointed at it, so the test needs no network beyond
installing the Haybarn CLI and the community VGI extension.
"""

from __future__ import annotations

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture_for(path: str, query: dict[str, list[str]]) -> str | None:
    if path.startswith("/site/") and path.endswith("/scoreboard"):
        return "scoreboard_pga_2025.json" if "dates" in query else "scoreboard_pga_current.json"
    if path == "/web/site/v2/sports/golf/leaderboard":
        return "leaderboard_pga_masters_2026.json"
    if path.startswith("/core/leagues/") and path.endswith("/linescores"):
        return "linescores_masters_scheffler.json"
    if path == "/web/common/v3/sports/golf/pga/statistics/byathlete":
        return "stats_pga_2026.json"
    if path == "/web/common/v3/sports/golf/pga/athletes/9478":
        return "athlete_scheffler.json"
    if path == "/owgr/rankings/getRankings":
        return "owgr_rankings_top5.json"
    return None


class _GolfHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        name = _fixture_for(parsed.path, parse_qs(parsed.query))
        if name is None:
            self.send_error(404)
            return
        body = (FIXTURES / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "max-age=60")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_haybarn_attaches_and_queries_every_table_and_backing_scan() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GolfHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = Path(__file__).resolve().parent.parent
    location = f"uv run --project {project} vgi-golf"
    sql = (
        f"INSTALL vgi FROM community; LOAD vgi; ATTACH 'golf' (TYPE vgi, LOCATION '{location}'); "
        "SELECT "
        "(SELECT count(*) FROM golf.main.tours) AS tours, "
        "(SELECT count(*) FROM golf.main.current_leaderboard) AS leaderboard, "
        "(SELECT count(*) FROM golf.main.current_schedule) AS schedule, "
        "(SELECT count(*) FROM golf.main.current_player_stats) AS stats, "
        "(SELECT count(*) FROM golf.main.current_world_rankings) AS rankings, "
        "(SELECT count(*) FROM golf.main.all_current_leaderboard()) AS leaderboard_scan, "
        "(SELECT count(*) FROM golf.main.all_tours()) AS tours_scan, "
        "(SELECT count(*) FROM golf.main.all_current_schedule()) AS schedule_scan, "
        "(SELECT count(*) FROM golf.main.all_current_player_stats()) AS stats_scan, "
        "(SELECT count(*) FROM golf.main.all_current_world_rankings()) AS rankings_scan, "
        "(SELECT count(*) FROM golf.main.schedule(season := 2025) WHERE completed) AS season_events, "
        "(SELECT count(*) FROM golf.main.season_player_stats(season := 2026)) AS season_stats, "
        "(SELECT count(*) FROM golf.main.world_rankings(max_rank := 3)) AS top_three, "
        "(SELECT count(*) FROM golf.main.player_scorecard(401811941, 9478)) AS holes, "
        "(SELECT count(*) FROM golf.main.course_holes(401811941)) AS course, "
        "(SELECT count(*) FROM golf.main.player(9478)) AS profile, "
        "(SELECT player_name FROM golf.main.leaderboard(event_id := 401811941) "
        "WHERE sort_order = 1) AS winner;"
    )
    base = f"http://127.0.0.1:{server.server_port}"
    env = {
        **{key: value for key, value in os.environ.items() if key != "AI_AGENT"},
        "GOLF_ESPN_SITE_URL": f"{base}/site",
        "GOLF_ESPN_WEB_URL": f"{base}/web",
        "GOLF_ESPN_CORE_URL": f"{base}/core",
        "GOLF_OWGR_URL": f"{base}/owgr",
    }
    try:
        result = subprocess.run(
            ["uvx", "haybarn-cli", ":memory:", "-csv", "-c", sql],
            cwd=project,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert (
        "tours,leaderboard,schedule,stats,rankings,leaderboard_scan,tours_scan,schedule_scan,stats_scan,"
        "rankings_scan,season_events,season_stats,top_three,holes,course,profile,winner" in result.stdout
    ), result.stdout + result.stderr
    assert "6,8,3,4,5,8,6,3,4,5,3,4,3,72,18,1,Rory McIlroy" in result.stdout, result.stdout + result.stderr
