![GitHub License](https://img.shields.io/github/license/moralmunky/Home-Assistant-Mail-And-Packages)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/moralmunky/Home-Assistant-Mail-And-Packages)
![CI](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/workflows/CI/badge.svg?branch=dev)
![CodeQL](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/workflows/CodeQL/badge.svg?branch=dev)
![Codecov branch](https://img.shields.io/codecov/c/github/moralmunky/Home-Assistant-Mail-And-Packages/dev)

![GitHub contributors](https://img.shields.io/github/contributors/moralmunky/Home-Assistant-Mail-And-Packages)
![GitHub commit activity](https://img.shields.io/github/commit-activity/y/moralmunky/Home-Assistant-Mail-And-Packages)
![GitHub last commit](https://img.shields.io/github/last-commit/moralmunky/Home-Assistant-Mail-And-Packages/dev)

## About Mail and Packages

The [Mail and Packages integration](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages) connects to your email account and creates sensors tracking mail and packages scheduled for delivery **today**. It counts in-transit and delivered packages per shipper, generates a rotating GIF of USPS Informed Delivery mail images, and keeps everything completely local — no external services involved.

## Credits

Huge contributions from [@firstof9](https://github.com/firstof9) moving the project forward and keeping it active!

<a href="https://www.buymeacoffee.com/Moralmunky" target="_blank"><img src="/docs/coffee.png" alt="Buy Us A Coffee" height="51px" width="217px" /></a>

## Features

- Per-shipper sensors for packages in transit, delivered, and exceptions
- USPS Informed Delivery — mail piece count and rotating GIF of today's mail images
- Camera entities per shipper with delivery images pulled directly from emails
- OAuth2 authentication support for Microsoft (Outlook/Exchange) and Google (Gmail)
- Email forwarding service support — match carrier emails via a forwarding header (e.g. SimpleLogin) or a per-carrier address list
- LLM vision grid image generation for use with AI-based automations
- All processing done locally on your Home Assistant instance — no data leaves your network

## How it works

From your Home Assistant instance, the integration connects via IMAP to the email account where your shipment notifications are sent. It checks the subject lines of today's emails from [supported shippers](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Supported-Shipper-Requirements) against known delivery status language and counts matches. For USPS Informed Delivery emails it also downloads the mail piece images and combines them into a rotating GIF.

See the [wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki) for full details.

> **Note:** The integration does not delete or modify your emails. Do not delete delivery emails on the day they arrive, as the integration needs to find them during its scans. You can filter shipping notifications into a dedicated folder and point the integration at that folder. Delivery images revert to the no-mail placeholder after the first scan past midnight, local time.

> **Privacy / Security:** All processing is done locally. No data is sent outside your Home Assistant instance. Files stored in the `www` folder are [publicly accessible](https://www.home-assistant.io/integrations/http/#hosting-files) by default — image filenames are randomised to reduce exposure. Two sensors are available for use in notifications:
> - `sensor.mail_image_system_path`
> - `sensor.mail_image_url` *(requires `External_URL` or `Internal_URL` to be set in HA general settings)*

## Super Tracking Beta

This fork contains an experimental, opt-in package tracking pipeline built on the current upstream `dev` codebase. It adds a persistent package registry, forwarding through Home Assistant's official 17TRACK integration, and a local universal tracking-number scanner.

The beta does not add cloud LLM email analysis, Amazon cookie scraping, or direct 17TRACK credentials. Gmail OAuth and the current upstream IMAP implementation remain intact.

### Install this beta with HACS

If the upstream **Mail and Packages** repository is already installed through HACS, uninstall the HACS repository download first **without deleting the Home Assistant Mail and Packages integration/config entry**.

Then in HACS:

1. Open **Custom repositories**.
2. Add `https://github.com/Feta/Home-Assistant-Mail-And-Packages`.
3. Select **Integration** as the repository type.
4. Install **Mail and Packages Super Tracking (Beta)**.
5. Restart Home Assistant.

The integration domain remains `mail_and_packages`, so this beta replaces the code used by the existing Mail and Packages config entry rather than installing a second copy.

## Installation

### HACS (recommended)

Mail and Packages is available in the [HACS](https://hacs.xyz) default store. Search for **Mail and Packages** and install from there.

### Manual

Copy the `custom_components/mail_and_packages` folder into your Home Assistant `custom_components` directory and restart.

## Support & Documentation

| Resource | Link |
|---|---|
| Configuration & email settings | [Wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Configuration-and-Email-Settings) |
| Supported shippers & requirements | [Wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Supported-Shipper-Requirements) |
| Troubleshooting | [Wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Troubleshooting) |
| USPS Informed Delivery image | [Wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/USPS-Informed-Delivery-Image) |
| Text summary templates | [Wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Mail-Summary-Message) |
| Notification examples | [Wiki](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Notifications) |

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and [DEVCONTAINER.md](DEVCONTAINER.md) for setting up a local development environment. All PRs and releases are based on the `dev` branch.
