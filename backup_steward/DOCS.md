# HA Backup Steward

Open the web interface to configure disks, retention, encryption and the app-owned schedule.

Starts in **observation mode** with scheduling off. Do not enable production until the isolated pilot has passed. Keep the existing NASRUI udev rule and backup scripts during testing.

Physical disk operations require protection mode off, host D-Bus, hardware access and the host journal. No host-installed script or SSH key is used by this app.

Read the installation guide, security model and migration checklist at:

https://github.com/rpmlourenco/ha-backup-steward
