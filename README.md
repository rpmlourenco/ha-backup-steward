# HA Backup Steward

<img src="icon.png" width="80" height="80" alt="Backup Steward icon">

Encrypted Home Assistant backups, verified offline copies, and safe disk shutdown — packaged as one app with a lightweight companion integration.

> **Experimental 0.1.2.** Local automated tests and container builds are not HAOS hardware acceptance. Keep the established backup scripts and `80-local-media.rules` installed during the pilot. The app starts in **observation mode**, with its **schedule disabled**. Nothing is automatically installed on your Home Assistant.

![Backup Steward interface](docs/images/overview.png)

The interface follows Home Assistant's visual language, supports light/dark mode and mobile screens, and uses no remote fonts, JavaScript CDN, analytics, MQTT broker or frontend framework. Screenshot data is simulated.

## What it does

- **L2:** asks the Supervisor for an encrypted, compressed native backup of Home Assistant, all installed apps, `ssl`, `share` and local apps; excludes `media`.
- Streams the resulting archive into a confined `.partial` file, validates length and archive identity, flushes it to disk, and only then publishes the final filename.
- Removes the native temporary copy only after delivery. A transfer failure keeps the native backup and existing archives.
- **L3:** mirrors configured local media trees to an offline disk, preserving replaced/deleted files in dated history directories.
- Checks every copy by file set/size, SHA-256-checks the L2 archive mirror, and optionally SHA-256-checks all source/mirror files.
- Validates exact ext4 mounts and filesystem UUIDs, blocks competing workers, and refuses suspicious source disappearance or excessive deletion.
- Flushes, unmounts, flushes the whole physical disk and confirms its final SCSI cache synchronization in the host kernel journal **before** switching power off.
- Reports L2 delivery, L3 outcome, retention failures and incomplete physical cleanup independently.

The app uses the documented Supervisor backup API directly. **Auto Backup is not a dependency or fork.** The established scripts and their patched Auto Backup remain the rollback path until migration acceptance.

## Installation

1. Add `https://github.com/rpmlourenco/ha-backup-steward` in **Settings → Apps → App store → Repositories** (called add-ons on older versions).
2. Install **HA Backup Steward**. The Supervisor builds the Dockerfile for `aarch64` or `amd64`.
3. Disk lifecycle operations require protection mode off. The app requests host D-Bus, read-only journal access and hardware access; it does **not** request the Docker socket or host PID namespace. Review [the security model](docs/SECURITY.md) before enabling execution.
4. Start the app and open its web interface. Keep observation mode and the schedule off during initial inspection.
5. Add this repository as a HACS **Integration** custom repository and download **HA Backup Steward**, or copy only `custom_components/backup_steward` into your HA custom components directory manually. Reload/restart Core as appropriate for installing a new integration.
6. In app settings, choose **Ligar ao Home Assistant**, copy the pairing key, and add **HA Backup Steward** under **Devices & services**.
7. Enter `http://<app-internal-hostname>:8100` and the pairing key. Find the app slug/hostname in Supervisor app information. The host port is not exposed by default. Pairing uses the app's own scoped key, never a Home Assistant long-lived or Supervisor token.

No installer here changes your existing Home Assistant YAML or deploy whitelist. Follow [the migration checklist](docs/MIGRATION.md) for a controlled pilot.

## Configuration

The **app is the sole schedule owner**. The integration has no timer or scheduling automation. Its buttons and `backup_steward.run` action are manual entry points.

Configuration is validated in app settings. Supervisor options bootstrap a new installation; saved in-app settings take precedence. The UI writes an atomic private configuration in `/data/steward/config.json`. Secrets are not returned by the normal configuration/status API.

| Setting | Default |
| --- | --- |
| Execution | Observation, no changes |
| Schedule | Disabled; when enabled, daily at 04:00 |
| Time zone | `Europe/Madrid`, configurable |
| Full checksum | First Sunday of each month; weekly/manual alternatives |
| L2 retention | 3 completed backups, configurable |
| L3 history | 30 days |
| Free-space floor | 100 GiB on destination |
| Sync deletion ceiling | 1,000 files / 100 GiB per source |
| Missing-source guard | Refuse a drop over 20% when mirror has at least 20 files |
| Backup creation/download deadline | 360 minutes each |

**Three daily L2 backups represent roughly three days of recovery**, not three weeks. Choose a larger retention if needed. All protective limits remain enforced when invoking an operation through the API.

Set source/destination UUIDs, paths and power switch explicitly. Defaults contain no real disk UUIDs, IP addresses, tokens or entity-specific switch IDs. Source paths must be distinct direct children of `/media`. Relative archive/test paths cannot traverse outside their allowed roots. No format, partition, filesystem repair or force/lazy unmount operation exists.

### Modes

- **Observe:** serves configuration/status; never starts backups or controls power.
- **Test:** creates real encrypted L2 archives under `<source>/backup-steward-test/level-2`, copies only that test tree to `<destination>/backup-steward-test/mirror`, never rotates L2 or prunes L3 history. It exercises the actual disk lifecycle.
- **Production:** L2 into the configured archive directory and complete L3 mirror/history. Can recognize the established `L2_NASRUI_apps_*` archives for retention, but only after a new Steward L2 is delivered and validated.

Test/production require an encryption key and acknowledgement that the legacy schedule is disabled. Do not run the old scripts manually at the same time. A host transient `flock` lease shares the old L3 lock and blocks concurrent L3 workers, but cannot prevent an operator starting a separate legacy L2 creation.

### Checksums and scheduling

Every backup gets normal copy verification and L2 archive hashing. The configurable full checksum additionally reads every file in all configured source/mirror pairs. It is independent of the normal copy checks.

A failed or missed full checksum remains due until a successful production verification; the next backup retries it. A test checksum never clears the production deadline. The app deduplicates daily executions by local calendar date across DST/restarts. A missed 04:00 backup is caught up later that day if the app starts; it does not queue multiple historical days. A failed daily attempt is not retried automatically every minute. Manual retries remain available.

## Existing host rule and other apps

**`80-local-media.rules` stays unchanged.** NASRUI automount remains owned by that rule. The app validates it and never mounts/unmounts the source disk. Only the configured offline destination is managed by the app.

The FileBrowser startup hook and any unrelated packaged applications are out of scope. Existing backup filenames remain restorable using their original key; the new app cannot recover a lost key. Store the encryption secret/emergency kit somewhere accessible without HA.

## Status and recovery

The app keeps atomic bounded state and events in `/data/steward`. The integration exposes status/stage, L2/L3/retention results, last success/checksum, next run, running/schedule/recovery indicators, and manual backup/checksum/recovery buttons.

An interrupted app does not blindly restart a copy or power off the disk. The UI shows recovery required and blocks new executions. **Concluir fecho seguro** first checks native backup completion and physical identities. Unknown native job outcomes, disk disappearance without flush confirmation, or journal/identity failures intentionally require manual diagnosis. A restart of Core alone does not interrupt the app's work.

The app uses `backup: hot`: its own configuration and atomic state are included in L2 without stopping its worker. Restoring an in-progress state can require recovery/inspection; never assume an app backup is an image of HAOS or that it includes the external data disk.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
docker build -t ha-backup-steward:dev backup_steward
```

Run disk tests against fixtures only. Never mount `/dev`, host D-Bus, the HA filesystem or real disks into a test container. CI also validates component metadata/imports and builds the app. See [architecture](docs/ARCHITECTURE.md), [security](docs/SECURITY.md), and [migration acceptance](docs/MIGRATION.md).

MIT licensed. Rclone is bundled from its upstream versioned container image; see [third-party notices](THIRD_PARTY.md).

## Integration and repository icon

The repository and `custom_components/backup_steward/brand/` ship the same icon as the app, with a 512 px high-resolution variant. Home Assistant serves these local assets without a brands-repository submission. Update the integration through HACS and reload/restart Core to discover the new brand folder; a browser refresh may be needed for cached images. Older HACS frontends may still show their CDN placeholder on the repository card even when the HA integration icon works; see [HACS issue 5402](https://github.com/hacs/integration/issues/5402). This app does not patch HACS to work around that separate frontend limitation.

## App log

The Supervisor Log tab receives concise startup and workflow messages on stdout: version/mode, configuration ready, schedule enabled or disabled (time/zone/checksum), interface ready, execution ID, validation, power/mount, native creation and waiting, delivery, L3/checksum, retention, safe removal and result. Failure/recovery messages include the affected stage and safe HTTP method/path/status. Polling does not produce a log line on every request. Known encryption/pairing/Supervisor secrets are redacted.

The app requires the Supervisor `manager` role to read `/jobs/<uuid>` for native background-job completion. The narrower `backup` role permits creating backups but rejects that polling endpoint. Before powering the HDD, a harmless read-only permission probe verifies this exact access. No admin role is requested.
