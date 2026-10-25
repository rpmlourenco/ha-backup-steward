"""One bounded integrity report and one error budget for the whole execution."""

from pathlib import PurePosixPath
from . import telemetry


class IntegrityFailure(RuntimeError):
    def __init__(self, report):
        self.report = report
        reason = (
            "Limite global de erros de integridade atingido"
            if report["limit_reached"]
            else "Verificação de integridade incompleta"
            if not report["complete"]
            else "Verificação de integridade concluída com erros"
        )
        super().__init__(f"{reason}: {report['error_count']} erro(s); consultar resumo por origem")


class IntegrityRun:
    def __init__(self, limit, on_report=None):
        self.limit = limit
        self.on_report = on_report or (lambda report: None)
        self.areas = []
        self.seen = set()
        self.area_seen = {}
        self.samples = []
        self.limit_reached = False
        self.finished = False
        self.aborted = False

    def plan(self, source, destination, kind):
        key = (str(source), str(destination), kind)
        for area in self.areas:
            if (area["source"], area["destination"], area["kind"]) == key:
                return area
        area = dict(
            source=str(source),
            destination=str(destination),
            kind=kind,
            status="pending",
            errors=0,
            checked=0,
            detail="",
        )
        self.areas.append(area)
        self.area_seen[id(area)] = set()
        return area

    def report(self):
        status = (
            "aborted"
            if self.aborted or self.limit_reached
            else ("failed" if self.finished and self.seen else "passed" if self.finished else "running")
        )
        return dict(
            status=status,
            complete=self.finished and not self.aborted and not self.limit_reached,
            error_count=len(self.seen),
            error_limit=self.limit,
            limit_reached=self.limit_reached,
            areas=[dict(a) for a in self.areas],
            samples=[dict(s) for s in self.samples],
        )

    def publish(self):
        self.on_report(self.report())

    def begin(self, area):
        area["status"] = "running"
        self.publish()

    def problem(self, area, path, category, detail=""):
        key = (
            str(PurePosixPath(area["source"]) / path)
            if path
            else ("operation", area["source"], area["destination"], area["kind"])
        )
        if key not in self.area_seen[id(area)]:
            self.area_seen[id(area)].add(key)
            area["errors"] += 1
        if key not in self.seen:
            self.seen.add(key)
            if len(self.samples) < 8:
                self.samples.append(
                    dict(source=area["source"], path=telemetry.process_text(path or ""), category=category)
                )
        if detail:
            area["detail"] = telemetry.process_text(detail)
        if len(self.seen) >= self.limit:
            self.limit_reached = True
            area["status"] = "stopped"
            self.publish()
            raise IntegrityFailure(self.report())
        if len(self.seen) <= 8 or len(self.seen) % 100 == 0:
            self.publish()

    def line(self, area, line):
        line = line.rstrip("\r\n")
        if len(line) < 2 or line[0] not in "=+-*!" or line[1] != " ":
            raise RuntimeError("Formato inesperado no relatório de integridade do rclone")
        symbol, relative = line[0], line[2:]
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or ".." in path.parts:
            raise RuntimeError("Caminho inesperado no relatório de integridade")
        area["checked"] += 1
        if symbol != "=":
            self.problem(
                area,
                relative,
                {
                    "+": "missing_on_destination",
                    "-": "missing_on_source",
                    "*": "different",
                    "!": "read_error",
                }[symbol],
            )

    def end(self, area, failed=False):
        area["status"] = "failed" if failed or area["errors"] else "success"
        self.publish()

    def abort(self):
        if not self.finished:
            self.aborted = True
            for area in self.areas:
                if area["status"] == "running":
                    area["status"] = "stopped"
            self.publish()

    def finish(self):
        if not self.areas or any(a["status"] in ("pending", "running", "stopped") for a in self.areas):
            self.abort()
            raise IntegrityFailure(self.report())
        self.finished = True
        self.publish()
        if self.seen:
            raise IntegrityFailure(self.report())
        return self.report()
