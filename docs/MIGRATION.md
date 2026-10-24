# Pilot and migration from the established scripts

No stage of this document is performed automatically by repository creation or app installation. The existing HomeAssistant repository, deploy whitelist, production scripts, patched Auto Backup and `80-local-media.rules` stay intact during development.

## 1. Observe

- Install app/integration with schedule off and execution mode `observe`.
- Confirm API pairing, UI, HA entities, source UUID/path, target UUID/path and intended power switch.
- Verify the rule still mounts NASRUI normally. Do not replace or remove it.
- Keep a known-good external backup and its recovery key.

## 2. Isolated test

- Choose a window with no existing L2/L3 execution; temporarily disable the old weekly schedule.
- Confirm this in app settings, supply the encryption secret, select `test`, leave scheduling off.
- Run one test manually. Test mode creates native backups and operates the real power switch: it is not a dry run.
- Verify separate source/destination test paths, no changes to production archives/history, archive encryption and native restore visibility.
- Confirm exact host mount, container visibility, shared legacy L3 lock, complete copy checks and kernel-confirmed SCSI flush before power-off.
- Test failure cases: missing source, wrong UUID, occupied mount, incomplete transfer, copy/checksum error, switch unavailable, journal unavailable, interrupted app/Core and unavailable Supervisor.
- Confirm the UI distinguishes delivered L2 from L3/retention/cleanup failures. Unknown native creation outcomes must not cause an unsafe unmount or power-off.

## 3. Controlled production pilot

- Keep both schedulers off and run manually with `production` only after the isolated test is accepted.
- Production retention can adopt valid existing `L2_NASRUI_apps_*` archives. Confirm desired retained count before the first execution.
- Verify L2 contents, decryption/restorability, L3 mirror/history, full checksums and safe power-off.
- Test app restart with a completed removal but failed power-off, and rejection if the disk is reinserted before recovery.
- Record durations and confirm 04:00 daily jobs fit the available window. Long jobs never overlap.

## 4. Cutover

- Leave the legacy weekly agenda disabled; enable the app agenda as the only scheduler.
- Enable **Start on boot** for the app after pilot acceptance; installation defaults to manual boot for safety.
- Choose daily L2/L3 at 04:00 and monthly/weekly checksum as appropriate.
- Confirm daily retention window: keeping 3 daily backups preserves about 3 days.
- Observe complete successful cycles, including a scheduled checksum and reboot/restore acceptance.
- Keep the old scripts available as manual rollback, but never run them concurrently with the app.

## 5. Retirement (separate explicit approval)

- Stop/disable the app schedule before using the rollback implementation.
- Only after acceptance, remove obsolete backup shell commands, loose scripts, host rclone and patched Auto Backup if no other automation uses it.
- Remove SSH credentials only after checking for other consumers.
- **Keep `80-local-media.rules`.** Source automount is intentionally outside the app.
- Keep unrelated FileBrowser customization untouched.
- Update disaster-recovery documentation and verify reinstall/restore from GitHub plus encrypted backups.
