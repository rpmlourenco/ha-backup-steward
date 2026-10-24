"""Atomic durable state, private credentials, and bounded job events."""

import json
import os
from pathlib import Path
import secrets


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.data = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {
                "state": "idle",
                "stage": "idle",
                "events": [],
                "last_checksum": None,
                "last_success": None,
                "last_schedule_date": None,
                "needs_cleanup": False,
            }
        )
        identity = self.directory / "identity.json"
        if not identity.exists():
            atomic_json(
                identity, {"instance_id": secrets.token_hex(16), "api_token": secrets.token_urlsafe(32)}
            )
        self.identity = json.loads(identity.read_text(encoding="utf-8"))

    def update(self, **kwargs):
        self.data.update(kwargs)
        atomic_json(self.path, self.data)

    def event(self, stage, message):
        events = self.data.get("events", [])[-99:]
        events.append({"stage": stage, "message": message})
        self.update(stage=stage, events=events)

    def public(self):
        # Backup job IDs and configuration contain no credentials. No raw API responses are persisted.
        return dict(self.data, instance_id=self.identity["instance_id"])
