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
