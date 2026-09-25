---
name: netapp
description: NetApp ONTAP volume provisioning, export policies, CIFS/NFS shares, and naming conventions.
always_on: false
---

Volume names follow the pattern `<env>_<app>_<purpose>` (ONTAP volume names use underscores, not hyphens — hyphens aren't allowed in ONTAP volume names).

Export policies control NFS client access per volume; always confirm the exact policy name and target volume before modifying one (see team-safety-rules). CIFS shares are managed separately from NFS export policies even when pointing at the same volume.

For ONTAP CLI/API command syntax, see `ontap-cli-reference.md` (via `read_skill_file`).
