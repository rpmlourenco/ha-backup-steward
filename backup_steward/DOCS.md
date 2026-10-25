# HA Backup Steward

Open the web interface to configure disks, retention, encryption and the app-owned schedule.

Starts in **observation mode** with scheduling off. Do not enable production until the isolated pilot has passed. Keep the existing NASRUI udev rule and backup scripts during testing.

Physical disk operations require protection mode off, host D-Bus, hardware access and the host journal. No host-installed script or SSH key is used by this app.

**Manual destination controls:** use **Ligar e montar** to leave the configured destination mounted without running a backup. Use **Desmontar e desligar** when finished; power-off still requires safe removal and final SCSI confirmation. Backups and settings changes are blocked while the manual session is open. Close files using the disk before shutdown. The integration includes the same controls; observation mode disables them. Errors notify; successful manual operations only update status/logs.

Read the installation guide, security model and migration checklist at:

https://github.com/rpmlourenco/ha-backup-steward
