# HA Backup Steward

Open the web interface to configure disks, retention, encryption and the app-owned schedule.

Starts in **observation mode** with scheduling off. Do not enable production until the isolated pilot has passed. Keep the existing NASRUI udev rule and backup scripts during testing.

Physical disk operations require protection mode off, host D-Bus, hardware access and the host journal. No host-installed script or SSH key is used by this app.

**Manual destination controls:** use **Ligar e montar** to leave the configured destination mounted without running a backup. Use **Desmontar e desligar** when finished; power-off still requires safe removal and final SCSI confirmation. Backups and settings changes are blocked while the manual session is open. Close files using the disk before shutdown. The integration includes the same controls; observation mode disables them. Errors notify; successful manual operations only update status/logs.

Read the installation guide, security model and migration checklist at:

https://github.com/rpmlourenco/ha-backup-steward

## Restore L3

Use **Restaurar L3** to restore the current L3 mirror to all original areas or any selected combination. Close the manual destination session first. Stop applications that use the selected areas before restoring; Steward does not stop or restart them automatically.

1. Select the areas and whether to run full SHA-256 after copying.
2. Choose **Analisar seleção**. This only inspects the original areas and does not power on the backup disk.
3. Missing original data directories are listed for creation after confirmation. The primary disk mount must still exist and pass UUID validation; an absent mount is never replaced by an ordinary directory. Parent identities are pinned and changes require another preparation.
4. If any selected area contains files or directories, explicitly authorize erasing its complete contents. Without that confirmation, cancel. A new nonempty area or changed identity requires another preparation.
5. Choose **Restaurar agora**. The app mounts L3 read-only, validates every selected mirror, area and space estimate before deleting anything, clears only confirmed original areas and copies the mirror directly into them.

Files are written in place (`rclone --inplace`), without a temporary full copy or history directory. Selected original content is deleted before copying. Cancellation or a write failure can leave a partial restore; the read-only L3 mirror is preserved so the restore can be repeated. Normal file-size validation is always performed; optional SHA-256 uses the existing global integrity error limit. Missing/empty mirrors, symbolic/hard links, special files and nested mounts block preparation.

Restore shares the app lock, cancellation and safe disk shutdown. It does not create L2, apply retention, modify the backup summary, or advance backup/checksum schedules. Existing backup policies and keys remain unchanged. This restores the current L3 file mirror; L2 archives are restored directly through Home Assistant.
