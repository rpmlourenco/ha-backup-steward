"""Strict, upgrade-safe policy for a direct remote mirror."""

import copy
import re
import uuid


DEFAULTS = {
    "enabled": False,
    "schedule_enabled": False,
    "schedule_time": "06:00",
    "label": "Lisboa",
    "host": "",
    "port": 2222,
    "host_key": "",
    "destination_uuid": "",
    "sources": [],
    "max_delete_files": 1000,
    "min_free_gib": 20,
    "timeout_minutes": 720,
}


def public_key(value):
    import base64
    import struct

    parts = value.strip().split()
    if len(parts) != 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Cola uma chave pública Ed25519, sem opções ou comentários.")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        if (
            len(blob) != 51
            or blob[:4] != struct.pack(">I", 11)
            or blob[4:15] != b"ssh-ed25519"
            or blob[15:19] != struct.pack(">I", 32)
        ):
            raise ValueError
    except Exception:
        raise ValueError("A chave pública Ed25519 não é válida.") from None
    return " ".join(parts)


def validate(raw, allowed):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError("Configuração L4 inválida.")
    cfg = dict(copy.deepcopy(DEFAULTS), **copy.deepcopy(raw))
    for key in ("enabled", "schedule_enabled"):
        if type(cfg[key]) is not bool:
            raise ValueError("Os interruptores L4 têm de ser booleanos.")
    for key, lo, hi in (
        ("port", 1, 65535),
        ("max_delete_files", 1, 100000),
        ("min_free_gib", 1, 100000),
        ("timeout_minutes", 1, 1440),
    ):
        if type(cfg[key]) is not int or not lo <= cfg[key] <= hi:
            raise ValueError(f"L4: {key} deve estar entre {lo} e {hi}.")
    if not isinstance(cfg["label"], str) or not 1 <= len(cfg["label"]) <= 60:
        raise ValueError("Dá um nome ao destino L4 (até 60 caracteres).")
    if not isinstance(cfg["host"], str) or (
        cfg["host"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", cfg["host"])
    ):
        raise ValueError("Indica apenas o nome DNS ou IP, sem https:// nem caminho.")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", cfg["schedule_time"]):
        raise ValueError("A hora L4 deve usar HH:MM.")
    if cfg["destination_uuid"]:
        try:
            uuid.UUID(cfg["destination_uuid"])
        except ValueError, TypeError, AttributeError:
            raise ValueError("Confirma o UUID do filesystem do recetor.") from None
    if cfg["host_key"]:
        cfg["host_key"] = public_key(cfg["host_key"])
    if not isinstance(cfg["sources"], list) or len(cfg["sources"]) > 20:
        raise ValueError("Seleciona até 20 origens L4.")
    names, paths = set(), set()
    for item in cfg["sources"]:
        if not isinstance(item, dict) or set(item) != {"source", "name", "uuid"}:
            raise ValueError("Cada origem L4 requer caminho, nome remoto e UUID.")
        if item["source"] not in allowed or not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", item["name"]):
            raise ValueError("Escolhe uma origem configurada e um nome remoto simples em minúsculas.")
        if item["name"] in names or item["source"] in paths:
            raise ValueError("As origens e nomes L4 não podem repetir-se.")
        names.add(item["name"])
        paths.add(item["source"])
        try:
            uuid.UUID(item["uuid"])
        except ValueError, TypeError, AttributeError:
            raise ValueError("Confirma o UUID do filesystem de cada origem L4.") from None
    if cfg["enabled"] and (
        not cfg["host"] or not cfg["host_key"] or not cfg["sources"] or not cfg["destination_uuid"]
    ):
        raise ValueError(
            "Para ativar L4, configura endereço, identidade SSH, UUID do destino e pelo menos uma origem."
        )
    if cfg["schedule_enabled"] and not cfg["enabled"]:
        raise ValueError("Ativa L4 antes de ligar a sua agenda.")
    return cfg
