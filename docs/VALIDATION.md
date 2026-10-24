# Initial implementation validation

Validated locally in isolated containers and a local browser:

- **48 automated tests**: policy bounds, default observation mode, secrets, scheduling/DST/checksum catch-up, authenticated API and ingress protection, native download/retention ordering, corrupt/linked archive rejection, source confinement, non-destructive test copy, concurrent-job guards, interrupted/failed jobs, terminal native failure, recovery identity, kernel flush success/failure/absence and blocked unsafe power-off.
- **Official Home Assistant 2026.10.3 smoke test**: config flow, pairing authentication, coordinator polling, 15 entities (8 sensors, 3 binary sensors, 4 buttons), manual run service and integration unload.
- **Container builds**: amd64 and aarch64, Python 3.14, Rclone 1.75.1. Base images are pinned by manifest digest and direct Python dependencies by tested version.
- **UI**: desktop, mobile (390 px), light/dark, settings dialog, disabled execution in observation mode; no JavaScript exceptions or horizontal mobile overflow in the preview. Screenshots contain simulated observation data only.
- Ruff static checks and Python compilation.

These results **do not** establish HAOS hardware compatibility or production acceptance. No app installation, native backup, disk operation, power switch action or migration was performed on the user's instance during implementation.

Still required in an explicitly authorized pilot: Supervisor installation/schema acceptance; actual host D-Bus permissions; propagation of the transient HAL3 mount to app/consumers; real native encrypted backup creation and restore/decryption; real kernel journal visibility and final SCSI flush; interrupted app/reboot/restore behavior; operational timings and cutover acceptance. Keep the existing backup implementation and NASRUI udev rule until those checks pass.

## 0.1.1 follow-up

- 54 isolated tests pass, adding host journal selection (persistent/volatile), missing-journal rejection before power-on or unmount, explicit journal selection throughout final SCSI proof, and matching repository/integration/app branding.
- Read-only diagnosis on the installed 0.1.0 app confirmed that implicit `journalctl` discovery returned no cursor, while `--directory=/var/log/journal` returned a kernel cursor. No disk operations were used for that diagnosis.
- The HA smoke test additionally verifies that Core detects the integration's local `brand` directory.
- 0.1.1 remains experimental; it does not activate the schedule, alter existing secrets or bypass physical acceptance.

## 0.1.2 follow-up

- 62 isolated tests pass, including real app startup with console output, enabled/disabled schedule summaries, workflow steps, credential redaction, exact native-job permission checks and recovery presentation while busy.
- Official Home Assistant 2026.10.3 smoke passes, including recovery sensor transitions from a running job to an interrupted job needing cleanup.
- The aarch64 app image builds successfully. No real disks, host D-Bus or production HA configuration are mounted into these tests.
- The installed pilot's HTTP 403 was traced to the Supervisor `backup` role rejecting `/jobs/<uuid>`. This release requests `manager` and checks access before powering the destination. Hardware recovery and a complete L2/L3 run remain manual pilot acceptance steps.

## 0.1.3 follow-up

- 63 isolated tests, the official Home Assistant 2026.10.3 smoke test and the ARM64 image build pass.
- Read-only inspection of the actual HDD confirmed that PATH/TYPE/UUID output is flat without `--tree`, and correctly nested with it. The disk has one ext4 partition with the configured UUID. The failed 0.1.2 recovery stopped before unmount; no disk operation or power change was performed by these diagnostics.
- Regression tests model that exact lsblk behavior and preserve rejection of invalid layouts before sync, unmount or kernel removal. Hardware safe-removal acceptance remains pending.
