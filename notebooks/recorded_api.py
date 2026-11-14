"""Replay two unchanged repository fixtures through the worker's supported URLs."""

from __future__ import annotations

import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

ENVIRONMENTS = ("GOLF_ESPN_SITE_URL", "GOLF_ESPN_WEB_URL", "GOLF_ESPN_CORE_URL", "GOLF_OWGR_URL")


class RecordedAPI:
    """Serve only the documented event/player fixture requests on loopback."""

    def __init__(self, directory: Path) -> None:
        """Verify bundled fixture hashes, then start a bounded local server."""
        provenance = json.loads((directory / "provenance.json").read_text())
        responses = {}
        for item in provenance["files"]:
            raw = (directory / item["file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError(f"Fixture hash mismatch: {item['file']}")
            responses[item["file"]] = raw

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                """Keep fixture requests out of notebook outputs."""

            def do_GET(self) -> None:
                """Return one exact fixture route, rejecting unsupported requests."""
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                name = None
                if parsed.path == "/web/site/v2/sports/golf/leaderboard" and query == {
                    "league": ["pga"],
                    "event": ["401811941"],
                }:
                    name = "leaderboard_pga_masters_2026.json"
                elif parsed.path == (
                    "/core/leagues/pga/events/401811941/competitions/401811941/competitors/9478/linescores"
                ) and query == {"limit": ["25"]}:
                    name = "linescores_masters_scheffler.json"
                if name is None:
                    self.send_error(404, "Outside bundled fixture coverage")
                    return
                body = responses[name]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "max-age=60")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        root = f"http://127.0.0.1:{self.server.server_port}"
        for key, suffix in zip(ENVIRONMENTS, ("site", "web", "core", "owgr"), strict=True):
            os.environ[key] = f"{root}/{suffix}"

    def close(self) -> None:
        """Stop the local fixture server and clear its endpoint overrides."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for key in ENVIRONMENTS:
            os.environ.pop(key, None)


def configure(mode: str) -> RecordedAPI | None:
    """Clear inherited URL overrides and explicitly select recorded or live data."""
    if mode not in ("recorded", "live"):
        raise ValueError("DATA_MODE must be 'recorded' or 'live'.")
    for key in ENVIRONMENTS:
        os.environ.pop(key, None)
    return RecordedAPI(Path("fixtures")) if mode == "recorded" else None
