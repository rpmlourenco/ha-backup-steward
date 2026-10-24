# 0.1.4 — Host-side kernel disk removal

- Request kernel disk removal through host systemd over the existing D-Bus connection. App sysfs remains read-only.
- Use a temporary oneshot unit running the stock tee utility with fixed stdin and a validated kernel-device path; no shell or persistent host helper is installed.
- Require the owned backup lease and confirmed command completion. Disk disappearance and final kernel SCSI flush remain mandatory before power-off.
- Cover D-Bus property types, success/failure/timeout, unsafe device names and missing ownership in isolated tests.

# 0.1.3 — Explicit disk topology during safe removal

- Request explicit tree output from lsblk when validating a dedicated destination disk. PATH/TYPE/UUID JSON otherwise returns a flat list, causing valid one-partition disks to be rejected.
- Preserve UUID, dedicated-partition and final SCSI flush checks. Invalid layouts still block before sync, unmount or removal.
- Add regression coverage for real flat/tree output and rejection of empty, multiple, wrong-UUID or wrong-type partitions.

# 0.1.2 — Console logs, native-job permission and recovery presentation

- Write concise timestamped startup, schedule, phase, outcome and recovery messages to the Supervisor Log tab.
- Redact encryption, pairing and Supervisor credentials; never dump request bodies or headers.
- Use the Supervisor manager role required by native background-job polling; probe access before power-on.
- Include safe HTTP method/path/status in API failures.
- Distinguish active disk ownership from recovery required; running jobs no longer show a recovery warning.
- Preserve pending job identity and disk state across updates; never retry an unknown native backup automatically.

# 0.1.1 — Host journal and local branding

- Select the mapped host journal explicitly, independent of the container machine ID.
- Support persistent and volatile journal mappings and pin the selected directory through final SCSI confirmation.
- Refuse to power or mount the destination when no readable kernel cursor is available.
- Include matching app/repository/integration icons and high-resolution local brand assets.
- Existing settings, secrets and backups are preserved.

# 0.1.0 — Experimental

- App-owned daily calendar and monthly/weekly/manual full checksums.
- Native encrypted L2, atomic delivery and validated archive retention.
- Configurable L3 mirrors, dated history and bounded deletion guards.
- Host D-Bus disk lifecycle, shared legacy L3 lease and kernel-confirmed SCSI flush.
- Companion HA integration with manual controls and status entities.
- Responsive light/dark ingress UI and original app icon.
- Observation/test modes; established scripts and NASRUI automount remain available.

Hardware migration acceptance remains required before production.
