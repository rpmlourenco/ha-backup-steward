"""Confined files, verified archive retention and local rclone operations."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile

from . import telemetry
from .integrity import IntegrityFailure, IntegrityRun
from .config import verification_source


ARCHIVE = re.compile(r"steward_l2_\d{8}T\d{6}Z_[a-f0-9]{8}\.tar")
LEGACY = re.compile(r"L2_NASRUI_apps_(?:(?:semanal|manual)_)?\d{4}_\d{2}_\d{2}(?:_\d{2}_\d{2}_\d{2})?\.tar")


def confined(path, root):
    path, root = Path(path), Path(root)
    if root.is_symlink() or root.resolve(strict=True) != root.absolute():
        raise ValueError("Root traverses a symbolic link")
    path.relative_to(root)
    current = root
    for part in path.relative_to(root).parts:
        if part in (".", ".."):
            raise ValueError("Invalid path component")
        current /= part
        if current.is_symlink():
            raise ValueError("Symbolic link in managed path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escaped its managed root")
    return path


def inspect_archive(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Archive is not an independent regular file")
    with tarfile.open(path, "r:") as archive:
        members = archive.getmembers()
        manifests = [m for m in members if m.name in ("backup.json", "./backup.json")]
        if len(manifests) != 1 or not manifests[0].isfile() or manifests[0].size > 1024 * 1024:
            raise ValueError("Invalid archive manifest")
        if any(m.offset_data + m.size > info.st_size for m in members):
            raise ValueError("Truncated archive")
        data = json.load(archive.extractfile(manifests[0]))
    if not re.fullmatch(r"[a-f0-9]{8}", str(data.get("slug", ""))):
        raise ValueError("Invalid archive slug")
    return data


def fingerprint(path):
    s = path.lstat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns)


def prune_l2(directory, keep, successful_slug):
    directory = confined(Path(directory), Path(directory))
    archives = []
    for path in directory.iterdir():
        if not ARCHIVE.fullmatch(path.name) and not LEGACY.fullmatch(path.name):
            continue
        before = fingerprint(path)
        data = inspect_archive(path)
        if not str(data.get("name", "")).startswith(("HA Backup Steward L2 ", "L2 NASRUI apps ")):
            raise ValueError("Archive is not a managed L2")
        date = datetime.fromisoformat(data["date"])
        if date.tzinfo is None:
            raise ValueError("Backup date must have a timezone")
        archives.append((date, path.name, path, before, data["slug"]))
    archives.sort(key=lambda item: (item[0], item[1]), reverse=True)
    matches = [item for item in archives if item[4] == successful_slug]
    if len(matches) != 1 or matches[0] != archives[0]:
        raise ValueError("Successful L2 must be the newest unique archive")
    if type(keep) is not int or keep < 1:
        raise ValueError("Retention must preserve at least one archive")
    for _, _, path, before, _ in archives:
        if fingerprint(path) != before:
            raise ValueError("Archive changed during retention planning")
    removed = []
    for _, _, path, before, _ in archives[keep:]:
        if fingerprint(path) != before:
            raise ValueError("Archive changed before removal")
        path.unlink()
        removed.append(path.name)
    return removed


def count_files(root):
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("Symbolic links are not allowed in backup trees")
            if name in files and not path.is_file():
                raise ValueError("Special files are not supported")
        total += len(files)
    return total


async def filesystem_call(function, *args):
    """Keep slow scans off the API loop; drain the worker before safe removal."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        finally:
            raise


async def command(*args, timeout=120, integrity=None):
    if Path(args[0]).name == "rclone":
        return await rclone_command(args, timeout, integrity)
    process = await asyncio.create_subprocess_exec(
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate()
    except BaseException:
        await stop_process(process)
        raise
    if process.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed with exit code {process.returncode}")
    return output.decode("utf-8", errors="replace").strip()


async def stop_process(process):
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()


async def rclone_command(args, timeout, integrity=None):
    args = list(map(str, args))
    # Parse structured errors as they arrive. INFO permits periodic statistics;
    # successful per-file messages and arbitrary JSON fields are never relayed.
    for flag, value in (("--log-level", "INFO"), ("--stats", "60s"), ("--stats-log-level", "NOTICE")):
        if flag in args:
            args[args.index(flag) + 1] = value
        else:
            args.extend((flag, value))
    if "--use-json-log" not in args:
        args.append("--use-json-log")
    if integrity is not None:
        args.extend(("--combined", "-", "--retries", "1"))
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=256 * 1024,
    )
    details = []
    error_count = 0

    async def read_diagnostics():
        nonlocal error_count
        while line := await process.stderr.readline():
            try:
                record = json.loads(line)
            except ValueError, UnicodeDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            stats = record.get("stats")
            if isinstance(stats, dict):
                numbers = {
                    key: stats.get(key, 0) if type(stats.get(key, 0)) in (int, float) else 0
                    for key in ("checks", "bytes", "elapsedTime")
                }
                telemetry.event(
                    f"rclone: verificados={numbers['checks']}; transferidos={numbers['bytes']} bytes; tempo={round(numbers['elapsedTime'], 1)} s"
                )
            if record.get("level") not in ("error", "fatal"):
                continue
            error_count += 1
            if len(details) >= 8:
                continue
            message = telemetry.process_text(record.get("msg", "rclone error"))
            object_name = telemetry.process_text(record.get("object", "")) if record.get("object") else ""
            detail = f"{message} [{object_name}]" if object_name else message
            details.append(detail)
            telemetry.event(f"rclone: {detail}", level=40)

    async def read_report():
        while line := await process.stdout.readline():
            integrity(line.decode("utf-8", errors="replace"))
        return b""

    readers = [
        asyncio.create_task(read_report() if integrity is not None else process.stdout.read()),
        asyncio.create_task(read_diagnostics()),
    ]
    try:
        async with asyncio.timeout(timeout):
            output, _ = await asyncio.gather(*readers)
            await process.wait()
    except BaseException:
        await stop_process(process)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    if error_count > len(details):
        telemetry.event(
            f"rclone: mais {error_count - len(details)} mensagens de erro; limite de detalhes atingido",
            level=40,
        )
    if process.returncode:
        summary = f"rclone failed with exit code {process.returncode}"
        if details:
            summary += ": " + details[0]
        raise RuntimeError(summary)
    return output.decode("utf-8", errors="replace").strip()


class Copier:
    def __init__(self, cfg, disks, on_event=None, on_report=None):
        self.cfg, self.disks = cfg, disks
        self.on_event = on_event or (lambda stage, message: telemetry.event(f"{stage}: {message}"))
        self.integrity = IntegrityRun(cfg["integrity_error_limit"], on_report)

    def plan_checks(self, include_copy=True, include_full=True, test=False, source=None):
        if include_copy:
            for origin, target in self.pairs(test):
                self.integrity.plan(origin, target, "size")
            for origin, target, kind in self.archive_pairs(test):
                self.integrity.plan(origin, target, kind)
        if include_full:
            for origin, target in self.verification_pairs(test, source):
                self.integrity.plan(origin, target, "sha256")
        self.integrity.publish()

    def archive_pair(self, test):
        c = self.cfg
        source = Path(c["source_mount"]) / (c["test_directory"] + "/level-2" if test else c["l2_directory"])
        target = self.pairs(test)[0][1] if test else self.pairs()[0][1] / c["l2_directory"]
        return source, target

    def archive_pairs(self, test):
        source, target = self.archive_pair(test)
        yield source, target, "l2_sha256"
        if self.cfg["l1_enabled"]:
            source = Path(self.cfg["source_mount"]) / (
                self.cfg["test_directory"] + "/level-1" if test else self.cfg["l1_directory"]
            )
            target = self.pairs(test)[1][1] if test else self.pairs()[0][1] / self.cfg["l1_directory"]
            yield source, target, "l1_sha256"

    def abort_checks(self):
        self.integrity.abort()

    def finish_checks(self):
        report = self.integrity.report()
        self.on_event(
            "integrity_summary",
            f"Resumo: {report['error_count']} erro(s) globais em {len(report['areas'])} verificações",
        )
        return self.integrity.finish()

    def check_paths(self, source, target):
        confined(source, source)
        confined(target, Path(self.cfg["destination_mount"]))

    async def verify(self, source, target, kind, test=False):
        area = self.integrity.plan(source, target, kind)
        self.integrity.begin(area)
        await self.disks.validate_source()
        await self.disks.validate_destination()
        if not source.is_dir() or not target.is_dir():
            self.integrity.problem(area, None, "unavailable", "Origem ou destino ausente")
            self.integrity.end(area, failed=True)
            self.on_event(
                "integrity_area_failed", f"{kind}: {source}; origem ou destino ausente; a continuar"
            )
            return
        self.check_paths(source, target)
        try:
            if not await filesystem_call(count_files, source):
                self.integrity.problem(area, None, "unavailable", "Origem vazia; verificação recusada")
                self.integrity.end(area, failed=True)
                self.on_event("integrity_area_failed", f"{kind}: {source}; origem vazia; a continuar")
                return
            await filesystem_call(count_files, target)
        except OSError as error:
            self.integrity.problem(area, None, "read_error", type(error).__name__)
            self.integrity.end(area, failed=True)
            self.on_event("integrity_area_failed", f"{kind}: {source}; falha de leitura; a continuar")
            return
        args = ["rclone", "--config", "/dev/null", "check", source, target, "--checkers", "2"]
        args += ["--size-only"] if kind == "size" else ["--local-hashes", "sha256", "--checksum"]
        if test and kind == "size":
            args.append("--one-way")
        try:
            await command(*args, timeout=7 * 86400, integrity=lambda line: self.integrity.line(area, line))
        except IntegrityFailure:
            self.integrity.abort()
            self.on_event(
                "integrity_limit",
                f"Limite global de {self.integrity.limit} erros atingido; a parar a verificação",
            )
            raise
        except (RuntimeError, TimeoutError) as error:
            if not area["errors"]:
                self.integrity.problem(area, None, "operation_error", str(error) or type(error).__name__)
            self.integrity.end(area, failed=True)
            self.on_event(
                "integrity_area_failed",
                f"{kind}: {source}; {area['errors']} erro(s); a continuar nas restantes origens",
            )
        else:
            self.integrity.end(area)
            if area["errors"]:
                self.on_event(
                    "integrity_area_failed", f"{kind}: {source}; {area['errors']} erro(s); a continuar"
                )
            else:
                self.on_event(
                    "integrity_area_ok", f"{kind}: {source}; {area['checked']} ficheiros verificados"
                )

    def pairs(self, test=False):
        c = self.cfg
        destination = Path(c["destination_mount"])
        if test:
            pairs = [
                (
                    Path(c["source_mount"]) / c["test_directory"] / "level-2",
                    destination / c["test_directory"] / "mirror" / Path(c["source_mount"]).name,
                )
            ]
            if c["l1_enabled"]:
                pairs.append(
                    (
                        Path(c["source_mount"]) / c["test_directory"] / "level-1",
                        destination / c["test_directory"] / "mirror" / "L1",
                    )
                )
            return pairs
        return [
            (Path(s), destination / "mirror" / Path(s).name)
            for s in [c["source_mount"], *c["additional_sources"]]
        ]

    async def copy(self, run_id, test=False):
        c = self.cfg
        for source, target in self.pairs(test):
            self.on_event("l3_source", f"A copiar e verificar tamanhos: {source} → {target}")
            await self.disks.validate_source()
            await self.disks.validate_destination()
            confined(source, Path(c["source_mount"]) if test else source)
            confined(target, Path(c["destination_mount"]))
            current = await filesystem_call(count_files, source) if source.is_dir() else 0
            if not current:
                raise ValueError("Missing or empty source; refusing to copy")
            existing = await filesystem_call(count_files, target) if target.is_dir() else 0
            if existing >= 20 and current < existing * (1 - c["max_missing_fraction"]):
                raise ValueError("Too many source files disappeared; refusing sync")
            target.mkdir(parents=True, exist_ok=True)
            base = [
                "rclone",
                "--config",
                "/dev/null",
                "--log-level",
                "ERROR",
                "--transfers",
                str(c["transfers"]),
                "--checkers",
                str(c["checkers"]),
            ]
            args = base + ["copy" if test else "sync", str(source), str(target)]
            if not test:
                history = Path(c["destination_mount"]) / "history" / run_id / source.name
                confined(history, Path(c["destination_mount"]))
                args += [
                    "--delete-after",
                    "--max-delete",
                    str(c["max_delete_files"]),
                    "--max-delete-size",
                    f"{c['max_delete_gib']}G",
                    "--backup-dir",
                    str(history),
                    "--create-empty-src-dirs",
                ]
            await command(*args, timeout=7 * 86400)
            await self.disks.validate_destination()
            await self.verify(source, target, "size", test)
            if not self.integrity.plan(source, target, "size")["errors"]:
                self.on_event("l3_verified", f"Cópia e tamanhos confirmados: {source}")
        for source, destination, kind in self.archive_pairs(test):
            self.on_event("archive_checksum", "A confirmar SHA-256 dos arquivos L1/L2 entregues ao espelho")
            await self.verify(source, destination, kind, test)

    def verification_pairs(self, test=False, source=None):
        verification_source(self.cfg, "checksum", source)
        pairs = self.pairs(test)
        if source is None:
            return pairs
        if test:
            return pairs[:1]
        return [(origin, target) for origin, target in pairs if str(origin) == source]

    async def checksum(self, test=False, source=None):
        self.plan_checks(include_copy=False, include_full=True, test=test, source=source)
        for source, destination in self.verification_pairs(test, source):
            self.on_event("checksum_source", f"A verificar SHA-256: {source} → {destination}")
            await self.verify(source, destination, "sha256", test)
            if not self.integrity.plan(source, destination, "sha256")["errors"]:
                self.on_event("checksum_verified", f"SHA-256 confirmado: {source}")

    def prune_history(self):
        root = confined(Path(self.cfg["destination_mount"]) / "history", Path(self.cfg["destination_mount"]))
        if not root.is_dir():
            return
        now = datetime.now(timezone.utc)
        for path in root.iterdir():
            confined(path, root)
            if path.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z", path.name):
                date = datetime.strptime(path.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                if (now - date).days >= self.cfg["history_days"]:
                    shutil.rmtree(path)
