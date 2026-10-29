# Changelog

This changelog documents fork-specific releases. For the original project history, see the upstream repository at https://github.com/moralmunky/Home-Assistant-Mail-And-Packages.

## 0.8.0b11 - 2026-10-29

### Security hardening

- Validate Amazon delivery-photo URLs against the two previously supported HTTPS S3 hosts, rejecting deceptive hosts, malformed URLs, unsafe ports and credential-bearing URLs. Enforce this validation before downloading and disallow HTTP redirects to avoid SSRF (PR #26).
- Generate safe local filenames for USPS JPEG attachments rather than trusting email attachment filenames. Skip known announcement attachments and empty/invalid payloads (PR #28).

### Amazon delivery tracking accuracy

- Recognize Amazon `Expected by`, `Now arriving`, and revised delivery dates so updated ETAs appear in merchant order records (PR #27).
- Normalize timezone-aware Amazon email dates to the local calendar day where applicable.
- Preserve an `action_required` / `approval_required` attention flag for Amazon approval notices without inventing a shipment state. Clear it after later shipping or delivery notifications.

### Delivery photos and Home Assistant compatibility

- Extract Walmart delivery photos encoded as either PNG or JPEG, including embedded and attached images, without changing other carrier formats (PR #29).
- Remove a deprecated Home Assistant config-flow connection-class assignment; the integration's existing manifest already specifies `iot_class` (PR #30).

### Update notes

- No automatic Home Assistant update, registry reset, dashboard change, script edit, or configuration migration is included. Existing merchant and package records are preserved.
- Previously processed universal-scanner messages will not automatically be replayed merely by installing this release. The opt-in historical rescan introduced in b10 preserves processed UID markers; do not reset them globally.
- Confirm Amazon order dates and delivery photos, Walmart photos, USPS mail images, and package/17TRACK registry lifecycle after upgrading. Back up Home Assistant first and retain the previous prerelease for rollback.

## 0.8.0b10 - 2026-10-28

### Safe historical rescan

- Add the opt-in Home Assistant action `mail_and_packages.rescan_registry` with a temporary 1–30-day email lookback (14 days by default).
- Keep the saved general and Amazon lookback settings unchanged after a manual scan.
- Retain previously processed universal-scanner UID markers, preserving existing deduplication and avoiding a wholesale reprocess; regular marketplace/carrier parsers can still inspect the longer search window.
- Serialize overlapping mail scans to protect the shared IMAP cache and registry, with regression coverage for OAuth, service validation, and scan locking.

### Marketplace physical carrier reconciliation

- Preserve temporary marketplace-to-carrier tracking mappings before dashboard deduplication removes them.
- Register recognizable physical carrier numbers from Shopify, Etsy, and Home Depot messages as packages instead of treating marketplace order references as physical tracking.
- Keep the corresponding marketplace reference in package source provenance; do not resurrect cleared or archived shipments or downgrade delivered packages.
- Add regression tests for storefront-reference filtering, real USPS/UPS tracking, duplicates, lifecycle preservation, and persistence.

### Notes

- Historical rescan does not force a reparse of universal-scanner messages whose UIDs are already recorded as processed.
- This prerelease preserves existing dashboards and regular 3-day mail lookback settings; verify behavior in Home Assistant after updating.

## 0.8.0b9 - 2026-10-13

### Carrier evidence and pickup-ready lifecycle

- Ignore Shopify/merchant-order parser prefixes as carrier tracking and reject Shopify-style `SH...-XX` order references.
- Safely move previously persisted Shopify order-reference false positives into the archive with a diagnostic reason, preventing re-detection while preserving history.
- Preserve real carrier tracking extracted from Shopify-style email and add regression cases for the observed USPS tracking format.
- Treat legitimate provider lifecycle updates as carrier confirmation, but never treat Not Found as confirmation.
- Add forward-only `ready_for_pickup` status and corresponding merchant-order aggregate states, with active/in-transit counts and provider synchronization.
- Expand regression coverage for false identifiers, pickup transitions, and merchant reconciliation.

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
