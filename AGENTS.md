# Backup Steward development

- App-owned schedule only. Do not add scheduling automations to Home Assistant.
- Keep the existing NASRUI `80-local-media.rules` automount unchanged; the app only validates the source.
- Existing backup scripts and patched Auto Backup remain the rollback path throughout the pilot. Do not change, disable or remove them without explicit deployment/migration approval.
- Repository implementation/testing does not authorize installation, a backup run, service calls, disk mounting, retention, restarts or power changes on a user's HAOS.
- Never expose tokens, encryption keys, private app data or authenticated URLs in logs, test output, screenshots, commits or issues.
- Keep machine identifiers in configuration. Source/destination UUIDs must be checked; paths must remain confined; all operations are allowlisted.
- Every physical shutdown must prove final SCSI cache synchronization before power-off. Unknown states leave power on.
- Tests use temporary files, fake APIs and mocked hardware only. Never map real disks, host D-Bus or the user's HA configuration into development test containers.
- Validate the integration in an isolated official Home Assistant image and build the app for aarch64 before a pilot release. Automated tests cannot substitute for hardware acceptance.
- Use small cohesive modules within the packaged app. Fewer loose host artifacts is the goal, not a single enormous source file.
