"""Concise app-console logging, with secret redaction at both call and output."""

from datetime import datetime
import logging
import os
import sys
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("steward")
_secrets = set()


def protect(*values):
    for value in values:
        if isinstance(value, str) and value:
            _secrets.add(value)


def redact(value):
    text = str(value)
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


class SafeFormatter(logging.Formatter):
    def __init__(self, zone="UTC"):
        super().__init__("%(asctime)s %(levelname)s [%(run_id)s] %(message)s")
        self.zone = ZoneInfo(zone)

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, self.zone).isoformat(timespec="seconds")

    def format(self, record):
        if not hasattr(record, "run_id"):
            record.run_id = "app"
        return redact(super().format(record))


def configure(zone="UTC"):
    protect(os.environ.get("SUPERVISOR_TOKEN"))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeFormatter(zone))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def event(message, run_id=None, level=logging.INFO):
    LOGGER.log(level, redact(message), extra={"run_id": run_id or "app"})


def announce_policy(cfg):
    modes = {"observe": "observação", "test": "teste", "production": "produção"}
    event(
        f"Configuração pronta; modo={modes[cfg['execution_mode']]}; origem={cfg['source_mount']}; destino={cfg['destination_mount']}"
    )
    state = "ATIVA" if cfg["schedule_enabled"] else "DESATIVADA"
    event(
        f"Agenda disponível e pronta: {state}; hora={cfg['schedule_time']}; fuso={cfg['timezone']}; checksum={cfg['checksum_frequency']}"
    )
    event(
        "Encriptação: chave configurada"
        if cfg.get("encryption_password")
        else "Encriptação: chave ainda não configurada"
    )
