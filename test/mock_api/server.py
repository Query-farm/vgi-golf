"""Serve the recorded ESPN and OWGR fixtures over HTTP for the SQL tests.

``ci/run-integration.sh`` starts this server, points every upstream base URL
at it, and runs ``test/sql`` through DuckDB, so the suite is hermetic.
Prints ``PORT:<n>`` once listening.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


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


def main() -> None:
    """Serve until killed."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GolfHandler)
    print(f"PORT:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
