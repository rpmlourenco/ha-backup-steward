# 0.1.15 — Create missing original directories during L3 restore

- Accept absent original data areas in restore preparation instead of returning a server error. Show directories that will be created after confirmation.
- Keep preview and confirmation free of filesystem writes. Validate all selected mirrors and space estimates before creating missing directories or clearing existing data.
- Pin the nearest existing parent and create the confirmed path without following symbolic links. Changed paths require a new preparation; a missing primary disk mount remains an error and is never replaced with an ordinary directory.
- Preserve direct in-place copying, optional SHA-256, cancellation, read-only L3 and safe shutdown. Add regression tests for missing directories, changed parents, links, insufficient space and test-area isolation.

# 0.1.14 — Restore L3 by area

- Restore the current L3 mirror to all original areas, one area or a selected combination from the app UI.
- Prepare the selection first; require explicit confirmation before clearing nonempty areas. Bind the single-use preparation to the selected roots, configuration and a ten-minute expiry.
- Validate every selected mirror and area, reject unsafe trees and estimate available space before deletion. Mount L3 read-only and write restored files directly with `--inplace`, without a temporary duplicate.
- Always verify restored sizes and optionally compare full SHA-256 after copying. Preserve the existing integrity error budget, safe cancellation and final disk shutdown.
- Do not create or restore L2, apply retention, replace the last backup summary or advance backup/checksum dates. L2 restore remains available directly through Home Assistant.
- Add isolated restore, API, mount and desktop/mobile UI regressions. Stop applications using selected original areas before restore; cancellation or failure can leave a partial restore while the L3 mirror remains intact.

# 0.1.13 — Keep backup results separate from verification

- Persist the latest backup outcome (L2, L3, retention and safe shutdown) independently of later integrity checks or manual disk controls.
- Show the backup date and operation context in the journey card; update the last successful copy date only after a successful production backup.
- Mark retention as not applicable for verification-only operations and clear stale per-run removal counts. Retention behavior is unchanged.
- Clarify that individual-area checks do not count as a complete verification of all sources. Migrate identifiable existing backup results; older results already overwritten by checks remain unavailable until the next backup.
- Add persistence, migration and browser regressions. Preserve configuration, encryption keys, scheduling, disk safety and integration entity IDs.

# 0.1.12 — Disable operations until status is confirmed

- Disable operation controls in the initial HTML, before JavaScript/status loading, and during pending status reads or connection failure.
- Enable actions only after validating current status; ignore outdated responses and prevent mutation requests while status is unknown.
- Keep open confirmations blocked if connection is lost or a backup becomes active. Preserve available cancellation only when the app explicitly permits it.
- Add a browser regression suite to CI for loading, active backups, outages, malformed status, reconnection and reply ordering. No running backup is changed by this UI fix.

# 0.1.11 — Consistent UI loading after updates

- Fix reuse of older UI files after an app update. The affected HTTPS browser was running 0.1.9 HTML/JavaScript against the 0.1.10 API, which concealed the new backup choices.
- Serve HTML and API responses without caching. Give JavaScript and stylesheet files content-addressed URLs so an updated document requests its own matching assets.
- Keep the existing confirmation dialog layout, backup/checksum/cancellation behavior, keys, configuration and integration entity IDs.

# 0.1.10 — Manual checksum choice and safe cancellation

- Offer automatic, no full checksum and forced full checksum choices for manual backups. The dedicated backup_only mode bypasses overdue full checks while retaining normal copy/L2 validation and disk safety.
- Add authenticated cancellation controls to the app and Home Assistant integration. Local workers stop before safe closure; native creation and hardware transitions finish at safe boundaries.
- Persist an explicit cancelled outcome without advancing success dates. Preserve uncertain native jobs for recovery and notify only if an actual failure occurs.
- Add two integration buttons (19 entities total); keep configured-area checks and app-owned scheduling unchanged.

# 0.1.9 — Manual verification by area

- Choose all configured sources or one source/mirror pair when requesting manual SHA-256 verification in the app.
- Support optional exact configured source selection through the authenticated API and Home Assistant run action; existing buttons and schedules retain all-source behavior.
- Preserve global checksum/success dates after single-area verification. Keep test data separation, error limits, error-only notifications and safe disk lifecycle.
- Generate choices from configured sources and reject arbitrary paths or source selection on backup operations. No change is made to a currently running verification.

# 0.1.8 — Global integrity overview

- Continue verification across source/mirror pairs after integrity differences, missing files and read errors, collecting a complete overview instead of stopping at the first failed pair.
- Apply one configurable global limit (default 1,000) across size, L2 archive and full SHA-256 checks, counting overlapping affected files once. Stop the active process at the limit and close the destination safely.
- Show per-check results, unverified areas, global counts and bounded examples in the app; expose results through the integration Status attributes.
- Failed or incomplete integrity never advances the successful checksum calendar or permits L3 history expiry. Copy/sync and disk/path safety failures remain fail-fast. Preserve error-only notifications and existing settings/keys.

# 0.1.7 — Live rclone diagnostics

- Relay structured rclone errors immediately with the affected file and a safe reason, rather than discarding stderr and retaining only an exit code.
- Log concise progress every minute and durable per-source start/completion events for copy and SHA-256 verification.
- Redact credentials, authentication schemes and URLs; ignore arbitrary JSON fields and limit detailed errors to eight plus a count of additional errors.
- Preserve failure semantics, retention safeguards and safe shutdown. No automatic retry or checksum relaxation is introduced.

# 0.1.6 — Manual destination controls

- Add power-on/mount and safe unmount/power-off controls to the app and companion integration.
- Manual sessions preserve the host lock and physical identities until closed, blocking concurrent backups, scheduling and configuration changes.
- Reuse native-job completion checks and final kernel SCSI proof for shutdown. No source mount, backup, retention or successful-backup timestamp is changed by manual controls.
- Persist interrupted sessions for recovery after app restart; retain error-only notifications and observation-mode restrictions.

# 0.1.5 — Notify only on errors

- Successful backups and checksum runs no longer create Home Assistant notifications. Results remain visible in the app and console log.
- Failure and retention-error notifications remain enabled; a successful completion cannot overwrite a retention-error notification.
- Existing settings and keys are preserved.

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
