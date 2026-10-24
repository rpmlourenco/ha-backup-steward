"""Atomic durable state, private credentials, and bounded job events."""

import json
import os
from pathlib import Path
import secrets
from datetime import datetime, timezone
import logging

from . import telemetry


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
        telemetry.protect(self.identity["api_token"])

    def update(self, **kwargs):
        previous = {key: self.data.get(key) for key in kwargs}
        self.data.update(kwargs)
        atomic_json(self.path, self.data)
        run_id = self.data.get("run_id")
        if kwargs.get("state") != previous.get("state"):
            status = kwargs.get("state")
            if status == "running":
                telemetry.event(
                    f"Execução iniciada; operação={self.data.get('mode')}; modo={self.data.get('execution_mode')}",
                    run_id,
                )
            elif status == "success":
                telemetry.event(
                    f"Execução concluída; L2={self.data.get('l2')}; L3={self.data.get('l3')}; retenção={self.data.get('retention')}; destino desligado em segurança",
                    run_id,
                )
            elif status == "interrupted":
                telemetry.event(
                    "Execução interrompida ou recuperação concluída; consultar o estado antes de repetir",
                    run_id,
                    logging.WARNING,
                )
        for key, label in (("l2", "Resultado L2"), ("l3", "Resultado L3"), ("retention", "Retenção L2")):
            if key in kwargs and kwargs[key] != previous[key] and kwargs[key] not in ("pending", "skipped"):
                telemetry.event(
                    f"{label}: {kwargs[key]}",
                    run_id,
                    logging.WARNING if kwargs[key] == "failed" else logging.INFO,
                )

    def event(self, stage, message, level=None):
        message = telemetry.redact(message)
        events = self.data.get("events", [])[-99:]
        events.append({"stage": stage, "message": message, "time": datetime.now(timezone.utc).isoformat()})
        self.update(stage=stage, events=events)
        if level is None:
            level = (
                logging.ERROR
                if stage == "failure_detail"
                else (
                    logging.WARNING
                    if stage in ("recovery_required", "notification_pending", "temporary_retained")
                    else logging.INFO
                )
            )
        telemetry.event(f"{stage}: {message}", self.data.get("run_id"), level)

    def public(self, busy=False):
        # Backup job IDs and configuration contain no credentials. No raw API responses are persisted.
        return dict(
            self.data,
            instance_id=self.identity["instance_id"],
            recovery_required=bool(self.data.get("needs_cleanup")) and not busy,
        )
