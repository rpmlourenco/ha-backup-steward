"""Validated policy. There are no machine-specific identifiers in the program."""

import copy
import re
import uuid
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo


DEFAULTS = {
    "execution_mode": "observe",
    "legacy_scheduler_disabled": False,
    "schedule_enabled": False,
    "schedule_time": "04:00",
    "timezone": "Europe/Madrid",
    "checksum_frequency": "monthly",
    "checksum_weekday": 6,
    "checksum_month_week": 1,
    "source_uuid": "",
    "source_mount": "/media/NASRUI",
    "destination_uuid": "",
    "destination_mount": "/media/HAL3",
    "power_entity": "",
    "additional_sources": ["/media/immich", "/media/filebrowser"],
    "l2_directory": "backups/home-assistant/level-2",
    "test_directory": "backup-steward-test",
    "l2_keep": 3,
    "history_days": 30,
    "integrity_error_limit": 1000,
    "min_free_gib": 100,
    "max_delete_files": 1000,
    "max_delete_gib": 100,
    "max_missing_fraction": 0.2,
    "transfers": 2,
    "checkers": 4,
    "backup_timeout_minutes": 360,
    "device_wait_seconds": 45,
    "legacy_scripts": ["script.level2_run_backup", "script.level3_run_backup"],
}
SECRET_KEYS = {"api_token", "encryption_password"}


def media_path(value):
    p = PurePosixPath(value)
    if not value.startswith("/media/") or len(p.parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]+", p.name):
        raise ValueError("Mounts must be direct children of /media with simple names")
    return str(p)


def relative_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or any(x in (".", "..") for x in value.split("/")):
        raise ValueError("Expected a confined relative path")
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in p.parts):
        raise ValueError("Invalid relative path")
    return value


def validate(raw):
    unknown = set(raw) - set(DEFAULTS) - SECRET_KEYS
    if unknown:
        raise ValueError("Unknown configuration options: " + ", ".join(sorted(unknown)))
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(raw)
    for key in ("legacy_scheduler_disabled", "schedule_enabled"):
        if type(cfg[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    if cfg["execution_mode"] not in ("observe", "test", "production"):
        raise ValueError("Invalid execution mode")
    if cfg["checksum_frequency"] not in ("monthly", "weekly", "disabled"):
        raise ValueError("Invalid checksum frequency")
    ZoneInfo(cfg["timezone"])
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", cfg["schedule_time"]):
        raise ValueError("Time must use HH:MM")
    for key, lo, hi in (
        ("checksum_weekday", 0, 6),
        ("checksum_month_week", 1, 4),
        ("l2_keep", 1, 365),
        ("history_days", 1, 3650),
        ("integrity_error_limit", 1, 1000000),
        ("min_free_gib", 1, 100000),
        ("max_delete_files", 1, 1000000),
        ("max_delete_gib", 1, 100000),
        ("transfers", 1, 16),
        ("checkers", 1, 32),
        ("backup_timeout_minutes", 1, 1440),
        ("device_wait_seconds", 5, 300),
    ):
        if type(cfg[key]) is not int or not lo <= cfg[key] <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
    fraction = cfg["max_missing_fraction"]
    if type(fraction) not in (int, float) or not 0 < fraction <= 0.5:
        raise ValueError("Missing-file safety limit must be between 0 and 0.5")
    for key in ("source_mount", "destination_mount"):
        media_path(cfg[key])
    if cfg["source_mount"] == cfg["destination_mount"]:
        raise ValueError("Source and destination must be distinct")
    for key in ("source_uuid", "destination_uuid"):
        if cfg[key]:
            if str(uuid.UUID(cfg[key])) != cfg[key].lower():
                raise ValueError("Invalid filesystem UUID")
    if cfg["source_uuid"] and cfg["source_uuid"] == cfg["destination_uuid"]:
        raise ValueError("Source and destination UUID must differ")
    sources = cfg["additional_sources"]
    if not isinstance(sources, list) or len(sources) > 20:
        raise ValueError("additional_sources must be a list of up to 20 paths")
    for value in sources:
        media_path(value)
    if len(set(sources + [cfg["source_mount"], cfg["destination_mount"]])) != len(sources) + 2:
        raise ValueError("Duplicate or overlapping sources")
    relative_path(cfg["l2_directory"])
    relative_path(cfg["test_directory"])
    if cfg["power_entity"] and not re.fullmatch(r"switch\.[a-z0-9_]+", cfg["power_entity"]):
        raise ValueError("Power entity must be a switch")
    if not isinstance(cfg["legacy_scripts"], list) or not all(
        re.fullmatch(r"script\.[a-z0-9_]+", v) for v in cfg["legacy_scripts"]
    ):
        raise ValueError("Invalid legacy script entities")
    if cfg["execution_mode"] != "observe":
        if not cfg["source_uuid"] or not cfg["destination_uuid"] or not cfg["power_entity"]:
            raise ValueError("Execution requires both disk UUIDs and a power switch")
        if not cfg.get("encryption_password"):
            raise ValueError("An encryption secret is required; unencrypted L2 is not supported")
        if not cfg["legacy_scheduler_disabled"]:
            raise ValueError("Disable the legacy scheduler before enabling execution")
    if cfg["schedule_enabled"] and cfg["execution_mode"] == "observe":
        raise ValueError("Observation mode cannot run a schedule")
    return cfg


def public_config(cfg):
    return {k: copy.deepcopy(v) for k, v in cfg.items() if k not in SECRET_KEYS}


def verification_source(cfg, mode, source):
    """Manual scopes must name an exact configured root; never an arbitrary path."""
    if source is None:
        return None
    if mode != "checksum":
        raise ValueError("Selecting a source is only supported for checksum verification")
    allowed = [cfg["source_mount"]]
    if cfg["execution_mode"] != "test":
        allowed += cfg["additional_sources"]
    if not isinstance(source, str) or source not in allowed:
        raise ValueError("Choose one configured source available in the current mode")
    return source
