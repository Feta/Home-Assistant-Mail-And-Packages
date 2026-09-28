# Changelog

This changelog documents fork-specific releases. For the original project history, see the upstream repository at https://github.com/moralmunky/Home-Assistant-Mail-And-Packages.

## 0.8.0b4 - 2026-09-28

### Fixes

- Preserve shipped/out-for-delivery/delivered merchant-order status when an older confirmation email is rescanned.
- Force a one-time recent-mail rescan so corrected Walmart item counts can update already-attached orders without losing carrier/tracking metadata.

## 0.8.0b3 - 2026-09-28

### Fixes

- Fixed Walmart item counts when multipart order emails repeat per-item text before the order summary.
- Fixed `pending_orders` so shipped merchant orders without a linked tracking number are not misclassified as still awaiting tracking.

## 0.8.0b2 - 2026-09-28

### Super Tracking

- Added a persistent package registry that survives Home Assistant restarts.
- Added local universal tracking-number discovery for supported carrier formats.
- Added optional forwarding to Home Assistant's official 17TRACK integration.
- Added 17TRACK status, location, latest-event, timestamp, origin/destination, and package-type enrichment.
- Added a 48-hour grace period before a newly detected package with provider status `Not Found` is treated as an exception.
- Added manual package lifecycle services for adding, clearing, and marking packages delivered.

### Merchant orders

- Added Amazon order/item metadata persistence and package correlation.
- Added support for newer Amazon itemized `Shipped` and `Ordered` email subjects.
- Added Walmart pending-order discovery before carrier tracking is available.
- Added `mail_and_packages.attach_tracking` to attach a manually retrieved carrier number to an already-known merchant order.

### Repository and HACS

- Clarified that this repository is a separately maintained fork of the original Mail and Packages project.
- Added explicit credit to @moralmunky, @firstof9, and the upstream contributor community.
- Clarified privacy behavior when optional 17TRACK forwarding is enabled.
- Updated custom HACS installation documentation for the Super Tracking beta.

## Upstream

Upstream fixes are reviewed selectively and ported when compatible with the fork. The original project and wiki remain available at:

- https://github.com/moralmunky/Home-Assistant-Mail-And-Packages
- https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki
