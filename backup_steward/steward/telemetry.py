"""Concise app-console logging, with secret redaction at both call and output."""

from datetime import datetime
import logging
import os
import re
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
        "L1 nativo: " + ("integrado no ciclo, antes do L2/L3" if cfg.get("l1_enabled") else "não integrado")
    )
    event(
        "Encriptação: chave configurada"
        if cfg.get("encryption_password")
        else "Encriptação: chave ainda não configurada"
    )


def process_text(value):
    """Bounded process diagnostics; omit URLs, auth schemes and named secrets."""
    text = redact(value)
    text = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+", "[URL redacted]", text)
    text = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [redacted]", text)
    text = re.sub(
        r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\b(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1\2[redacted]",
        text,
    )
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:400]
