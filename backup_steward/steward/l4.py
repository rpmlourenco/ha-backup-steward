"""Direct rsync/SSH mirror. Independent outcomes, shared local execution lock."""

import asyncio
import json
import os
from pathlib import Path
import re
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .state import atomic_json
from .storage import filesystem_call
from .l4_progress import Progress
from . import telemetry


def now():
    return datetime.now(timezone.utc).isoformat()


async def execute(args, timeout=60, progress=None):
    """Never log argv, filenames, stderr or key material; bound captured output."""
    process = await asyncio.create_subprocess_exec(
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=dict(os.environ, LC_ALL="C"),
    )
    output, errors = bytearray(), bytearray()

    async def drain_errors():
        while chunk := await process.stderr.read(4096):
            errors.extend(chunk)
            del errors[:-8192]

    error_reader = asyncio.create_task(drain_errors())
    try:
        async with asyncio.timeout(timeout):
            while chunk := await process.stdout.read(4096):
                output.extend(chunk)
                if len(output) > 65536:
                    del output[:-65536]
                if progress:
                    progress(chunk.decode("utf-8", errors="replace"))
            code = await process.wait()
            await error_reader
        if code:
            detail = bytes(errors).lower()
            for marker, message in (
                (
                    b"host key verification failed",
                    "A identidade SSH do recetor não corresponde. Confirma-a pela app Mirror.",
                ),
                (
                    b"permission denied (publickey)",
                    "A chave de Alicante não está autorizada. Confirma o emparelhamento e reinicia a app Mirror após guardar.",
                ),
                (
                    b"connection refused",
                    "A porta SSH recusou a ligação. Confirma a porta publicada e o encaminhamento TCP.",
                ),
                (
                    b"could not resolve hostname",
                    "O endereço do recetor não resolve em DNS. Confirma o nome ou usa o IP LAN.",
                ),
                (b"no space left", "O disco do recetor ficou sem espaço."),
            ):
                if marker in detail:
                    raise ValueError(message)
            if str(args[0]) == "rsync" and code == 25:
                raise ValueError("O espelho excede o limite de eliminações; sincronização bloqueada.")
            raise ValueError(
                f"A operação remota falhou (código {code}). Confirma rede, chave, permissões e espaço no recetor."
            )
        return output.decode("utf-8", errors="strict").strip()
    finally:
        if process.returncode is None:
            # SSH/rsync can have descendants; cancellation must wait for all of them.
            import signal

            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        await asyncio.gather(error_reader, return_exceptions=True)


async def source_identity(item):
    root = Path(item["source"])
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise ValueError("Uma origem L4 está ausente ou é um link simbólico.")
    mount = json.loads(await execute(["findmnt", "-J", "-T", root, "-o", "UUID,FSTYPE"]))["filesystems"][0]
    if (mount.get("uuid") or "").lower() != item["uuid"].lower() or mount.get("fstype") != "ext4":
        raise ValueError("O filesystem da origem não corresponde ao UUID ext4 configurado.")
    return root


def ssh_args(cfg, directory, name):
    return [
        "ssh",
        "-F",
        "/dev/null",
        "-p",
        str(cfg["port"]),
        "-i",
        str(directory / f"{name}.key"),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "UserKnownHostsFile=" + str(directory / "known_hosts"),
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "Compression=no",
    ]


def known_hosts(cfg):
    host = cfg["host"] if cfg["port"] == 22 else f"[{cfg['host']}]:{cfg['port']}"
    return host + " " + cfg["host_key"] + "\n"


def rsync_args(cfg, directory, item, dry=False):
    import shlex

    args = [
        "rsync",
        "-rt",
        "--links",
        "--safe-links",
        "--omit-dir-times",
        "--partial-dir=.steward-partial",
        "--delete-delay",
        "--max-delete=" + str(cfg["max_delete_files"]),
        "--timeout=120",
        "--no-owner",
        "--no-group",
        "--no-perms",
        "--no-devices",
        "--no-specials",
        "-e",
        shlex.join(ssh_args(cfg, directory, item["name"])),
    ]
    if dry:
        args += ["--dry-run", "--itemize-changes", "--out-format=%i|%l"]
    else:
        args += ["--info=progress2", "--outbuf=L", "--out-format=", "--stats"]
    host = f"[{cfg['host']}]" if ":" in cfg["host"] else cfg["host"]
    return args + [item["source"] + "/", f"mirror_{item['name']}@{host}:./"]


class Mirror:
    def __init__(self, engine):
        self.engine = engine
        self.directory = engine.state.directory / "l4-ssh"
        self.task = None
        self.probing = False
        self.testing = False
        self.progress_at = 0
        self.progress_log_at = 0
        self.parser = Progress()
        saved = engine.state.data.get("l4", {})
        if saved.get("status") == "success" and not saved.get("finished_at"):
            self.update(
                phase="complete",
                finished_at=saved.get("last_success"),
                message="Espelho atualizado.",
            )
        if saved.get("connection") == "testing":
            self.update(
                connection="interrupted",
                connection_message="O teste foi interrompido. Repete para confirmar a ligação.",
            )
        if saved.get("status") in ("running", "testing"):
            self.update(
                status="interrupted",
                phase="interrupted",
                finished_at=now(),
                message="Ligação interrompida; repete para reconciliar o espelho.",
            )

    @property
    def busy(self):
        return bool(self.task and not self.task.done()) or self.probing

    def update(self, **values):
        if not set(values) <= {"connection", "tested_at", "connection_message"}:
            values["updated_at"] = now()
        data = dict(self.engine.state.data.get("l4", {}), **values)
        self.engine.state.update(l4=data)

    def event(self, stage, message, level=20):
        message = telemetry.process_text(message)
        data = self.engine.state.data.get("l4", {})
        events = data.get("events", [])[-39:] + [{"time": now(), "stage": stage, "message": message}]
        self.update(stage=stage, message=message, events=events)
        telemetry.event("L4: " + message, data.get("run_id"), level)

    def status(self):
        return dict(
            self.engine.state.data.get("l4", {}),
            busy=self.busy,
            testing=self.testing,
            enabled=self.engine.cfg["l4"]["enabled"],
        )

    def prepare(self, cfg):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        path = self.directory / "known_hosts"
        path.write_text(known_hosts(cfg), encoding="utf-8")
        os.chmod(path, 0o600)

    async def pairing(self):
        if self.engine.busy or self.busy:
            raise ValueError("Aguarda o fim da execução antes de preparar as chaves.")
        self.probing = True
        try:
            return await self._pairing()
        finally:
            self.probing = False

    async def _pairing(self):
        cfg = self.engine.cfg["l4"]
        if not cfg["sources"]:
            raise ValueError("Guarda primeiro pelo menos uma origem.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        result = []
        for item in cfg["sources"]:
            key = self.directory / (item["name"] + ".key")
            if not key.exists():
                await execute(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", key])
            os.chmod(key, 0o600)
            public = await execute(["ssh-keygen", "-y", "-f", key])
            result.append({"name": item["name"], "public_key": public})
        return {"protocol": "steward-mirror-v1", "sources": result}

    async def probe(self, cfg, item):
        self.prepare(cfg)
        if not cfg["host"] or not cfg["host_key"] or not (self.directory / (item["name"] + ".key")).exists():
            raise ValueError("Completa o emparelhamento e guarda a identidade SSH do recetor.")
        result = await execute(
            ssh_args(cfg, self.directory, item["name"])
            + [f"mirror_{item['name']}@{cfg['host']}", "mirror-probe"]
        )
        try:
            data = json.loads(result)
            if (
                data["protocol"] != "steward-mirror-v1"
                or data["name"] != item["name"]
                or data["uuid"].lower() != cfg["destination_uuid"].lower()
                or not data["uuid"]
            ):
                raise ValueError("A identidade ou UUID do recetor não corresponde ao destino configurado.")
            if type(data["free_bytes"]) is not int or data["free_bytes"] < cfg["min_free_gib"] * 1024**3:
                raise ValueError("O destino não tem o espaço livre mínimo configurado.")
        except KeyError, TypeError, json.JSONDecodeError:
            raise ValueError("O destino não devolveu uma identidade Mirror válida.") from None
        return data

    async def test(self):
        if self.engine.busy or self.busy:
            raise ValueError("Aguarda o fim da execução antes de testar.")
        self.probing = True
        self.testing = True
        self.update(
            connection="testing",
            tested_at=None,
            connection_message="A verificar a ligação SSH, as chaves e o destino. Nenhum ficheiro será enviado.",
        )
        try:
            cfg = self.engine.cfg["l4"]
            if not cfg["sources"]:
                raise ValueError("Seleciona pelo menos uma origem.")
            for item in cfg["sources"]:
                await self.probe(cfg, item)
            self.update(
                connection="success",
                tested_at=now(),
                connection_message="Ligação, chaves e destino confirmados. Nenhum ficheiro foi enviado.",
            )
        except asyncio.CancelledError:
            self.update(
                connection="interrupted",
                connection_message="O teste foi interrompido. Repete para confirmar a ligação.",
            )
            raise
        except Exception as error:
            self.update(
                connection="failed",
                tested_at=now(),
                connection_message=str(error)
                if isinstance(error, ValueError)
                else "Não foi possível validar a ligação. Confirma o endereço, porta e chaves.",
            )
            raise
        finally:
            self.probing = False
            self.testing = False
        return self.status()

    def start(self, scheduled_date=None):
        cfg = self.engine.cfg["l4"]
        if not cfg["enabled"] or self.engine.cfg["execution_mode"] != "production":
            raise ValueError(
                "Ativa L4 em modo de produção para sincronizar. Observação e teste não enviam dados remotos."
            )
        if self.engine.busy or self.busy or self.engine.state.data.get("needs_cleanup"):
            raise ValueError("Conclui a execução ou o fecho seguro local antes de iniciar L4.")
        self.engine.control = None
        self.task = asyncio.create_task(self.run(scheduled_date))
        self.engine.task = self.task

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self, scheduled_date=None):
        async with self.engine.lock:
            cfg = self.engine.cfg["l4"]
            results = []
            previous = self.engine.state.data.get("l4", {}).get("counts", {})
            self.update(
                status="running",
                run_id="l4-" + secrets.token_hex(4),
                started_at=now(),
                finished_at=None,
                events=[],
                sources_total=len(cfg["sources"]),
                completed_sources=0,
                source_index=0,
                phase="preflight",
                source=None,
                bytes=0,
                message="A validar as origens e o recetor.",
                results=[],
                schedule_date=scheduled_date or self.engine.state.data.get("l4", {}).get("schedule_date"),
            )
            self.event("preflight", "Sincronização iniciada; a validar as origens e o recetor.")
            try:
                counts = {}
                for item in cfg["sources"]:
                    self.update(source=item["name"])
                    self.event("preflight", f"A validar a origem {item['name']}.")
                    root = await source_identity(item)
                    count = await filesystem_call(count_files, root)
                    old = previous.get(item["source"], 0)
                    if count == 0 or (
                        old >= 20 and count < old * (1 - self.engine.cfg["max_missing_fraction"])
                    ):
                        raise ValueError(
                            "A origem está vazia ou perdeu demasiados ficheiros; sincronização bloqueada."
                        )
                    counts[item["source"]] = count
                    await self.probe(cfg, item)
                for index, item in enumerate(cfg["sources"], 1):
                    self.parser = Progress()
                    self.progress_at = 0
                    self.progress_log_at = asyncio.get_running_loop().time()
                    self.update(
                        source=item["name"],
                        source_index=index,
                        phase="planning",
                        planned_bytes=None,
                        planned_files=None,
                        eta=None,
                        files_transferred=None,
                        files_remaining=None,
                        files_total=None,
                        percent=0,
                        bytes=0,
                        speed=None,
                        message="A comparar alterações antes do envio.",
                    )
                    self.event(
                        "planning",
                        f"Origem {index}/{len(cfg['sources'])}: {item['name']}; a comparar alterações.",
                    )
                    # Streaming plan counts prevent large trees from truncating deletion guards.
                    plan = Plan()
                    await execute(
                        rsync_args(cfg, self.directory, item, dry=True),
                        cfg["timeout_minutes"] * 60,
                        plan.feed,
                    )
                    plan.finish()
                    self.update(planned_bytes=plan.bytes, planned_files=plan.files)
                    self.event(
                        "planned",
                        f"{item['name']}: plano concluído; {plan.files} ficheiros, {plan.bytes} bytes a atualizar; {plan.deletes} eliminações previstas.",
                    )
                    if plan.deletes > cfg["max_delete_files"]:
                        raise ValueError(
                            "O espelho excede o limite de eliminações. Nenhum envio foi iniciado nesta origem."
                        )
                    remote = await self.probe(cfg, item)
                    if remote["free_bytes"] - plan.bytes < cfg["min_free_gib"] * 1024**3:
                        raise ValueError("O espaço livre previsto após a cópia fica abaixo do mínimo.")
                    self.update(
                        phase="transferring",
                        message="A enviar alterações. Os ficheiros removidos serão eliminados no fim.",
                    )
                    self.event(
                        "transferring",
                        f"A enviar {item['name']}; integridade da transferência validada pelo rsync.",
                    )
                    await source_identity(item)
                    await execute(
                        rsync_args(cfg, self.directory, item), cfg["timeout_minutes"] * 60, self.progress
                    )
                    self.parser.finish()
                    self.publish_progress(force=True)
                    self.update(phase="verifying")
                    self.event("verifying", f"{item['name']}: envio terminado; a confirmar o recetor.")
                    await self.probe(cfg, item)
                    results.append({"name": item["name"], "status": "success"})
                    self.update(results=results, completed_sources=len(results))
                    self.event(
                        "source_complete",
                        f"Origem {item['name']} concluída ({len(results)}/{len(cfg['sources'])}).",
                    )
                self.update(
                    status="success",
                    phase="complete",
                    finished_at=now(),
                    last_success=now(),
                    counts=counts,
                    source=None,
                    message="Espelho atualizado.",
                )
                self.event("complete", "Espelho atualizado; todas as origens concluídas.")
            except asyncio.CancelledError:
                self.update(
                    status="interrupted",
                    phase="interrupted",
                    finished_at=now(),
                    message="Envio interrompido. Os ficheiros já recebidos permanecem no destino.",
                )
                self.event(
                    "interrupted", "Envio interrompido; os ficheiros já recebidos permanecem no destino.", 30
                )
                raise
            except Exception as error:
                message = (
                    str(error)
                    if isinstance(error, ValueError)
                    else "A sincronização falhou; verifica a configuração e o recetor."
                )
                self.update(
                    status="failed", phase="failed", finished_at=now(), message=message, results=results
                )
                self.event("failed", message, 40)
                await self.engine.notify("L4: " + message)

    def progress(self, text):
        self.parser.feed(text)
        self.publish_progress()

    def publish_progress(self, force=False):
        loop = asyncio.get_running_loop()
        if self.parser.last is None or (not force and loop.time() - self.progress_at < 2):
            return
        self.progress_at = loop.time()
        self.update(**self.parser.last)
        if force or loop.time() - self.progress_log_at >= 60:
            self.progress_log_at = loop.time()
            metric = self.parser.last
            self.event(
                "transferring",
                f"{self.engine.state.data['l4'].get('source')}: {metric['bytes']} bytes processados; {metric['percent']}%; ritmo rsync={metric['speed']}; ficheiros transferidos={metric['files_transferred']}; ETA={metric['eta'] or '—'}.",
            )

    def due(self, instant):
        cfg = self.engine.cfg["l4"]
        local = instant.astimezone(ZoneInfo(self.engine.cfg["timezone"]))
        return (
            cfg["enabled"]
            and cfg["schedule_enabled"]
            and self.engine.cfg["execution_mode"] == "production"
            and (
                not self.engine.state.data.get("l4", {}).get("schedule_not_before")
                or instant >= datetime.fromisoformat(self.engine.state.data["l4"]["schedule_not_before"])
            )
            and local.strftime("%H:%M") >= cfg["schedule_time"]
            and self.engine.state.data.get("l4", {}).get("schedule_date") != local.date().isoformat()
        )


def count_files(root):
    def failed(error):
        raise error

    count = 0
    for _, _, files in os.walk(root, followlinks=False, onerror=failed):
        count += len(files)
    return count


class Plan:
    def __init__(self):
        self.buffer, self.deletes, self.bytes = "", 0, 0
        self.files = 0

    def feed(self, chunk):
        self.buffer += chunk
        lines = self.buffer.split("\n")
        self.buffer = lines.pop()
        if len(self.buffer) > 8192:
            raise ValueError("Resposta de planeamento inválida.")
        for line in lines:
            if line.startswith("*deleting"):
                self.deletes += 1
            elif re.fullmatch(r"[<>ch.][^|]{10}\|\d+", line):
                if line[1] == "f" and line[0] in "<>c":
                    self.files += 1
                    self.bytes += int(line.split("|")[1])

    def finish(self):
        if self.buffer:
            self.feed("\n")


def routes(app, engine):
    from aiohttp import web
    from .config import public_config, validate

    if not hasattr(engine, "mirror"):
        engine.mirror = Mirror(engine)

    async def status(request):
        return web.json_response(engine.mirror.status())

    async def storage(request):
        result = []
        for path in [engine.cfg["source_mount"]] + engine.cfg["additional_sources"]:
            try:
                record = json.loads(await execute(["findmnt", "-J", "-T", path, "-o", "UUID,FSTYPE"]))[
                    "filesystems"
                ][0]
                if record.get("fstype") == "ext4" and record.get("uuid"):
                    result.append({"source": path, "uuid": record["uuid"]})
            except Exception:
                continue
        return web.json_response({"sources": result})

    async def action(request):
        if await request.json() != {}:
            raise ValueError("Esta ação requer um objeto vazio.")
        name = request.match_info["action"]
        if name == "pair":
            return web.json_response(await engine.mirror.pairing())
        if name == "test":
            return web.json_response(await engine.mirror.test())
        if name == "cancel":
            await engine.mirror.cancel()
        elif name == "run":
            engine.mirror.start()
        else:
            raise ValueError("Ação L4 inválida.")
        return web.json_response({"accepted": True}, status=202)

    async def config(request):
        if request.method == "GET":
            return web.json_response(engine.cfg["l4"])
        raw = await request.json()
        updated = validate(dict(engine.cfg, l4=raw))
        disabling = engine.mirror.busy and updated["l4"] == dict(
            engine.cfg["l4"], enabled=False, schedule_enabled=False
        )
        if (engine.busy or engine.mirror.busy or engine.state.data.get("needs_cleanup")) and not disabling:
            raise ValueError(
                "Conclui a execução antes de alterar o destino L4. Podes desativar o L4 em curso."
            )
        old = engine.cfg["l4"]
        if any(updated["l4"][key] != old[key] for key in ("host_key", "destination_uuid", "sources")):
            engine.mirror.update(
                status="idle",
                last_success=None,
                connection="pending",
                counts={},
                results=[],
                source=None,
                speed=None,
                percent=0,
                message="Destino ou origens alterados. Testa a ligação antes do envio.",
            )
        if updated["l4"]["schedule_enabled"] and any(
            updated["l4"][k] != old[k] for k in ("enabled", "schedule_enabled", "schedule_time")
        ):
            from .schedule import next_run

            engine.mirror.update(
                schedule_not_before=next_run(
                    datetime.now(timezone.utc), dict(updated, schedule_time=updated["l4"]["schedule_time"])
                )
            )
        atomic_json(engine.state.directory / "config.json", updated)
        engine.cfg = updated
        if disabling:
            await engine.mirror.cancel()
        return web.json_response(public_config(updated)["l4"])

    app.router.add_get("/v1/l4/status", status)
    app.router.add_get("/v1/l4/storage", storage)
    app.router.add_get("/v1/l4/config", config)
    app.router.add_put("/v1/l4/config", config)
    app.router.add_post("/v1/l4/{action}", action)
