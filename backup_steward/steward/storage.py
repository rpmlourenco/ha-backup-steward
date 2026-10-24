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


async def command(*args, timeout=120):
    process = await asyncio.create_subprocess_exec(
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            output, error = await process.communicate()
    except BaseException:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        # Local process errors can include authenticated URLs; do not expose raw stderr.
        raise RuntimeError(f"{Path(args[0]).name} failed with exit code {process.returncode}")
    return output.decode("utf-8", errors="replace").strip()


class Copier:
    def __init__(self, cfg, disks):
        self.cfg, self.disks = cfg, disks

    def pairs(self, test=False):
        c = self.cfg
        destination = Path(c["destination_mount"])
        if test:
            return [
                (
                    Path(c["source_mount"]) / c["test_directory"] / "level-2",
                    destination / c["test_directory"] / "mirror" / Path(c["source_mount"]).name,
                )
            ]
        return [
            (Path(s), destination / "mirror" / Path(s).name)
            for s in [c["source_mount"], *c["additional_sources"]]
        ]

    async def copy(self, run_id, test=False):
        c = self.cfg
        for source, target in self.pairs(test):
            await self.disks.validate_source()
            await self.disks.validate_destination()
            confined(source, Path(c["source_mount"]) if test else source)
            confined(target, Path(c["destination_mount"]))
            if not source.is_dir() or count_files(source) == 0:
                raise ValueError("Missing or empty source; refusing to copy")
            current = count_files(source)
            existing = count_files(target) if target.is_dir() else 0
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
            check = base + ["check", str(source), str(target), "--size-only"]
            if test:
                check.append("--one-way")
            await command(*check, timeout=7 * 86400)
        source = Path(c["source_mount"]) / (c["test_directory"] + "/level-2" if test else c["l2_directory"])
        destination = self.pairs(test)[0][1] if test else self.pairs()[0][1] / c["l2_directory"]
        await command(
            "rclone",
            "--config",
            "/dev/null",
            "check",
            source,
            destination,
            "--local-hashes",
            "sha256",
            "--checksum",
            timeout=86400,
        )

    async def checksum(self, test=False):
        for source, destination in self.pairs(test):
            await self.disks.validate_source()
            await self.disks.validate_destination()
            confined(source, source)
            confined(destination, Path(self.cfg["destination_mount"]))
            if (
                not source.is_dir()
                or not destination.is_dir()
                or not count_files(source)
                or not count_files(destination)
            ):
                raise ValueError("Missing or empty checksum source or destination")
            await command(
                "rclone",
                "--config",
                "/dev/null",
                "check",
                source,
                destination,
                "--local-hashes",
                "sha256",
                "--checksum",
                "--checkers",
                "2",
                timeout=7 * 86400,
            )

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
