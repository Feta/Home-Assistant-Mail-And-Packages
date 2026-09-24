# Mail and Packages – Super Tracking Fork

![GitHub License](https://img.shields.io/github/license/Feta/Home-Assistant-Mail-And-Packages)
![CI](https://github.com/Feta/Home-Assistant-Mail-And-Packages/workflows/CI/badge.svg?branch=master)
![CodeQL](https://github.com/Feta/Home-Assistant-Mail-And-Packages/workflows/CodeQL/badge.svg?branch=master)
![GitHub last commit](https://img.shields.io/github/last-commit/Feta/Home-Assistant-Mail-And-Packages/master)

> **Fork notice:** This repository is a fork of [Mail and Packages](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages), originally created and maintained by [@moralmunky](https://github.com/moralmunky). It builds on the work of the original maintainers, [@firstof9](https://github.com/firstof9), and the wider upstream contributor community. The Super Tracking additions documented below are maintained separately in this fork and are not official upstream functionality.

## About this fork

The original **Mail and Packages** integration connects to your email account and creates Home Assistant sensors for mail and package activity. This fork preserves that functionality while adding an optional, persistent package-tracking pipeline designed to keep package history and tracking metadata useful beyond a single day's email scan.

### What this fork adds

- Persistent package registry across email scans and Home Assistant restarts
- Local universal tracking-number discovery from shipping email content
- Automatic forwarding of discovered tracking numbers to Home Assistant's official 17TRACK integration
- 17TRACK enrichment for status, location, latest event text, timestamps, origin/destination, and package type
- Amazon order and item metadata enrichment when shipping email can be correlated to a tracking number
- Amazon expected-delivery and delivery-state metadata
- Support for newer Amazon itemized subjects such as `Shipped 2 items: ...` and `Ordered 1 item: ...`
- Manual package lifecycle support for packages that cannot be discovered automatically
- No direct 17TRACK credentials stored by Mail and Packages; the fork uses Home Assistant's configured 17TRACK integration

## Original Mail and Packages features

The upstream integration provides:

- Per-shipper sensors for packages in transit, delivered, and exceptions
- USPS Informed Delivery mail-piece count and rotating GIF of today's mail images
- Camera entities per shipper with delivery images pulled from emails
- OAuth2 authentication support for Microsoft (Outlook/Exchange) and Google (Gmail)
- Email forwarding-service support through forwarding headers or per-carrier address lists
- LLM vision-grid image generation for AI-based automations
- Local IMAP/email processing inside Home Assistant

## How it works

Mail and Packages connects through IMAP to the mailbox receiving shipment notifications and evaluates supported shipping emails for delivery state, carrier information, order identifiers, and tracking numbers.

With Super Tracking enabled, the fork also maintains a persistent local registry. When a usable tracking number is discovered, it can be forwarded to Home Assistant's official 17TRACK integration. Tracking data returned by 17TRACK can then be merged back into the local registry so Home Assistant can retain richer per-package information than the original same-day counters alone.

Amazon shipping email can additionally enrich matching package records with order ID, item name, product image, expected-delivery date, and Amazon delivery state when those fields are available.

## Privacy and external services

Core email parsing and registry processing remain local to your Home Assistant instance.

If you enable the optional 17TRACK pipeline, discovered tracking numbers are sent through Home Assistant's configured 17TRACK integration to 17TRACK for tracking updates. That means those tracking numbers leave your local network as part of the tracking request.

This fork does **not**:

- Store direct 17TRACK credentials inside Mail and Packages
- Perform Amazon cookie scraping
- Require cloud LLM analysis for package discovery
- Delete or modify your shipping emails

Files stored in Home Assistant's `www` folder are publicly accessible through Home Assistant's HTTP server by default. The original integration randomizes image filenames to reduce exposure.

## Install this fork with HACS

This fork is installed as a **custom HACS repository**. It is not the HACS default-store entry for Mail and Packages.

If the upstream Mail and Packages repository is already installed through HACS, uninstall that HACS repository download first **without deleting the Home Assistant Mail and Packages integration/config entry**.

Then:

1. Open **HACS**.
2. Open **Custom repositories**.
3. Add `https://github.com/Feta/Home-Assistant-Mail-And-Packages`.
4. Select **Integration** as the repository type.
5. Install **Mail and Packages Super Tracking (Beta)**.
6. Restart Home Assistant.

The integration domain remains `mail_and_packages`, so this fork replaces the code used by the existing Mail and Packages config entry rather than installing a second integration alongside it.

### Manual install

Copy `custom_components/mail_and_packages` into your Home Assistant `custom_components` directory and restart Home Assistant.

## Want the original upstream project?

If you do not need the Super Tracking additions, use the official upstream project:

- [moralmunky/Home-Assistant-Mail-And-Packages](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages)
- [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki)

## Support and documentation

The original project's wiki remains the best reference for the base integration:

| Resource | Link |
|---|---|
| Configuration & email settings | [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Configuration-and-Email-Settings) |
| Supported shippers & requirements | [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Supported-Shipper-Requirements) |
| Troubleshooting | [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Troubleshooting) |
| USPS Informed Delivery image | [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/USPS-Informed-Delivery-Image) |
| Text summary templates | [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Mail-Summary-Message) |
| Notification examples | [Upstream wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Notifications) |

Fork-specific behavior is documented in this repository and its release notes as the Super Tracking work continues to stabilize.

## Credits

**Mail and Packages was originally created by [@moralmunky](https://github.com/moralmunky).** This fork would not exist without the original project, its maintainers, and its contributor community.

Major upstream contributions and ongoing project work have also come from [@firstof9](https://github.com/firstof9) and many other contributors to the upstream repository.

Recent upstream fixes are reviewed and selectively ported into this fork when they are compatible with the Super Tracking changes. For example, the Amazon itemized-subject fix from upstream PR [#1466](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/pull/1466) has been incorporated here.

If you want to support the original project creator:

<a href="https://www.buymeacoffee.com/Moralmunky" target="_blank"><img src="/docs/coffee.png" alt="Buy Moralmunky A Coffee" height="51px" width="217px" /></a>

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and [DEVCONTAINER.md](DEVCONTAINER.md) for the existing development setup.

For this fork, development work should target `master` through a short-lived feature/fix branch. Upstream changes should be reviewed selectively before being ported so the fork's persistent tracking behavior remains stable.
