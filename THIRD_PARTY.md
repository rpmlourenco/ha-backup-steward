# Third-party components

- Rclone 1.75.1 is copied from the upstream `rclone/rclone:1.75.1` container image. Rclone is MIT licensed: <https://github.com/rclone/rclone/blob/master/COPYING>.
- Python and Debian runtime packages retain their upstream licenses and notices in the container.
- Python libraries are listed in `backup_steward/requirements.txt`; their installed distribution metadata includes upstream licenses.
- The app calls the public Supervisor API. It does not vendor or depend on the Auto Backup integration. The previous local patch informed the delivery-before-retention invariant; no Auto Backup source is included in this repository.
- The SVG icon and interface assets are authored for this project and distributed under the repository license.
