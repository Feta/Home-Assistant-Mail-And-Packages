"""Tests for tracking-provider forwarding."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.mail_and_packages.tracking.forwarding import (
    SEVENTEENTRACK_ADD_SERVICE,
    SEVENTEENTRACK_DOMAIN,
    SEVENTEENTRACK_GET_SERVICE,
    async_forward_pending_to_seventeentrack,
    resolve_seventeentrack_config_entry,
)
from custom_components.mail_and_packages.tracking.registry import PackageRegistry


@pytest.fixture
def registry():
    """Create an in-memory package registry."""
    with patch(
        "custom_components.mail_and_packages.tracking.registry.Store"
    ):
        yield PackageRegistry(MagicMock(), "entry")


def _hass_with_entry() -> tuple[MagicMock, MagicMock]:
    """Return a Home Assistant mock with one 17TRACK config entry."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "17track-entry"
    entry.domain = SEVENTEENTRACK_DOMAIN
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]
    return hass, entry


def test_resolve_explicit_seventeentrack_entry():
    """Configured 17TRACK entry should be used when it is valid."""
    hass, entry = _hass_with_entry()

    assert (
        resolve_seventeentrack_config_entry(hass, entry.entry_id)
        == entry.entry_id
    )


def test_resolve_single_seventeentrack_entry():
    """A single 17TRACK entry should be auto-selected."""
    hass, entry = _hass_with_entry()

    assert resolve_seventeentrack_config_entry(hass, None) == entry.entry_id


def test_resolve_ambiguous_seventeentrack_entries():
    """Multiple 17TRACK entries should require an explicit selection."""
    hass, entry = _hass_with_entry()
    second = MagicMock()
    second.entry_id = "17track-entry-2"
    second.domain = SEVENTEENTRACK_DOMAIN
    hass.config_entries.async_entries.return_value = [entry, second]

    assert resolve_seventeentrack_config_entry(hass, None) is None


@pytest.mark.asyncio
async def test_forwarding_unavailable_without_service(registry):
    """Forwarding should safely defer when the 17TRACK service is unavailable."""
    registry.register_package("1Z123", "ups", "in_transit")
    hass, _ = _hass_with_entry()
    hass.services.has_service.return_value = False

    result = await async_forward_pending_to_seventeentrack(hass, registry)

    assert result.service_available is False
    assert registry.is_forwarded("1Z123", SEVENTEENTRACK_DOMAIN) is False
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_forwarding_adds_new_package(registry):
    """A new active registry package should be handed to 17TRACK once."""
    registry.register_package("1Z123456789", "ups", "in_transit")
    hass, entry = _hass_with_entry()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock(
        side_effect=[
            {"packages": []},
            None,
        ]
    )

    result = await async_forward_pending_to_seventeentrack(
        hass,
        registry,
        entry.entry_id,
    )

    assert result.forwarded == 1
    assert result.failed == 0
    assert registry.is_forwarded(
        "1Z123456789",
        SEVENTEENTRACK_DOMAIN,
        entry.entry_id,
    )
    assert hass.services.async_call.await_count == 2
    add_call = hass.services.async_call.await_args_list[1]
    assert add_call.args[0:2] == (
        SEVENTEENTRACK_DOMAIN,
        SEVENTEENTRACK_ADD_SERVICE,
    )
    assert add_call.args[2]["package_tracking_number"] == "1Z123456789"
    assert add_call.args[2]["config_entry_id"] == entry.entry_id
    assert "1Z123456789" not in add_call.args[2]["package_friendly_name"]


@pytest.mark.asyncio
async def test_forwarding_recognizes_existing_remote_package(registry):
    """Packages already in 17TRACK should be marked forwarded without re-adding."""
    registry.register_package("940012345678", "usps", "in_transit")
    hass, entry = _hass_with_entry()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock(
        return_value={
            "packages": [
                {"tracking_number": "940012345678"},
            ]
        }
    )

    result = await async_forward_pending_to_seventeentrack(
        hass,
        registry,
        entry.entry_id,
    )

    assert result.existing_remote == 1
    assert result.forwarded == 0
    assert registry.is_forwarded(
        "940012345678",
        SEVENTEENTRACK_DOMAIN,
        entry.entry_id,
    )
    hass.services.async_call.assert_awaited_once_with(
        SEVENTEENTRACK_DOMAIN,
        SEVENTEENTRACK_GET_SERVICE,
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )


@pytest.mark.asyncio
async def test_forwarding_does_not_repeat_persisted_handoff(registry):
    """A persisted provider handoff should suppress duplicate submissions."""
    registry.register_package("1ZABC", "ups", "in_transit")
    hass, entry = _hass_with_entry()
    registry.mark_forwarded(
        "1ZABC",
        SEVENTEENTRACK_DOMAIN,
        entry.entry_id,
    )
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock()

    result = await async_forward_pending_to_seventeentrack(
        hass,
        registry,
        entry.entry_id,
    )

    assert result.pending == 0
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_forwarding_failure_is_retryable(registry):
    """A failed 17TRACK add should remain unforwarded for a later retry."""
    registry.register_package("1ZFAIL", "ups", "in_transit")
    hass, entry = _hass_with_entry()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock(
        side_effect=[
            {"packages": []},
            RuntimeError("simulated provider failure"),
        ]
    )

    result = await async_forward_pending_to_seventeentrack(
        hass,
        registry,
        entry.entry_id,
    )

    assert result.failed == 1
    assert result.forwarded == 0
    assert not registry.is_forwarded(
        "1ZFAIL",
        SEVENTEENTRACK_DOMAIN,
        entry.entry_id,
    )


@pytest.mark.asyncio
async def test_forwarding_ignores_delivered_packages(registry):
    """Delivered packages should not be newly submitted to 17TRACK."""
    registry.register_package("DONE123", "ups", "delivered")
    hass, entry = _hass_with_entry()
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock()

    result = await async_forward_pending_to_seventeentrack(
        hass,
        registry,
        entry.entry_id,
    )

    assert result.pending == 0
    hass.services.async_call.assert_not_awaited()
