"""Tracking-provider forwarding helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .registry import PackageRegistry

_LOGGER = logging.getLogger(__name__)

SEVENTEENTRACK_DOMAIN = "seventeentrack"
SEVENTEENTRACK_ADD_SERVICE = "add_package"
SEVENTEENTRACK_GET_SERVICE = "get_packages"
SEVENTEENTRACK_PROVIDER = "seventeentrack"


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    """Snapshot of packages returned by the Home Assistant 17TRACK service."""

    config_entry_id: str | None = None
    service_available: bool = True
    packages: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ForwardingResult:
    """Summary of one forwarding pass."""

    config_entry_id: str | None = None
    service_available: bool = True
    pending: int = 0
    forwarded: int = 0
    existing_remote: int = 0
    failed: int = 0


def resolve_seventeentrack_config_entry(
    hass: HomeAssistant,
    configured_entry_id: str | None,
) -> str | None:
    """Resolve the configured 17TRACK entry, or auto-select a single entry."""
    if configured_entry_id:
        entry = hass.config_entries.async_get_entry(configured_entry_id)
        if entry is not None and entry.domain == SEVENTEENTRACK_DOMAIN:
            return entry.entry_id
        return None

    entries = hass.config_entries.async_entries(SEVENTEENTRACK_DOMAIN)
    if len(entries) == 1:
        return entries[0].entry_id
    return None


def _friendly_name(tracking_number: str, package: dict[str, Any]) -> str:
    """Build a minimal friendly name without sending email content to 17TRACK."""
    carrier = str(package.get("carrier", "package")).replace("_", " ").strip()
    if not carrier or carrier.lower() == "unknown":
        carrier = "Package"
    else:
        carrier = carrier.title()

    suffix = tracking_number[-6:] if len(tracking_number) > 6 else tracking_number
    return f"{carrier} package {suffix}"


async def async_get_seventeentrack_snapshot(
    hass: HomeAssistant,
    configured_entry_id: str | None = None,
) -> ProviderSnapshot:
    """Fetch one validated package snapshot from Home Assistant 17TRACK."""
    if not hass.services.has_service(
        SEVENTEENTRACK_DOMAIN,
        SEVENTEENTRACK_GET_SERVICE,
    ):
        return ProviderSnapshot(service_available=False)

    config_entry_id = resolve_seventeentrack_config_entry(
        hass,
        configured_entry_id,
    )
    if config_entry_id is None:
        return ProviderSnapshot(service_available=False)

    try:
        response = await hass.services.async_call(
            SEVENTEENTRACK_DOMAIN,
            SEVENTEENTRACK_GET_SERVICE,
            {"config_entry_id": config_entry_id},
            blocking=True,
            return_response=True,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Unable to query 17TRACK package data (%s)",
            type(err).__name__,
        )
        return ProviderSnapshot(
            config_entry_id=config_entry_id,
            service_available=False,
        )

    if not isinstance(response, dict):
        return ProviderSnapshot(
            config_entry_id=config_entry_id,
            service_available=False,
        )

    packages = response.get("packages", [])
    if not isinstance(packages, list):
        packages = []

    valid_packages = tuple(
        package for package in packages if isinstance(package, dict)
    )
    return ProviderSnapshot(
        config_entry_id=config_entry_id,
        packages=valid_packages,
    )


async def async_forward_pending_to_seventeentrack(
    hass: HomeAssistant,
    registry: PackageRegistry,
    configured_entry_id: str | None = None,
    snapshot: ProviderSnapshot | None = None,
) -> ForwardingResult:
    """Forward unsubmitted active registry packages to Home Assistant 17TRACK."""
    if not hass.services.has_service(
        SEVENTEENTRACK_DOMAIN,
        SEVENTEENTRACK_ADD_SERVICE,
    ):
        return ForwardingResult(service_available=False)

    provider_snapshot = snapshot or await async_get_seventeentrack_snapshot(
        hass,
        configured_entry_id,
    )
    config_entry_id = provider_snapshot.config_entry_id
    if not provider_snapshot.service_available or config_entry_id is None:
        return ForwardingResult(service_available=False)

    candidates = registry.get_forward_candidates(
        SEVENTEENTRACK_PROVIDER,
        config_entry_id,
    )
    if not candidates:
        return ForwardingResult(config_entry_id=config_entry_id)

    existing_remote = {
        registry.normalize_tracking_number(package["tracking_number"])
        for package in provider_snapshot.packages
        if package.get("tracking_number")
    }

    forwarded = 0
    already_remote = 0
    failed = 0

    for tracking_number, package in candidates:
        if tracking_number in existing_remote:
            if registry.mark_forwarded(
                tracking_number,
                SEVENTEENTRACK_PROVIDER,
                config_entry_id,
                existing_remote=True,
            ):
                already_remote += 1
            continue

        service_data = {
            "config_entry_id": config_entry_id,
            "package_tracking_number": tracking_number,
            "package_friendly_name": _friendly_name(tracking_number, package),
        }
        try:
            await hass.services.async_call(
                SEVENTEENTRACK_DOMAIN,
                SEVENTEENTRACK_ADD_SERVICE,
                service_data,
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            failed += 1
            _LOGGER.debug(
                "17TRACK rejected or failed to add one package (%s); "
                "it will be retried on a later scan",
                type(err).__name__,
            )
            continue

        if registry.mark_forwarded(
            tracking_number,
            SEVENTEENTRACK_PROVIDER,
            config_entry_id,
        ):
            forwarded += 1

    return ForwardingResult(
        config_entry_id=config_entry_id,
        pending=len(candidates),
        forwarded=forwarded,
        existing_remote=already_remote,
        failed=failed,
    )
