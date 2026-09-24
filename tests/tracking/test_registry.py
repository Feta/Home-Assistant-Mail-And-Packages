"""Tests for the persistent package registry."""

from datetime import UTC, datetime, timedelta
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
    """Load should hit persistent storage only once."""
    await registry.async_load()
    await registry.async_load()
    mock_store.async_load.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_normalizes_and_advances(registry):
    """Registry should normalize keys and only move state forward."""
    await registry.async_load()
    assert registry.register_package(" 1zabc ", "UPS", "detected")
    assert "1ZABC" in registry.packages
    assert registry.register_package("1ZABC", "ups", "in_transit")
    assert registry.register_package("1ZABC", "ups", "delivered")
    assert not registry.register_package("1ZABC", "ups", "in_transit")
    assert registry.packages["1ZABC"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_carrier_confirmation_without_status_change(registry):
    """Carrier mail should confirm an existing package without advancing state."""
    await registry.async_load()
    registry.register_package("PKG1", "ups", "in_transit", source="universal_scan")
    assert registry.register_package(
        "PKG1", "ups", "in_transit", source="carrier_email"
    )
    assert registry.packages["PKG1"]["carrier_confirmed"] is True


@pytest.mark.asyncio
async def test_cleared_package_not_redetected(registry):
    """Cleared packages should stay suppressed until manually re-added."""
    await registry.async_load()
    registry.register_package("PKG1", "ups", "delivered")
    assert registry.clear_package("pkg1")
    assert not registry.register_package("PKG1", "ups", "delivered")
    assert registry.add_package("pkg1", "ups")
    assert registry.packages["PKG1"]["status"] == "detected"


@pytest.mark.asyncio
async def test_reconcile_tracking_details(registry):
    """Carrier parser output should populate and advance registry records."""
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
    """Later scans should never downgrade a delivered package."""
    await registry.async_load()
    registry.reconcile_tracking_details({"ups_delivered": ["1Z123"]})
    registry.reconcile_tracking_details({"ups_delivering": ["1Z123"]})
    assert registry.packages["1Z123"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_counts_and_coordinator_data(registry):
    """Registry should expose dashboard-friendly summary data."""
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
async def test_provider_enrichment_advances_lifecycle(registry):
    """17TRACK metadata should enrich and advance an existing package."""
    await registry.async_load()
    registry.register_package("1Z123", "ups", "detected", source="manual")

    changes, transitions = registry.reconcile_tracking_provider_packages(
        "seventeentrack",
        "entry-1",
        [
            {
                "tracking_number": "1Z123",
                "status": "Delivered",
                "location": "Hartford, CT",
                "info_text": "Package delivered",
                "timestamp": "2026-09-24T14:32:00+00:00",
                "origin_country": "US",
                "destination_country": "US",
            }
        ],
    )

    assert changes > 0
    assert transitions[0]["status"] == "delivered"
    package = registry.packages["1Z123"]
    assert package["status"] == "delivered"
    assert package["tracking_provider"]["provider"] == "seventeentrack"
    assert package["tracking_provider"]["location"] == "Hartford, CT"


@pytest.mark.asyncio
async def test_provider_issue_does_not_downgrade_lifecycle(registry):
    """Provider alert states should set an exception without downgrading lifecycle."""
    await registry.async_load()
    registry.register_package("1Z123", "ups", "in_transit")

    registry.reconcile_tracking_provider_packages(
        "seventeentrack",
        "entry-1",
        [{"tracking_number": "1Z123", "status": "Alert"}],
    )

    assert registry.packages["1Z123"]["status"] == "in_transit"
    assert registry.get_packages_list()[0]["exception"] is True


@pytest.mark.asyncio
async def test_provider_can_import_existing_remote_package(registry):
    """17TRACK packages unknown to email parsing should still appear in the registry."""
    await registry.async_load()

    registry.reconcile_tracking_provider_packages(
        "seventeentrack",
        "entry-1",
        [{"tracking_number": "REMOTE123", "status": "In Transit"}],
    )

    assert registry.packages["REMOTE123"]["status"] == "in_transit"
    assert registry.packages["REMOTE123"]["source"] == "seventeentrack"


@pytest.mark.asyncio
async def test_amazon_orders_and_merchant_metadata(registry):
    """Amazon order metadata should persist separately and link when correlation exists."""
    await registry.async_load()
    assert registry.reconcile_amazon_orders(
        {
            "123-1234567-1234567": {
                "name": "Bambu Lab Filament Dryer",
                "image": "https://m.media-amazon.com/images/I/example.jpg",
                "status": "shipped",
                "expected_delivery": "2026-09-25",
            }
        }
    )

    registry.register_package("1Z123", "ups", "detected")
    assert registry.enrich_package_merchant(
        "1Z123",
        {
            "merchant": "Amazon",
            "order_id": "123-1234567-1234567",
            "name": "Bambu Lab Filament Dryer",
        },
    )

    data = registry.coordinator_data()
    assert data["registry_amazon_orders_list"][0]["order_id"] == ("123-1234567-1234567")
    package = data["registry_packages_list"][0]
    assert package["merchant"]["merchant"] == "Amazon"


@pytest.mark.asyncio
async def test_auto_expire(registry):
    """Expired delivered, detected, and cleared records should be removed."""
    await registry.async_load()
    registry.register_package("DELIVERED", "ups", "delivered")
    registry.register_package("DETECTED", "ups", "detected")
    registry.register_package("CLEARED", "ups", "delivered")
    registry.clear_package("CLEARED")

    registry.packages["DELIVERED"]["last_updated"] = (
        datetime.now(UTC) - timedelta(days=4)
    ).isoformat()
    registry.packages["DETECTED"]["last_updated"] = (
        datetime.now(UTC) - timedelta(days=15)
    ).isoformat()
    registry.packages["CLEARED"]["last_updated"] = (
        datetime.now(UTC) - timedelta(days=31)
    ).isoformat()

    assert registry.auto_expire() == 3
    assert registry.packages == {}


@pytest.mark.asyncio
async def test_manual_mark_and_clear(registry):
    """Manual lifecycle actions should update package state."""
    await registry.async_load()
    registry.add_package("1ZMANUAL", "ups")
    assert registry.mark_delivered("1zmanual")
    assert registry.packages["1ZMANUAL"]["status"] == "delivered"
    assert registry.clear_package("1zmanual")
    assert registry.packages["1ZMANUAL"]["status"] == "cleared"


@pytest.mark.asyncio
async def test_forwarding_state_is_account_scoped(registry):
    """Forwarding state should be persisted per provider account."""
    await registry.async_load()
    registry.register_package("1ZFORWARD", "ups", "in_transit")

    assert registry.mark_forwarded(
        "1ZFORWARD",
        "seventeentrack",
        "entry-a",
    )
    assert registry.is_forwarded(
        "1ZFORWARD",
        "seventeentrack",
        "entry-a",
    )
    assert not registry.is_forwarded(
        "1ZFORWARD",
        "seventeentrack",
        "entry-b",
    )
    assert registry.mark_forwarded(
        "1ZFORWARD",
        "seventeentrack",
        "entry-b",
    )
    assert registry.is_forwarded(
        "1ZFORWARD",
        "seventeentrack",
        "entry-b",
    )


@pytest.mark.asyncio
async def test_same_state_can_enrich_unknown_carrier(registry):
    """A same-state discovery should fill an unknown carrier without downgrading."""
    await registry.async_load()
    registry.register_package("PKG1", "unknown", "detected", source="manual")

    assert registry.register_package(
        "PKG1",
        "ups",
        "detected",
        source="universal_scan",
    )
    assert registry.packages["PKG1"]["carrier"] == "ups"
    assert registry.packages["PKG1"]["status"] == "detected"


@pytest.mark.asyncio
async def test_processed_uid_lifecycle(registry):
    """Processed UID markers should dedupe scans and expire cleanly."""
    await registry.async_load()

    assert registry.mark_uid_processed("Packages/123")
    assert not registry.mark_uid_processed("Packages/123")
    assert registry.is_uid_processed("Packages/123")

    registry._processed_uids["Packages/123"] = (
        datetime.now(UTC) - timedelta(days=9)
    ).isoformat()

    assert registry.expire_processed_uids(max_age_days=7) == 1
    assert not registry.is_uid_processed("Packages/123")


@pytest.mark.asyncio
async def test_save_and_remove(registry, mock_store):
    """Registry should persist and remove its storage cleanly."""
    await registry.async_load()
    registry.add_package("1Z123", "ups")
    await registry.async_save()
    await registry.async_remove()

    mock_store.async_save.assert_awaited_once()
    mock_store.async_remove.assert_awaited_once()


def test_status_rank_ordering():
    """Lifecycle status ranks should remain strictly ordered."""
    assert STATUS_RANK["detected"] < STATUS_RANK["in_transit"]
    assert STATUS_RANK["in_transit"] < STATUS_RANK["out_for_delivery"]
    assert STATUS_RANK["out_for_delivery"] < STATUS_RANK["delivered"]
    assert STATUS_RANK["delivered"] < STATUS_RANK["cleared"]
