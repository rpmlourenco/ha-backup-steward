# 0.1.11 — Visible backup choices and consistent UI loading

- Move backup verification choices to an always-visible selector above the backup button. Capture the chosen mode before confirmation and describe the exact requested operation.
- Serve HTML and API responses without caching. Give JavaScript and stylesheet files content-addressed URLs so an updated document requests its own matching assets.
- Preserve backup/checksum/cancellation behavior, keys, configuration and integration entity IDs.

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
