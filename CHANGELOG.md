# Changelog

This changelog documents fork-specific releases. For the original project history, see the upstream repository at https://github.com/moralmunky/Home-Assistant-Mail-And-Packages.

## 0.8.0b8 - 2026-09-30

### Linked package/order self-healing

- Reconcile every active package's current lifecycle back into its linked merchant shipment during normal registry refreshes.
- Re-run linked-order reconciliation immediately after 17TRACK/provider enrichment so provider-driven lifecycle changes cannot leave merchant shipment metadata stale.
- Repair persisted split-order drift automatically after restart or upgrade without requiring the original merchant email to be processed again.
- Add regression coverage for both normal registry refresh and tracking-provider refresh paths.

## 0.8.0b7 - 2026-09-30

### Lifecycle and order reconciliation

- Distinguish partial multi-shipment states from whole-order states so one out-for-delivery or delivered shipment no longer overstates the parent order.
- Archive delivered and manually cleared package records after their retention window instead of deleting them, preserving package history and automatic deduplication.
- Allow an archived merchant order to reopen only when a later update is meaningfully newer, such as a newly discovered tracking number or more advanced status.
- Mark merchant orders overdue when their expected-delivery date has passed without inventing a delivered or lost outcome.

### Tracking diagnostics

- Track carrier-label activation checks with first-seen, last-checked, check-count, and activation/timed-out state.
- Retain unique package source provenance, including RFC822 Message-ID when available, so multiple related emails enrich one package record.
- Add Mail Registry Archived and Mail Registry Health sensors with diagnostic counts for active, pending, archived, overdue, activation-waiting, and exception records.
- Remove the redundant Pillow manifest requirement now that Home Assistant provides it directly.

## 0.8.0b6 - 2026-09-30

### Tracking accuracy

- Require explicit UPS evidence near a `1Z...` value or in the sender/subject before accepting it as a UPS package; generic words such as `package` or `delivery` are no longer sufficient.
- Add regression coverage for the real USPS Informed Delivery plaintext URL-token failure mode that resurrected a bogus UPS package after the b5 rescan.
- Bump the universal scanner UID generation so recent shipping mail is reparsed with the stricter UPS rule.

## 0.8.0b5 - 2026-09-30

### Live mail parsing

- Treat trusted carrier-authored status emails as lifecycle evidence so direct FedEx/UPS/USPS/DHL updates can advance packages even when 17TRACK is stale or reports `Not Found`.
- Suppress stale provider `Not Found` metadata after a carrier-confirmed package has advanced to in transit, out for delivery, or delivered.
- Require UPS/shipping context before accepting `1Z...` values, preventing tracking-shaped tokens inside unrelated URLs from becoming packages.
- Parse Walmart split shipments into per-part tracking records with their own expected-delivery dates, item counts, and part numbers while retaining one aggregate merchant order.
- Keep merchant-order and shipment lifecycle states monotonic when older emails are rescanned.
- Enrich Amazon shipment records with item counts and common relative arrival dates such as `Arriving Thursday`.
- Bump the universal scanner UID generation so recent shipping mail is reparsed with the corrected logic.

## 0.8.0b4 - 2026-09-28

### Fixes

- Preserve shipped/out-for-delivery/delivered merchant-order status when an older confirmation email is rescanned.
- Force a one-time recent-mail rescan so corrected Walmart item counts can update already-attached orders without losing carrier/tracking metadata.
- Reject FedEx-like purchase-order, invoice, reference, customer, and account identifiers when they are explicitly labeled as non-tracking values.

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
