"""Mail and Packages Integration."""

import asyncio
import logging
from functools import partial

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_RESOURCES,
    CONF_TOKEN,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)

from . import const
from .const import (
    ATTR_IMAGE_NAME,
    ATTR_IMAGE_PATH,
    AUTH_TYPE_PASSWORD,
    CONF_AMAZON_CUSTOM_IMG,
    CONF_AMAZON_CUSTOM_IMG_FILE,
    CONF_AMAZON_DAYS,
    CONF_AMAZON_DOMAIN,
    CONF_AMAZON_FWDS,
    CONF_AUTH_TYPE,
    CONF_FEDEX_CUSTOM_IMG,
    CONF_FEDEX_CUSTOM_IMG_FILE,
    CONF_FOLDER,
    CONF_FORWARDED_EMAILS,
    CONF_GENERIC_CUSTOM_IMG,
    CONF_GENERIC_CUSTOM_IMG_FILE,
    CONF_HOME_DEPOT_CUSTOM_IMG,
    CONF_HOME_DEPOT_CUSTOM_IMG_FILE,
    CONF_IMAGE_SECURITY,
    CONF_IMAP_SECURITY,
    CONF_IMAP_TIMEOUT,
    CONF_PATH,
    CONF_SCAN_INTERVAL,
    CONF_STORAGE,
    CONF_UPS_CUSTOM_IMG,
    CONF_UPS_CUSTOM_IMG_FILE,
    CONF_VERIFY_SSL,
    CONF_WALMART_CUSTOM_IMG,
    CONF_WALMART_CUSTOM_IMG_FILE,
    CONFIG_VER,
    DEFAULT_AMAZON_CUSTOM_IMG_FILE,
    DEFAULT_AMAZON_DAYS,
    DEFAULT_FEDEX_CUSTOM_IMG_FILE,
    DEFAULT_GENERIC_CUSTOM_IMG_FILE,
    DEFAULT_HOME_DEPOT_CUSTOM_IMG_FILE,
    DEFAULT_UPS_CUSTOM_IMG_FILE,
    DEFAULT_WALMART_CUSTOM_IMG_FILE,
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    VERSION,
)
from .coordinator import (
    MailAndPackagesConfigEntry,
    MailAndPackagesData,
    MailDataUpdateCoordinator,
)
from .migrate import async_migrate_entry
from .tracking import PackageRegistry
from .utils.image import default_image_path, hash_file

__all__ = [
    "ATTR_IMAGE_NAME",
    "ATTR_IMAGE_PATH",
    "AUTH_TYPE_PASSWORD",
    "CONFIG_VER",
    "CONF_AMAZON_CUSTOM_IMG",
    "CONF_AMAZON_CUSTOM_IMG_FILE",
    "CONF_AMAZON_DAYS",
    "CONF_AMAZON_DOMAIN",
    "CONF_AMAZON_FWDS",
    "CONF_AUTH_TYPE",
    "CONF_FEDEX_CUSTOM_IMG",
    "CONF_FEDEX_CUSTOM_IMG_FILE",
    "CONF_FOLDER",
    "CONF_FORWARDED_EMAILS",
    "CONF_GENERIC_CUSTOM_IMG",
    "CONF_GENERIC_CUSTOM_IMG_FILE",
    "CONF_HOME_DEPOT_CUSTOM_IMG",
    "CONF_HOME_DEPOT_CUSTOM_IMG_FILE",
    "CONF_IMAGE_SECURITY",
    "CONF_IMAP_SECURITY",
    "CONF_IMAP_TIMEOUT",
    "CONF_PATH",
    "CONF_SCAN_INTERVAL",
    "CONF_STORAGE",
    "CONF_UPS_CUSTOM_IMG",
    "CONF_UPS_CUSTOM_IMG_FILE",
    "CONF_VERIFY_SSL",
    "CONF_WALMART_CUSTOM_IMG",
    "CONF_WALMART_CUSTOM_IMG_FILE",
    "DEFAULT_AMAZON_CUSTOM_IMG_FILE",
    "DEFAULT_AMAZON_DAYS",
    "DEFAULT_FEDEX_CUSTOM_IMG_FILE",
    "DEFAULT_GENERIC_CUSTOM_IMG_FILE",
    "DEFAULT_HOME_DEPOT_CUSTOM_IMG_FILE",
    "DEFAULT_UPS_CUSTOM_IMG_FILE",
    "DEFAULT_WALMART_CUSTOM_IMG_FILE",
    "DOMAIN",
    "ISSUE_URL",
    "PLATFORMS",
    "VERSION",
    "MailAndPackagesConfigEntry",
    "MailAndPackagesData",
    "MailDataUpdateCoordinator",
    "async_migrate_entry",
    "const",
    "default_image_path",
    "hash_file",
]

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

OAUTH_TOKEN_KEYS = {
    CONF_TOKEN,
    CONF_ACCESS_TOKEN,
    "refresh_token",
    "expires_at",
    "expires_in",
    "auth_implementation",
}


async def async_setup(hass: HomeAssistant, config_entry: MailAndPackagesConfigEntry):  # pylint: disable=unused-argument
    """Set up integration-level services."""
    _register_registry_services(hass)
    return True


def _registry_coordinators(hass: HomeAssistant):
    """Yield coordinators that currently have the package registry enabled."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime_data, "coordinator", None)
        if coordinator is not None and coordinator.registry is not None:
            yield coordinator


async def _async_publish_registry(coordinator) -> None:
    """Persist and publish registry state without forcing an IMAP refresh."""
    await coordinator.registry.async_save()
    data = dict(coordinator.data or {})
    data.update(coordinator.registry.coordinator_data())
    coordinator.async_set_updated_data(data)


async def _handle_clear_package(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle clearing one package from all enabled registries."""
    tracking = call.data["tracking_number"]
    for coordinator in _registry_coordinators(hass):
        if coordinator.registry.clear_package(tracking):
            await _async_publish_registry(coordinator)


async def _handle_clear_all_delivered(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle clearing all delivered packages."""
    for coordinator in _registry_coordinators(hass):
        if coordinator.registry.clear_all_delivered():
            await _async_publish_registry(coordinator)


async def _handle_mark_delivered(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle manually marking one package delivered."""
    tracking = call.data["tracking_number"]
    for coordinator in _registry_coordinators(hass):
        registry = coordinator.registry
        normalized = registry.normalize_tracking_number(tracking)
        package = registry.packages.get(normalized)
        previous_status = package.get("status") if package else None
        if registry.mark_delivered(normalized):
            await _async_publish_registry(coordinator)
            hass.bus.async_fire(
                f"{DOMAIN}_package_delivered",
                {
                    "tracking_number": normalized,
                    "carrier": (package or {}).get("carrier", "unknown"),
                    "status": "delivered",
                    "previous_status": previous_status,
                    "source": "manual",
                },
            )


async def _handle_add_package(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Handle manually adding one package."""
    tracking = call.data["tracking_number"]
    carrier = call.data.get("carrier", "unknown")
    for coordinator in _registry_coordinators(hass):
        registry = coordinator.registry
        normalized = registry.normalize_tracking_number(tracking)
        if registry.add_package(normalized, carrier):
            await _async_publish_registry(coordinator)
            hass.bus.async_fire(
                f"{DOMAIN}_package_detected",
                {
                    "tracking_number": normalized,
                    "carrier": carrier.lower(),
                    "status": "detected",
                    "previous_status": None,
                    "source": "manual",
                },
            )


def _register_registry_services(hass: HomeAssistant) -> None:
    """Register package-registry management services once."""
    if hass.services.has_service(DOMAIN, "clear_package"):
        return

    tracking_schema = vol.Schema({vol.Required("tracking_number"): cv.string})
    add_schema = vol.Schema(
        {
            vol.Required("tracking_number"): cv.string,
            vol.Optional("carrier", default="unknown"): cv.string,
        }
    )

    hass.services.async_register(
        DOMAIN,
        "clear_package",
        partial(_handle_clear_package, hass),
        schema=tracking_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "clear_all_delivered",
        partial(_handle_clear_all_delivered, hass),
    )
    hass.services.async_register(
        DOMAIN,
        "mark_delivered",
        partial(_handle_mark_delivered, hass),
        schema=tracking_schema,
    )
    hass.services.async_register(
        DOMAIN,
        "add_package",
        partial(_handle_add_package, hass),
        schema=add_schema,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MailAndPackagesConfigEntry,
) -> bool:
    """Load the saved entities."""
    _LOGGER.info(
        "Version %s is starting, if you have any issues please report them here: %s",
        VERSION,
        ISSUE_URL,
    )
    # Merge data and options
    config = {**config_entry.data, **config_entry.options}

    # Sort the resources
    if CONF_RESOURCES in config:
        sorted_resources = sorted(config[CONF_RESOURCES])
        if sorted_resources != config[CONF_RESOURCES]:
            config[CONF_RESOURCES] = sorted_resources
            if CONF_RESOURCES in config_entry.options:
                hass.config_entries.async_update_entry(
                    config_entry,
                    options={**config_entry.options, CONF_RESOURCES: sorted_resources},
                )
            else:
                hass.config_entries.async_update_entry(
                    config_entry,
                    data={**config_entry.data, CONF_RESOURCES: sorted_resources},
                )

    # Setup the data coordinator
    coordinator = MailDataUpdateCoordinator(hass, config, config_entry)

    last_data = {
        k: v for k, v in config_entry.data.items() if k not in OAUTH_TOKEN_KEYS
    }
    config_entry.runtime_data = MailAndPackagesData(
        coordinator=coordinator,
        cameras=[],
        last_options=dict(config_entry.options),
        last_data=last_data,
    )

    # Fetch initial data in the background so setup doesn't block
    hass.async_create_task(coordinator.async_refresh())

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    config_entry.async_on_unload(config_entry.add_update_listener(update_listener))

    return True


async def update_listener(
    hass: HomeAssistant, config_entry: MailAndPackagesConfigEntry
) -> None:
    """Update listener."""
    if config_entry.runtime_data:
        current_non_oauth_data = {
            k: v for k, v in config_entry.data.items() if k not in OAUTH_TOKEN_KEYS
        }
        if (
            config_entry.options == config_entry.runtime_data.last_options
            and current_non_oauth_data == config_entry.runtime_data.last_data
        ):
            _LOGGER.debug("Config entry update was token-only refresh; skipping reload")
            return

    _LOGGER.debug("Attempting to reload sensors from the %s integration", DOMAIN)
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_entry(
    hass: HomeAssistant,
    config_entry: MailAndPackagesConfigEntry,
) -> None:
    """Remove package-registry storage when a config entry is deleted."""
    registry = PackageRegistry(hass, config_entry.entry_id)
    await registry.async_remove()


async def async_remove_config_entry_device(  # pylint: disable-next=unused-argument
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Remove config entry from a device if its no longer present."""
    return not any(
        identifier
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
        and config_entry.runtime_data.get_device(identifier[1])
    )


async def async_unload_entry(
    hass: HomeAssistant,
    config_entry: MailAndPackagesConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    _LOGGER.debug("Attempting to unload sensors from the %s integration", DOMAIN)

    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(config_entry, platform)
                for platform in PLATFORMS
            ],
        ),
    )

    if unload_ok:
        _LOGGER.debug("Successfully removed sensors from the %s integration", DOMAIN)

    return unload_ok
