# HA Backup Steward

Open the web interface to configure disks, retention, encryption and the app-owned schedule.

Starts in **observation mode** with scheduling off. Do not enable production until the isolated pilot has passed. Keep the existing NASRUI udev rule and backup scripts during testing.

Physical disk operations require protection mode off, host D-Bus, hardware access and the host journal. No host-installed script or SSH key is used by this app.

**Manual destination controls:** use **Ligar e montar** to leave the configured destination mounted without running a backup. Use **Desmontar e desligar** when finished; power-off still requires safe removal and final SCSI confirmation. Backups and settings changes are blocked while the manual session is open. Close files using the disk before shutdown. The integration includes the same controls; observation mode disables them. Errors notify; successful manual operations only update status/logs.

Read the installation guide, security model and migration checklist at:

https://github.com/rpmlourenco/ha-backup-steward

## Native L1 in the Steward cycle

L1 is optional (`l1_enabled`, off on upgrade). Once enabled, scheduled and manual backups run **L1 → encrypted L1 delivery to NASRUI → L2 → L3 → safe shutdown**. A checksum-only or restore operation never creates L1. The existing app schedule controls the whole sequence; no HA automation is added.

Before enabling L1 in Steward, set only the native backup schedule to **Never** in **Settings → System → Backups**. Preserve its app/folder selection, local/cloud destinations, password and retention. Keep local storage enabled and encrypted. Steward rejects enabling L1 if the native scheduler is still active, and checks again before each L1. The native L1 key remains the HA recovery-kit key; the L2 password in Steward is independent.

The native L1 remains in its configured destinations. Steward also downloads the same encrypted archive, without decrypting it, to `<source>/backups/home-assistant/level-1` (`l1_directory`). This is a second copy on the NASRUI/L2 storage tier, not an archive embedded inside another archive. Only Steward-managed copies in that directory use `l1_keep` (default 3); native retention is unchanged. The full source tree is mirrored to L3, so the L1 copies also reach HAL3 and receive a separate SHA-256 archive comparison even when the monthly full scan is skipped.

L1 creation and all configured native destination deliveries must finish before L2 starts. A failure stops the cycle and notifies; inspect the separate level results. L1 is created before HAL3 is powered on. A lost creation response or app stop blocks new work until recovery confirms the original native job. Recovery never recreates L1, and never unmounts or powers off HAL3 if it was not acquired. A lost response with no known job requires diagnosis rather than guessing which backup belongs to the run.

In test mode, native L1 creation still uses the configured HA selection/destinations. Its additional copies and L3 test mirror are isolated under `backup-steward-test`; Steward skips archive retention there. Test mode is not a dry run and native HA retention still applies.

Restoring L1 remains a native HA operation using the native recovery key. L1 snapshots and per-level results survive later checks in the backup summary; the companion integration adds an L1 result sensor without renaming existing entities.

## Scheduling and progress

Enabling the schedule or changing its time/timezone arms the next future occurrence. Enabling a 04:00 schedule in the evening waits until 04:00 the next day. This boundary survives app restarts. Once active, a missed daily occurrence can still run after a temporary interruption; checksum catch-up is unchanged.

Settings cannot be saved while an operation or destination session is active, recovery is pending, or the connection is unconfirmed. The settings dialog explains the reason and keeps unsaved edits while the status updates. Full SHA-256 verification shows the cumulative processed-file count and active area on the main screen, refreshed by the normal status polling. Size checks and the separate L2 archive checksum are excluded from this full-check count.

The obsolete confirmation about disabling the old agenda has been removed. Old saved values are ignored on upgrade; encryption, disk identities and concurrency checks remain required.

## Restore L3

Use **Restaurar L3** to restore the current L3 mirror to all original areas or any selected combination. Close the manual destination session first. Stop applications that use the selected areas before restoring; Steward does not stop or restart them automatically.

1. Select the areas and whether to run full SHA-256 after copying.
2. Choose **Analisar seleção**. This only inspects the original areas and does not power on the backup disk.
3. Missing original data directories are listed for creation after confirmation. The primary disk mount must still exist and pass UUID validation; an absent mount is never replaced by an ordinary directory. Parent identities are pinned and changes require another preparation.
4. If any selected area contains files or directories, explicitly authorize erasing its complete contents. Without that confirmation, cancel. A new nonempty area or changed identity requires another preparation.
5. Choose **Restaurar agora**. The app mounts L3 read-only, validates every selected mirror, area and space estimate before deleting anything, clears only confirmed original areas and copies the mirror directly into them.

Files are written in place (`rclone --inplace`), without a temporary full copy or history directory. Selected original content is deleted before copying. Cancellation or a write failure can leave a partial restore; the read-only L3 mirror is preserved so the restore can be repeated. Normal file-size validation is always performed; optional SHA-256 uses the existing global integrity error limit. Missing/empty mirrors, symbolic/hard links, special files and nested mounts block preparation.

Restore shares the app lock, cancellation and safe disk shutdown. It does not create L2, apply retention, modify the backup summary, or advance backup/checksum schedules. Existing backup policies and keys remain unchanged. This restores the current L3 file mirror; L2 archives are restored directly through Home Assistant.
