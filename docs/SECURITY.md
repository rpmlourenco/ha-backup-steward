# Security and operational boundaries

This app manages physical disks and therefore is privileged. Protection mode must be disabled for `full_access`. Host D-Bus can perform privileged systemd operations; the app's code restricts itself to named mount/lease units, but the container itself is not a security boundary against a compromise of its own privileged process.

- No Docker socket, host PID namespace, SSH key or host-installed helper is needed.
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
