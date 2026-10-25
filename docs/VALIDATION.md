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

## 0.1.4 follow-up

- 66 isolated tests pass, including host removal request properties with real D-Bus Variant validation, missing lease, unsafe device names and failed/unknown command outcomes. Existing kernel flush failure/absence tests remain required.
- Read-only pilot diagnosis confirmed sysfs is read-only inside the app and writable on HAOS. The HDD had been unmounted before the 0.1.3 recovery failed; the disk remained present and power remained on. The stock host tee utility exists.
- The official Home Assistant 2026.10.3 smoke and ARM64 build pass. Actual execution of the new host removal unit remains a manual hardware acceptance step; diagnostics did not run it.

## 0.1.5 follow-up

- 66 isolated tests pass, including silent success and notifications for backup/retention failures. The retention-error notification remains even when L3 and safe shutdown complete.
- Official Home Assistant 2026.10.3 integration smoke and ARM64 image build pass. No Home Assistant services or hardware operations were invoked by this change.

## 0.1.6 follow-up

- 76 isolated tests pass, adding manual mount/close, concurrency and observation guards, authenticated/strict API requests, failure/cancellation ownership, original shutdown identity, unchanged backup timestamps, schedule blocking and durable restart recovery.
- Official Home Assistant 2026.10.3 smoke passes with 17 entities, authenticated manual disk actions and button availability transitions. All actions target a simulated app API.
- Browser checks pass for manual request bodies/confirmation text, observation restrictions, mounted-state presentation and desktop/mobile layout without horizontal overflow or JavaScript errors.
- Manual hardware acceptance is pending; no real disk or power operation was invoked during implementation.

## 0.1.7 follow-up

- 81 isolated tests pass, adding error delivery before process completion, credential/URL redaction, bounded error output, concise progress and subprocess timeout cleanup.
- A real rclone 1.75.1 fixture with the exact production checksum flags returned 0 for identical files and 1 with sha256 differ for changed content of the same size. The new adapter captured both the reason and affected file immediately. Only temporary isolated files were used.
- The original production failure's stderr was discarded by 0.1.6 and cannot be reconstructed. Read-only size/name comparisons currently match all three real source/mirror trees; this does not establish checksum equality or the cause of the original failure.
- No new full checksum, copy, retention or hardware operation was initiated by these changes. The real destination remains in the user-opened manual session until the user closes it.

## 0.1.8 follow-up

- 96 isolated tests pass, including real rclone 1.75.1 temporary-file checks: differing hashes on two origins followed by a successful third origin, individual missing files, accumulated cross-origin ceiling, unavailable trees, I/O errors, deduplication across overlapping checks, incomplete-area rejection and bounded samples.
- A live fake rclone subprocess emits an error and waits 30 seconds; reaching the limit terminates it within the five-second test deadline. Aggregate integrity failure preserves previous success/checksum timestamps, blocks L3 history expiry, notifies once and performs safe cleanup through mocked hardware.
- Authenticated API report persistence and secret redaction pass. Official Home Assistant 2026.10.3 smoke passes with all 17 entities and the new Status report attributes.
- The ARM64 app image builds. Local browser checks pass for error overview, threshold/unchecked presentation, manual controls, desktop/mobile layouts and no JavaScript exceptions or horizontal overflow.
- All new execution checks use temporary files and fake APIs/hardware. No production backup, mount, power action or full verification was started for this implementation. USB transport diagnosis is a separate follow-up.

## 0.1.9 follow-up

- 105 isolated tests pass. New cases cover configured-source selection against real temporary rclone trees, excluded corrupt areas, single-area failures, separate test-tree mapping, unchanged all-source backup planning, strict request validation and dynamically advertised choices.
- Engine tests verify that single-area success preserves global success/checksum timestamps, performs no backup/copy/retention, keeps safe cleanup and remains silent on success. Full checksum still advances the global checksum date. Invalid scopes are rejected before work begins.
- Official Home Assistant 2026.10.3 smoke passes with scoped run action requests, invalid backup/scope rejection, Status scope attributes and the unchanged 17 entities.
- ARM64 app build and local desktop/mobile browser checks pass. The selector generates choices from app status, sends a source only for single-area checks, resets to all areas for subsequent requests, remains hidden for backups, and cannot start work while busy.
- No production operation or configuration change was performed. An already running production verification is unaffected by this local implementation.
