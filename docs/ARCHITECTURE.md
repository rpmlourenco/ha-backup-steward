# Architecture

One repository contains two installable packages: a Supervisor app (`backup_steward/`) and a Core integration (`custom_components/backup_steward/`). The app independently owns calendar, state, secrets, backup creation, file operations and physical disk lifecycle. The integration only consumes the authenticated API and provides manual actions/entities.

## Modules

| Module | Responsibility |
| --- | --- |
| `config.py` | Schema, confined policy, safe defaults and public/secret separation |
| `schedule.py` | Daily local calendar, monthly/weekly checksum deadline and catch-up |
| `state.py` | Atomic durable state and private installation identity |
| `supervisor.py` | Native backup jobs, Core states/power and streamed delivery |
| `disks.py` | Host systemd D-Bus, shared legacy lock, UUID mounts, final SCSI proof |
| `storage.py` | Confined files, archive identity/retention, rclone copy/check/history |
| `engine.py` | Serialized workflow and recovery, policy snapshot per execution |
| `server.py` | Supervisor-ingress UI and separately authenticated integration API |

Multiple testable modules are intentionally packaged together. Minimizing loose host artifacts does not require putting the entire implementation in one source file.

## Native backup adapter instead of an Auto Backup fork

The current patch solves ordering: backup delivery is awaited before success or expiry. That is an engine invariant here, not a patch to another installation. `POST /backups/new/partial` includes all apps and explicitly selected non-media folders. The job ID is recorded before polling. The archive is correlated by an unpredictable per-run name, validated for encryption/compression, downloaded, checked and flushed before native deletion or retention.

Native backup job errors/timeout never imply that it is safe to remove the destination; an uncertain job keeps the host lease and power on. No Core private backup-manager objects or exported Supervisor tokens are used.

## Host boundary

The app talks to `org.freedesktop.systemd1` over the host system bus. It creates a transient mount unit for the offline target and a transient lease service made only from the stock `flock`/`sleep` executables. No custom executable or permanent service file is copied to HAOS. The lease holds `/run/lock/ha-level3-hdd.lock` for compatibility with the established L3 worker. On interruption it can only be adopted by the same stored installation identity during explicit recovery.

The app reads the host journal to require the same final SCSI flush evidence as the existing helper. Only a dedicated ext4 destination disk with one partition is supported for physical removal. Source automount is still the existing udev rule. No source mounting operation exists.

Journal discovery explicitly selects `/var/log/journal` or `/run/log/journal`, rather than the container's machine ID. A readable kernel cursor is required before power-on, and the selected directory stays fixed for the removal cursor and subsequent kernel-event checks. Empty or unreadable mappings block disk operations.

**HAOS hardware acceptance is a release gate:** D-Bus policy, media mount propagation, block-device visibility and journal visibility must all be proven on the actual host before enabling production. A successful Docker build cannot prove these properties.

## Execution invariants

1. Observation never triggers a service, mounts a disk or modifies backup data.
2. One execution at a time. Existing powered/mounted destinations are not adopted by a normal run.
3. Exact source/destination mounts and UUIDs are checked before copying/pruning.
4. All L2 retention candidates are validated before deletion. Symlinks, hardlinks, corrupt archives and ambiguous newest identities block pruning.
5. In test mode, old production archives/history remain untouched.
6. Full checksum failure blocks L3 history pruning and does not advance the successful checksum deadline.
7. Power-off requires safe removal proof. If a device reappears after that proof, power-off is blocked.
8. Restoration uses native backup archives; no proprietary restore format is introduced.

## API

The app emits concise timestamped lifecycle and workflow events to stdout for the Supervisor Log tab. Startup distinguishes a ready scheduler from an enabled schedule. Errors identify the failing phase without logging credentials or raw API bodies. Persistent `needs_cleanup` records disk ownership during a run; public `recovery_required` is true only when cleanup is pending and no job is busy.

Port 8099 serves the UI only to the configured Supervisor ingress peer, default `172.30.32.2`. State-changing browser requests require a custom header; there is no permissive CORS policy. Port 8100 is not exposed on the host by default; it authenticates every request using a random per-installation pairing key.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/status` | Bounded public state, policy summary and next run |
| `POST /v1/run` | `backup`, `backup_checksum` or `checksum`; returns 202 |
| `POST /v1/recover` | Start serialized recovery |
| `GET/PUT /v1/config` | Ingress only, secrets omitted from reads |
| `POST /v1/pair` | Ingress only, explicit pairing-key reveal |

The API has no arbitrary shell, filesystem path, restore, reboot, format or service-call endpoint. Integration HTTP redirects are rejected so its authorization header cannot follow a redirect to another host.
