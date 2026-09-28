{% if prerelease %}
### Super Tracking beta

This is a pre-release build of the Super Tracking fork. It may contain bugs or behavior that changes between beta releases. Please report fork-specific problems in the [fork issue tracker](https://github.com/Feta/Home-Assistant-Mail-And-Packages/issues).

{% endif %}

# Mail and Packages – Super Tracking Fork

This repository is a fork of [Mail and Packages](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages), originally created by [@moralmunky](https://github.com/moralmunky), with major upstream contributions from [@firstof9](https://github.com/firstof9) and the wider upstream community.

The fork preserves the original integration while adding a persistent package-tracking pipeline for Home Assistant.

## Super Tracking additions

- Persistent package registry across scans and restarts
- Local universal tracking-number discovery
- Optional forwarding to Home Assistant's official 17TRACK integration
- 17TRACK status, location, event, and carrier enrichment
- Amazon order/item metadata correlation
- Walmart pending-order discovery before a carrier number is known
- Manual attachment of a carrier tracking number to a known merchant order
- Grace handling for newly created labels that 17TRACK temporarily reports as Not Found
- Manual package lifecycle services

## Privacy

Email parsing and package-registry processing happen locally in Home Assistant.

When 17TRACK forwarding is enabled, tracking numbers are sent through Home Assistant's configured 17TRACK integration so carrier status can be retrieved. Mail and Packages does not store separate 17TRACK credentials.

The fork does not require cloud LLM analysis, Amazon cookie scraping, or Walmart account credentials.

## Documentation

The original project's wiki remains the best reference for base Mail and Packages configuration and shipper behavior:

- [Configuration and email settings](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Configuration-and-Email-Settings)
- [Supported shippers](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Supported-Shipper-Requirements)
- [Troubleshooting](https://github.com/moralmunky/Home-Assistant-Mail-And-Packages/wiki/Troubleshooting)

Fork-specific behavior and installation notes are maintained in the [fork README](https://github.com/Feta/Home-Assistant-Mail-And-Packages#readme).

## Credits

Mail and Packages was originally created by [@moralmunky](https://github.com/moralmunky). This fork builds on the work of the original maintainers and contributors.

<a href="https://www.buymeacoffee.com/Moralmunky" target="_blank"><img src="/docs/coffee.png" alt="Buy Moralmunky A Coffee" height="51px" width="217px" /></a>
