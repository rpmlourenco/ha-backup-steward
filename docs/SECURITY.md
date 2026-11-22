# Security and operational boundaries

This app manages physical disks and therefore is privileged. Protection mode must be disabled for `full_access`. Host D-Bus can perform privileged systemd operations; the app's code restricts itself to named mount/lease units, but the container itself is not a security boundary against a compromise of its own privileged process.

- Local L1-L3 require no Docker socket, host PID namespace, host SSH key or host-installed helper. Optional L4 uses its own outbound SSH keys only.
- Kernel disk removal uses a temporary host systemd unit with a fixed executable, fixed stdin and a validated kernel-device path. The app does not remount sysfs or request additional container privileges. The owned backup lease is required; the final SCSI proof remains mandatory.
- The Supervisor injects its app token. Never enter, print or commit it.
- The integration uses a separate random pairing token scoped to this app API.
- Status/configuration responses do not contain encryption passwords or tokens. Pairing-token reveal is a deliberate ingress-only action.
- State-changing ingress requests require a non-simple custom header; no CORS bypass is provided.
- Native creation always requests encryption. No implicit fallback to an unencrypted backup exists.
- Configuration and identity are mode 0600 and stored under private app data. They are included in the encrypted app backup. Keep an external copy of the recovery key.
- No remote JS, fonts, analytics or dynamic code installation is performed at app runtime.
- Source/destination policies accept confined local `/media` roots, not arbitrary remote URLs or shell arguments.
- On an unknown state the app leaves the disk powered and blocks new jobs. Do not disconnect power as an alternative to recovery.

The ingress peer override `STEWARD_INGRESS_IP` is for controlled container/UI testing only; it is not an app option. Do not expose ingress port 8099 directly. If exposing the authenticated 8100 API outside the HA container network, provide appropriate transport security; the default exposes no host port.

Report defects through GitHub issues without posting tokens, passwords, complete app data, authenticated URLs or unredacted logs. The project does not automatically upload diagnostics.

Supervisor API role: `manager` is required for read-only background-job polling (`/jobs/<uuid>`), which the `backup` role does not permit. Manager also grants other Supervisor management endpoints, although the adapter only implements the backup/polling/Core calls documented here. Review this permission when updating from 0.1.1. It is not an `admin` token and is never exported to the integration or logs.

## L3 restore boundary

Restore is a two-step authenticated operation: `/v1/restore/preview` accepts exact configured areas and returns a short-lived, single-use preparation token; `/v1/restore/start` requires that token, the exact list of nonempty areas explicitly authorized for deletion and a boolean checksum choice. Tokens expire on app restart, after ten minutes, after consumption, or on configuration change. Ordinary `/v1/run` and schedules cannot start restores.

The backup disk is mounted read-only. Every selected mirror, original root identity, nested mount boundary and space estimate is checked before the first deletion. Cleanup retains each selected root and unlinks children using directory file descriptors without following links. Transfers go from `mirror/<area>` to that exact configured original area with `--inplace`; they never use sync, history expiry or L2 creation. Other areas and the backup mirror are not deletion targets. Applications using selected original areas must be stopped by the operator; concurrent writes are not a supported restore mode.

Missing ordinary restore areas are planned without filesystem writes during preview/start. The nearest existing parent is pinned by device/inode; all selected mirrors and space estimates must pass before directories are created through no-follow directory file descriptors. An area that appears after preparation requires a new confirmation. The primary source mount remains mandatory and is never replaced with a directory on underlying storage.

## L4 remote boundary

L4 is opt-in and production-only. Its endpoints exist only on ingress, with the
same peer and custom-header protections as settings. Remote destinations have
validated host/port fields, a pinned Ed25519 server key and an expected filesystem
UUID; arbitrary URLs and SSH options are not accepted. Sources must be exact
configured local roots with their own verified ext4 UUIDs.

Per-source private keys stay in the app's private data directory with restricted
permissions and are never returned by an API. The pairing endpoint exports only
public keys. Raw SSH/rsync stderr is bounded in memory and mapped to fixed error
messages; keys, argv and filenames are never emitted as remote diagnostics.

Remote runs share the local execution lock, preserve local backup/checksum dates,
and never acquire or power HAL3. Disabling L4 cancels and awaits the local SSH/rsync
process group. Source identity is checked again immediately before transfer.
Deletion planning, runtime deletion limits and free-space guards constrain normal
mirror operation; the mirror is not append-only or immutable storage. A compromised
source key can modify its authorized mirror area, so the independent offline L3
remains important. The dedicated receiver uses unprivileged per-source accounts,
forced upstream rrsync validation, disabled shell/forwarding and neutralized links.
