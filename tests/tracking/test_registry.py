"""Tests for the persistent package registry."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.mail_and_packages.tracking.registry import (
    STATUS_RANK,
    PackageRegistry,
)


@pytest.fixture
def mock_store():
    """Mock Home Assistant storage."""
    with patch(
        "custom_components.mail_and_packages.tracking.registry.Store"
    ) as store_class:
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        store.async_remove = AsyncMock()
        store_class.return_value = store
        yield store


@pytest.fixture
def registry(mock_store):
    """Create a registry with mocked storage."""
    return PackageRegistry(MagicMock(), "entry")


@pytest.mark.asyncio
async def test_load_is_idempotent(registry, mock_store):
    await registry.async_load()
    await registry.async_load()
    mock_store.async_load.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_normalizes_and_advances(registry):
    await registry.async_load()
    assert registry.register_package(" 1zabc ", "UPS", "detected")
    assert "1ZABC" in registry.packages
    assert registry.register_package("1ZABC", "ups", "in_transit")
    assert registry.register_package("1ZABC", "ups", "delivered")
    assert not registry.register_package("1ZABC", "ups", "in_transit")
    assert registry.packages["1ZABC"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_carrier_confirmation_without_status_change(registry):
    await registry.async_load()
    registry.register_package("PKG1", "ups", "in_transit", source="universal_scan")
    assert registry.register_package(
        "PKG1", "ups", "in_transit", source="carrier_email"
    )
    assert registry.packages["PKG1"]["carrier_confirmed"] is True


@pytest.mark.asyncio
async def test_cleared_package_not_redetected(registry):
    await registry.async_load()
    registry.register_package("PKG1", "ups", "delivered")
    assert registry.clear_package("pkg1")
    assert not registry.register_package("PKG1", "ups", "delivered")
    assert registry.add_package("pkg1", "ups")
    assert registry.packages["PKG1"]["status"] == "detected"


@pytest.mark.asyncio
async def test_reconcile_tracking_details(registry):
    await registry.async_load()
    transitions = registry.reconcile_tracking_details(
        {
            "ups_delivering": ["1z123"],
            "fedex_delivered": ["987654321"],
        }
    )
    assert registry.packages["1Z123"]["status"] == "in_transit"
    assert registry.packages["987654321"]["status"] == "delivered"
    assert {item["status"] for item in transitions} == {"in_transit", "delivered"}


@pytest.mark.asyncio
async def test_reconcile_does_not_downgrade_delivered(registry):
    await registry.async_load()
    registry.reconcile_tracking_details({"ups_delivered": ["1Z123"]})
    registry.reconcile_tracking_details({"ups_delivering": ["1Z123"]})
    assert registry.packages["1Z123"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_counts_and_coordinator_data(registry):
    await registry.async_load()
    registry.register_package("A", "ups", "detected")
    registry.register_package("B", "fedex", "in_transit")
    registry.register_package("C", "usps", "delivered")
    data = registry.coordinator_data()
    assert data["registry_tracked"] == 3
    assert data["registry_in_transit"] == 2
    assert data["registry_delivered"] == 1
    assert len(data["registry_packages_list"]) == 3


@pytest.mark.asyncio
async def test_auto_expire(registry):
    await registry.async_load()
    registry.register_package("DELIVERED", "ups", "delivered")
    registry.register_package("DETECTED", "ups", "detected")
    registry.register_package("CLEARED", "ups", "delivered")
    registry.clear_package("CLEARED")

    registry.packages["DELIVERED"]["last_updated"] = (
        datetime.now(timezone.utc) - timedelta(days=4)
    ).isoformat()
    registry.packages["DETECTED"]["last_updated"] = (
        datetime.now(timezone.utc) - timedelta(days=15)
    ).isoformat()
    registry.packages["CLEARED"]["last_updated"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()

    assert registry.auto_expire() == 3
    assert registry.packages == {}


@pytest.mark.asyncio
async def test_manual_mark_and_clear(registry):
    await registry.async_load()
    registry.add_package("1ZMANUAL", "ups")
    assert registry.mark_delivered("1zmanual")
    assert registry.packages["1ZMANUAL"]["status"] == "delivered"
    assert registry.clear_package("1zmanual")
    assert registry.packages["1ZMANUAL"]["status"] == "cleared"


@pytest.mark.asyncio
async def test_save_and_remove(registry, mock_store):
    await registry.async_load()
    registry.add_package("1Z123", "ups")
    await registry.async_save()
    await registry.async_remove()

    mock_store.async_save.assert_awaited_once()
    mock_store.async_remove.assert_awaited_once()


def test_status_rank_ordering():
    assert STATUS_RANK["detected"] < STATUS_RANK["in_transit"]
    assert STATUS_RANK["in_transit"] < STATUS_RANK["out_for_delivery"]
    assert STATUS_RANK["out_for_delivery"] < STATUS_RANK["delivered"]
    assert STATUS_RANK["delivered"] < STATUS_RANK["cleared"]
