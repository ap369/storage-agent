---
name: purestorage
description: PureStorage FlashArray/FlashBlade provisioning, naming conventions, and REST API usage.
always_on: false
---

Volume names follow the pattern `<env>-<app>-<purpose>-<size>` (e.g. `prod-erp-data-500g`).

For FlashArray REST API calls, use the `purestorage_*` tools if configured in the REST allowlist. See `rest-api-cheatsheet.md` (via `read_skill_file`) for endpoint details.
