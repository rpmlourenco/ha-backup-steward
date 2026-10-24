"""Loopback-only interface preview with simulated observation data; never calls HA."""

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backup_steward"))
from steward.config import DEFAULTS  # noqa: E402 - repository-local preview bootstrap


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "backup_steward/steward/static"), **kwargs)

    def do_GET(self):
        if self.path == "/v1/status":
            value = {
                "version": "0.1.0",
                "busy": False,
                "mode": "observe",
                "state": {"state": "idle", "events": [], "needs_cleanup": False},
                "schedule_enabled": False,
                "schedule_time": "04:00",
                "timezone": "Europe/Madrid",
                "checksum_frequency": "monthly",
                "next_run": None,
                "daily_retention_days": 3,
            }
        elif self.path == "/v1/config":
            value = DEFAULTS
        elif self.path.startswith("/static/"):
            self.path = self.path.removeprefix("/static")
            return super().do_GET()
        else:
            return super().do_GET()
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"Simulated UI at http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
