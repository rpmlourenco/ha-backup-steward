"""Explicit, confined L3 restores. The mirror is read only; live areas are written in place."""

import asyncio
import copy
import os
from pathlib import Path
import secrets
import shutil
import stat
import time

from .cancellation import RunControl
from .config import public_config
from .storage import Copier, command, confined


def selected_pairs(cfg, sources):
    allowed = [cfg["source_mount"]]
    if cfg["execution_mode"] != "test":
        allowed += cfg["additional_sources"]
    if sources is not None and (
        not isinstance(sources, list)
        or not sources
        or any(not isinstance(s, str) or s not in allowed for s in sources)
        or len(sources) != len(set(sources))
    ):
        raise ValueError("Escolhe todas as áreas ou áreas configuradas válidas")
    chosen = allowed if sources is None else sources
    pairs = Copier(cfg, None).pairs(cfg["execution_mode"] == "test")
    return [
        (mirror, live)
        for live, mirror in pairs
        if (cfg["source_mount"] if cfg["execution_mode"] == "test" else str(live)) in chosen
    ]


def root_identity(path):
    confined(path, path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("A área de restauro tem de ser uma pasta existente")
    return [info.st_dev, info.st_ino]


def describe_area(live, cfg):
    """Missing ordinary areas are planned without creating directories during preview."""
    try:
        live.lstat()
    except FileNotFoundError:
        if live == Path(cfg["source_mount"]):
            raise ValueError(
                "A montagem de origem principal está ausente; não será criada uma pasta no seu lugar"
            ) from None
        parent = live.parent
        while not parent.exists():
            if parent.is_symlink():
                raise ValueError("Link simbólico no caminho da área")
            parent = parent.parent
        confined(live, parent)
        if cfg["execution_mode"] == "test" and not parent.is_relative_to(Path(cfg["source_mount"])):
            raise ValueError("A área de teste tem de permanecer no disco de origem")
        return dict(
            identity=None,
            create=True,
            nonempty=False,
            parent=str(parent),
            parent_identity=root_identity(parent),
        )
    return dict(identity=root_identity(live), create=False, nonempty=any(live.iterdir()))


def validate_area(area):
    """Pin existing roots, or the nearest existing parent of an absent root."""
    live = Path(area["source"])
    try:
        if area.get("create"):
            parent = Path(area["parent"])
            if root_identity(parent) != area["parent_identity"]:
                raise ValueError("A pasta de destino mudou; prepara novamente o restauro")
            confined(live, parent)
            try:
                live.lstat()
            except FileNotFoundError:
                return
            raise ValueError("A área passou a existir; prepara novamente o restauro")
        if root_identity(live) != area["identity"]:
            raise ValueError("A identidade de uma área mudou desde a confirmação")
    except FileNotFoundError:
        raise ValueError("Uma pasta mudou ou desapareceu; prepara novamente o restauro") from None


def create_area(area):
    """Create only the confirmed path, using a pinned parent FD and no symlink traversal."""
    validate_area(area)
    live, parent = Path(area["source"]), Path(area["parent"])
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(parent, flags)
    try:
        opened = os.fstat(fd)
        if [opened.st_dev, opened.st_ino] != area["parent_identity"]:
            raise ValueError("A pasta de destino mudou antes da criação")
        device = opened.st_dev
        for component in live.relative_to(parent).parts:
            # Existing intermediate directories must also be re-confirmed, never adopted.
            try:
                os.mkdir(component, mode=0o755, dir_fd=fd)
            except FileExistsError:
                raise ValueError("O caminho passou a existir; prepara novamente o restauro") from None
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            opened = os.fstat(fd)
            if opened.st_dev != device:
                raise ValueError("A área criada atravessa uma montagem inesperada")
        area.update(identity=[opened.st_dev, opened.st_ino], create=False)
    finally:
        os.close(fd)


def nested_mounts(root):
    # Bind mounts can share st_dev, so device checks alone are insufficient.
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        value = line.split()[4]
        for escaped, plain in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            value = value.replace(escaped, plain)
        mount = Path(value)
        if mount != root and mount.is_relative_to(root):
            raise ValueError("Uma área contém outra montagem; restauro recusado")


async def walk_tree(root, *, erase=False):
    """Never follow symlinks or cross mounts; unlink relative to pinned directory FDs."""
    identity = root_identity(root)
    nested_mounts(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    opened = os.fstat(fd)
    if [opened.st_dev, opened.st_ino] != identity:
        os.close(fd)
        raise ValueError("A área mudou durante a abertura")
    device = opened.st_dev
    total = [0, 0, 0]

    async def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                if info.st_dev != device or stat.S_ISLNK(info.st_mode):
                    raise ValueError("Montagem ou link simbólico encontrado na área")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, flags, dir_fd=directory)
                    try:
                        opened_child = os.fstat(child)
                        if (opened_child.st_dev, opened_child.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError("A árvore mudou durante o restauro")
                        await visit(child)
                    finally:
                        os.close(child)
                    if erase:
                        os.rmdir(entry.name, dir_fd=directory)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total[0] += 1
                    total[1] += info.st_size
                    total[2] += info.st_blocks * 512
                    if erase:
                        os.unlink(entry.name, dir_fd=directory)
                else:
                    raise ValueError("Links físicos ou ficheiros especiais não são suportados no restauro")
                await asyncio.sleep(0)

    try:
        await visit(fd)
    finally:
        os.close(fd)
    return dict(files=total[0], size=total[1], allocated=total[2])


async def preview(engine, sources):
    if engine.busy or engine.cfg["execution_mode"] == "observe" or engine.state.data.get("needs_cleanup"):
        raise ValueError("O restauro requer a app disponível e o destino fechado")
    async with engine.lock:
        cfg = copy.deepcopy(engine.cfg)
        pairs = selected_pairs(cfg, sources)
        host = engine.host_factory(engine.state.identity["instance_id"])
        disks = engine.disks_factory(cfg, host)
        try:
            await disks.validate_source()
            areas = []
            for mirror, live in pairs:
                details = describe_area(live, cfg)
                nested_mounts(live)
                areas.append(dict(source=str(live), mirror=str(mirror), **details))
        finally:
            await host.close()
        token = secrets.token_urlsafe(32)
        engine.restore_plan = dict(
            token=token, expires=time.monotonic() + 600, cfg=public_config(cfg), areas=areas, sources=sources
        )
        return dict(
            token=token,
            areas=[{k: area[k] for k in ("source", "mirror", "nonempty", "create")} for area in areas],
        )


def start(engine, token, erase_sources, checksum):
    if engine.busy or engine.cfg["execution_mode"] == "observe" or engine.state.data.get("needs_cleanup"):
        raise ValueError("O restauro requer a app disponível e o destino fechado")
    plan = getattr(engine, "restore_plan", None)
    if not isinstance(token, str) or not plan or not secrets.compare_digest(token, plan["token"]):
        raise ValueError("Volta a preparar o restauro antes de confirmar")
    if time.monotonic() > plan["expires"] or public_config(engine.cfg) != plan["cfg"]:
        raise ValueError("A preparação expirou ou a configuração mudou; volta a preparar")
    if (
        type(checksum) is not bool
        or not isinstance(erase_sources, list)
        or any(not isinstance(s, str) for s in erase_sources)
    ):
        raise ValueError("Indica as áreas a apagar e a opção de checksum")
    expected = [a["source"] for a in plan["areas"] if a["nonempty"]]
    if sorted(erase_sources) != sorted(expected):
        raise ValueError("É necessária confirmação explícita para apagar as áreas com conteúdo")
    for area in plan["areas"]:
        path = Path(area["source"])
        validate_area(area)
        if not area.get("create") and any(path.iterdir()) and str(path) not in erase_sources:
            raise ValueError("Uma área mudou; volta a preparar e confirmar o restauro")
    engine.restore_plan = None
    engine.control = RunControl()
    options = dict(areas=copy.deepcopy(plan["areas"]), erase_sources=erase_sources[:], checksum=checksum)
    engine.task = asyncio.create_task(engine.run("restore", control=engine.control, restore_options=options))
    return engine.task


class Restorer(Copier):
    def __init__(self, cfg, disks, on_event, on_report, options):
        super().__init__(cfg, disks, on_event, on_report)
        self.options = options
        self.prepared = False
        self.mirror_identities = {}
        allowed = selected_pairs(cfg, [cfg["source_mount"]] if cfg["execution_mode"] == "test" else None)
        self.restore_pairs = []
        for area in options["areas"]:
            pair = (Path(area["mirror"]), Path(area["source"]))
            if pair not in allowed or pair in self.restore_pairs:
                raise ValueError("Área de restauro inválida")
            self.restore_pairs.append(pair)
        if not self.restore_pairs:
            raise ValueError("Seleciona pelo menos uma área")

    def check_paths(self, source, target):
        if (source, target) not in self.restore_pairs:
            raise ValueError("Par de restauro inválido")
        confined(source, Path(self.cfg["destination_mount"]))
        area = next(a for a in self.options["areas"] if a["source"] == str(target))
        validate_area(area)

    async def prepare(self):
        # Validate every selected mirror/area and total space before the first deletion.
        self.prepared = False
        budgets = {}
        for area, (mirror, live) in zip(self.options["areas"], self.restore_pairs, strict=True):
            await self.disks.validate_source()
            await self.disks.validate_destination()
            self.check_paths(mirror, live)
            validate_area(area)
            if not mirror.is_dir():
                raise ValueError(f"Espelho L3 indisponível: {mirror}")
            missing = area.get("create", False)
            old = dict(files=0, size=0, allocated=0) if missing else await walk_tree(live)
            incoming = await walk_tree(mirror)
            self.mirror_identities[str(mirror)] = root_identity(mirror)
            if not incoming["files"]:
                raise ValueError("Espelho L3 vazio; não serão apagados dados")
            if not missing and any(live.iterdir()) and str(live) not in self.options["erase_sources"]:
                raise ValueError("A área deixou de estar vazia; confirma novamente antes de apagar")
            storage_root = Path(area["parent"]) if missing else live
            device = storage_root.stat().st_dev
            budget = budgets.setdefault(
                device, dict(free=shutil.disk_usage(storage_root).free, need=0, reclaim=0)
            )
            budget["need"] += incoming["size"]
            budget["reclaim"] += old["allocated"]
            self.integrity.plan(mirror, live, "size")
            if self.options["checksum"]:
                self.integrity.plan(mirror, live, "sha256")
        if any(b["need"] > b["free"] + b["reclaim"] for b in budgets.values()):
            raise ValueError("Espaço insuficiente nas áreas originais, mesmo após a limpeza confirmada")
        # All selected mirrors and space estimates passed; only now create confirmed missing areas.
        for area in self.options["areas"]:
            if area.get("create"):
                await self.disks.validate_source()
                await self.disks.validate_destination()
                create_area(area)
                self.on_event("restore_create", f"Pasta criada para o restauro: {area['source']}")
        self.integrity.publish()
        self.prepared = True

    async def restore(self):
        if not self.prepared:
            raise ValueError("Todas as áreas têm de ser validadas antes de restaurar")
        for area, (mirror, live) in zip(self.options["areas"], self.restore_pairs, strict=True):
            await self.disks.validate_source()
            await self.disks.validate_destination()
            self.check_paths(mirror, live)
            if root_identity(live) != area["identity"]:
                raise ValueError("A identidade da área mudou; restauro interrompido")
            if root_identity(mirror) != self.mirror_identities[str(mirror)]:
                raise ValueError("O espelho L3 mudou desde a preparação")
            if str(live) in self.options["erase_sources"]:
                self.on_event("restore_clear", f"A apagar conteúdo confirmado: {live}")
                await walk_tree(live, erase=True)
            if any(live.iterdir()):
                raise ValueError("A área não está vazia; interrompe as aplicações que a utilizam")
            # No sync/delete/backup-dir and no .partial/tmp copy. The mirror is never modified.
            self.on_event("restore_copy", f"A restaurar diretamente: {mirror} → {live}")
            await command(
                "rclone",
                "--config",
                "/dev/null",
                "copy",
                mirror,
                live,
                "--inplace",
                "--ignore-times",
                "--create-empty-src-dirs",
                "--transfers",
                str(self.cfg["transfers"]),
                "--checkers",
                str(self.cfg["checkers"]),
                timeout=7 * 86400,
            )
            await command("sync", "-f", live)
            await self.verify(mirror, live, "size")

    async def checksum(self, test=False, source=None):
        for mirror, live in self.restore_pairs:
            self.on_event("restore_checksum", f"A verificar SHA-256 após o restauro: {live}")
            await self.verify(mirror, live, "sha256")
