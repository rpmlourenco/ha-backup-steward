"""Native HA L1 orchestration; retain native settings and deliver a second copy."""

import asyncio
from datetime import datetime
from pathlib import Path
import re

from .storage import confined, filesystem_call, fingerprint, inspect_archive

L1_ARCHIVE = re.compile(r"steward_l1_\d{8}T\d{6}Z_[a-f0-9]{8}\.tar")


def prune(directory, keep, successful_slug):
    directory = confined(Path(directory), Path(directory))
    archives = []
    for path in directory.iterdir():
        if not L1_ARCHIVE.fullmatch(path.name):
            continue
        before = fingerprint(path)
        data = inspect_archive(path)
        if data.get("protected") is not True:
            raise ValueError("Managed L1 archive is not encrypted")
        date = datetime.fromisoformat(data["date"])
        if date.tzinfo is None:
            raise ValueError("L1 archive date must include a timezone")
        archives.append((date, path.name, path, before, data["slug"]))
    archives.sort(reverse=True)
    if type(keep) is not int or keep < 1:
        raise ValueError("L1 retention must preserve at least one archive")
    if (
        len([a for a in archives if a[4] == successful_slug]) != 1
        or not archives
        or archives[0][4] != successful_slug
    ):
        raise ValueError("Delivered L1 must be the newest unique archive")
    for _, _, path, before, _ in archives:
        if fingerprint(path) != before:
            raise ValueError("L1 archive changed during retention planning")
    removed = []
    for _, _, path, before, _ in archives[keep:]:
        if fingerprint(path) != before:
            raise ValueError("L1 archive changed before removal")
        path.unlink()
        removed.append(path.name)
    return removed


async def run(cfg, state, api, disks, control, run_id, test):
    state.event("l1_policy", "A validar a configuração nativa do L1 e a agenda única")
    await api.l1_policy()
    control.checkpoint()
    state.event("l1_create", "A criar o L1 com a seleção, chave e destinos nativos do HA")
    # Persist uncertainty before sending. A lost response must never cause a second L1.
    state.update(backup_kind="l1", backup_job=None, backup_pending=True, needs_cleanup=True)
    job = await api.create_l1()
    state.update(backup_job=job)
    state.event("l1_wait", "A aguardar a criação L1 e a entrega nos destinos nativos")
    slug, info = await api.finish_l1(job, cfg["backup_timeout_minutes"])
    state.update(backup_pending=False, needs_cleanup=False, l1="created", l1_slug=slug)
    control.checkpoint()
    directory = Path(cfg["source_mount"]) / (
        cfg["test_directory"] + "/level-1" if test else cfg["l1_directory"]
    )
    await disks.validate_source()
    confined(directory, Path(cfg["source_mount"]))
    directory.mkdir(parents=True, exist_ok=True)
    state.event("l1_download", "A copiar o L1 encriptado para NASRUI; a cópia nativa é preservada")
    async with asyncio.timeout(cfg["backup_timeout_minutes"] * 60):
        path, digest = await control.interruptible(
            api.download(slug, directory, f"steward_l1_{run_id}_{slug}.tar", info.get("size_bytes"))
        )
    state.update(l1="delivered", l1_file=path.name, l1_sha256=digest)
    control.checkpoint()
    if not test:
        state.event("l1_retention", "A aplicar retenção apenas às cópias L1 entregues no NASRUI")
        await disks.validate_source()
        removed = await filesystem_call(prune, directory, cfg["l1_keep"], slug)
        state.update(l1_retention="success", l1_removed=len(removed))
    else:
        state.update(l1_retention="disabled_in_test")
    state.event("l1_delivered", "L1 entregue no NASRUI; seguirá também para o espelho L3")
