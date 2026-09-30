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
async def test_walmart_order_id_is_not_registered_as_carrier_tracking(registry):
    """Walmart's merchant order ID should not be forwarded as carrier tracking."""
    await registry.async_load()

    transitions = registry.reconcile_tracking_details(
        {
            "walmart_delivering": ["2000153-93327828"],
            "walmart_exception": ["2000153-93327828"],
        }
    )

    assert transitions == []
    assert registry.packages == {}


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
    assert data["registry_archived"] == 0
    assert data["registry_health"] == "healthy"
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

    assert registry.reconcile_amazon_orders(
        {
            "123-1234567-1234567": {
                "status": "delivered",
            }
        }
    )
    assert (
        registry.enrich_package_merchant(
            "1Z123",
            {
                "merchant": "Amazon",
                "order_id": "123-1234567-1234567",
            },
        )
        is False
    )

    data = registry.coordinator_data()
    amazon_order = data["registry_amazon_orders_list"][0]
    assert amazon_order["order_id"] == "123-1234567-1234567"
    assert amazon_order["name"] == "Bambu Lab Filament Dryer"
    assert amazon_order["status"] == "delivered"
    package = data["registry_packages_list"][0]
    assert package["merchant"]["merchant"] == "Amazon"
    assert package["merchant"]["name"] == "Bambu Lab Filament Dryer"
    assert package["merchant"]["status"] == "delivered"
    assert package["merchant"]["expected_delivery"] == "2026-09-25"
    assert package["merchant"]["image"].startswith("https://m.media-amazon.com/")


@pytest.mark.asyncio
async def test_pending_orders_exclude_shipped_orders(registry):
    """Shipped merchant orders should not remain in the pending-tracking list."""
    await registry.async_load()
    registry.reconcile_merchant_orders(
        "Amazon",
        {
            "111-1111111-1111111": {
                "status": "shipped",
                "expected_delivery": "2026-11-03",
            }
        },
    )
    registry.reconcile_merchant_orders(
        "Walmart",
        {
            "2000153-93327828": {
                "status": "awaiting_tracking",
                "expected_delivery": "2026-10-01",
            }
        },
    )

    pending = registry.get_pending_orders_list()

    assert len(pending) == 1
    assert pending[0]["merchant"] == "Walmart"
    assert pending[0]["order_id"] == "2000153-93327828"


@pytest.mark.asyncio
async def test_merchant_rescan_does_not_downgrade_attached_order(registry):
    """A rescanned confirmation email must not downgrade an attached shipment."""
    await registry.async_load()
    registry.reconcile_merchant_orders(
        "Walmart",
        {
            "2000153-93327828": {
                "status": "awaiting_tracking",
                "expected_delivery": "2026-10-01",
                "item_count": 1,
                "description": "1 item",
            }
        },
    )
    registry.attach_tracking_to_order(
        "Walmart",
        "2000153-93327828",
        "877829797830",
        "fedex",
    )

    assert registry.reconcile_merchant_orders(
        "Walmart",
        {
            "2000153-93327828": {
                "status": "awaiting_tracking",
                "expected_delivery": "2026-10-01",
                "item_count": 2,
                "description": "2 items",
            }
        },
    )

    order = registry.get_merchant_orders_list("Walmart")[0]
    assert order["status"] == "shipped"
    assert order["tracking_number"] == "877829797830"
    assert order["carrier"] == "fedex"
    assert order["item_count"] == 2
    assert order["description"] == "2 items"

    package = registry.packages["877829797830"]
    assert package["merchant"]["status"] == "shipped"
    assert package["merchant"]["item_count"] == 2
    assert package["merchant"]["description"] == "2 items"


@pytest.mark.asyncio
async def test_split_merchant_order_keeps_package_specific_metadata(registry):
    """Split orders should enrich each package with only its own shipment metadata."""
    await registry.async_load()
    assert registry.reconcile_merchant_orders(
        "Walmart",
        {
            "2000153-93327828": {
                "status": "shipped",
                "expected_delivery": "2026-10-01",
                "item_count": 2,
                "description": "2 items • 2 shipments",
                "tracking_numbers": ["540576743144", "877829797830"],
                "shipments": [
                    {
                        "part_number": 1,
                        "part_count": 2,
                        "tracking_number": "540576743144",
                        "carrier": "fedex",
                        "status": "shipped",
                        "expected_delivery": "2026-09-30",
                        "item_count": 1,
                        "description": "1 item",
                    },
                    {
                        "part_number": 2,
                        "part_count": 2,
                        "tracking_number": "877829797830",
                        "carrier": "fedex",
                        "status": "shipped",
                        "expected_delivery": "2026-10-01",
                        "item_count": 1,
                        "description": "1 item",
                    },
                ],
            }
        },
    )

    registry.register_package("540576743144", "fedex", "detected")
    registry.enrich_package_merchant(
        "540576743144",
        {
            "merchant": "Walmart",
            "order_id": "2000153-93327828",
            "part_number": 1,
            "part_count": 2,
            "expected_delivery": "2026-09-30",
            "item_count": 1,
            "description": "1 item",
            "status": "shipped",
        },
    )
    registry.register_package("877829797830", "fedex", "detected")
    registry.enrich_package_merchant(
        "877829797830",
        {
            "merchant": "Walmart",
            "order_id": "2000153-93327828",
            "part_number": 2,
            "part_count": 2,
            "expected_delivery": "2026-10-01",
            "item_count": 1,
            "description": "1 item",
            "status": "shipped",
        },
    )

    assert (
        registry.reconcile_merchant_orders(
            "Walmart",
            {
                "2000153-93327828": {
                    "status": "shipped",
                    "expected_delivery": "2026-10-01",
                    "item_count": 2,
                    "description": "2 items • 2 shipments",
                    "tracking_numbers": ["540576743144", "877829797830"],
                    "shipments": [
                        {
                            "part_number": 1,
                            "part_count": 2,
                            "tracking_number": "540576743144",
                            "carrier": "fedex",
                            "status": "shipped",
                            "expected_delivery": "2026-09-30",
                            "item_count": 1,
                            "description": "1 item",
                        },
                        {
                            "part_number": 2,
                            "part_count": 2,
                            "tracking_number": "877829797830",
                            "carrier": "fedex",
                            "status": "shipped",
                            "expected_delivery": "2026-10-01",
                            "item_count": 1,
                            "description": "1 item",
                        },
                    ],
                }
            },
        )
        == 0
    )

    first = registry.packages["540576743144"]["merchant"]
    second = registry.packages["877829797830"]["merchant"]
    assert first["expected_delivery"] == "2026-09-30"
    assert first["part_number"] == 1
    assert first["item_count"] == 1
    assert second["expected_delivery"] == "2026-10-01"
    assert second["part_number"] == 2
    assert second["item_count"] == 1

    order = registry.get_merchant_orders_list("Walmart")[0]
    assert "tracking_number" not in order
    assert order["tracking_numbers"] == ["540576743144", "877829797830"]
    assert len(order["shipments"]) == 2


@pytest.mark.asyncio
async def test_carrier_lifecycle_updates_split_shipment_and_order(registry):
    """Carrier lifecycle should advance the matching shipment and aggregate order."""
    await registry.async_load()
    registry.reconcile_merchant_orders(
        "Walmart",
        {
            "2000153-93327828": {
                "status": "shipped",
                "shipments": [
                    {
                        "part_number": 1,
                        "part_count": 2,
                        "tracking_number": "540576743144",
                        "carrier": "fedex",
                        "status": "shipped",
                    },
                    {
                        "part_number": 2,
                        "part_count": 2,
                        "tracking_number": "877829797830",
                        "carrier": "fedex",
                        "status": "shipped",
                    },
                ],
            }
        },
    )
    registry.register_package("540576743144", "fedex", "detected")
    registry.enrich_package_merchant(
        "540576743144",
        {
            "merchant": "Walmart",
            "order_id": "2000153-93327828",
            "part_number": 1,
            "part_count": 2,
            "status": "shipped",
        },
    )

    assert registry.register_package(
        "540576743144",
        "fedex",
        "out_for_delivery",
        source="carrier_email",
    )
    assert registry.sync_tracking_status_to_merchant_order("540576743144")

    order = registry.get_merchant_orders_list("Walmart")[0]
    assert order["status"] == "partially_out_for_delivery"
    assert order["shipments"][0]["status"] == "out_for_delivery"
    assert order["shipments"][1]["status"] == "shipped"
    assert registry.packages["540576743144"]["merchant"]["status"] == "out_for_delivery"

    registry.register_package("877829797830", "fedex", "detected")
    registry.enrich_package_merchant(
        "877829797830",
        {
            "merchant": "Walmart",
            "order_id": "2000153-93327828",
            "part_number": 2,
            "part_count": 2,
            "status": "shipped",
        },
    )
    assert registry.register_package(
        "877829797830",
        "fedex",
        "out_for_delivery",
        source="carrier_email",
    )
    assert registry.sync_tracking_status_to_merchant_order("877829797830")
    order = registry.get_merchant_orders_list("Walmart")[0]
    assert order["status"] == "out_for_delivery"


@pytest.mark.asyncio
async def test_multi_shipment_aggregate_tracks_partial_delivery(registry):
    """Split orders should distinguish partial delivery from full delivery."""
    await registry.async_load()
    registry.reconcile_merchant_orders(
        "Walmart",
        {
            "ORDER-1": {
                "status": "shipped",
                "shipments": [
                    {
                        "tracking_number": "TRACK-A",
                        "carrier": "ups",
                        "status": "shipped",
                    },
                    {
                        "tracking_number": "TRACK-B",
                        "carrier": "ups",
                        "status": "shipped",
                    },
                ],
            }
        },
    )
    for tracking in ("TRACK-A", "TRACK-B"):
        registry.register_package(tracking, "ups", "detected")
        registry.enrich_package_merchant(
            tracking,
            {
                "merchant": "Walmart",
                "order_id": "ORDER-1",
                "status": "shipped",
            },
        )

    registry.register_package(
        "TRACK-A",
        "ups",
        "delivered",
        source="carrier_email",
    )
    assert registry.sync_tracking_status_to_merchant_order("TRACK-A")
    order = registry.get_merchant_orders_list("Walmart")[0]
    assert order["status"] == "partially_delivered"

    registry.register_package(
        "TRACK-B",
        "ups",
        "delivered",
        source="carrier_email",
    )
    assert registry.sync_tracking_status_to_merchant_order("TRACK-B")
    order = registry.get_merchant_orders_list("Walmart")[0]
    assert order["status"] == "delivered"


@pytest.mark.asyncio
async def test_overdue_orders_surface_in_health_summary(registry):
    """Past expected dates should be visible without inventing a delivery outcome."""
    await registry.async_load()
    registry.reconcile_merchant_orders(
        "Amazon",
        {
            "111-1111111-1111111": {
                "status": "shipped",
                "expected_delivery": "2000-01-01",
                "item_count": 1,
            }
        },
    )

    order = registry.get_merchant_orders_list("Amazon")[0]
    assert order["status"] == "shipped"
    assert order["overdue"] is True
    assert order["days_overdue"] > 0

    health = registry.get_health_summary()
    assert health["state"] == "attention"
    assert health["overdue_orders"] == 1


@pytest.mark.asyncio
async def test_package_source_provenance_is_unique(registry):
    """Multiple source messages should enrich one package instead of duplicating it."""
    await registry.async_load()
    assert registry.register_package(
        "1ZSOURCE",
        "ups",
        source="universal_scan",
        source_from="store.example",
        source_id="<message-1@example>",
    )
    assert not registry.register_package(
        "1ZSOURCE",
        "ups",
        source="universal_scan",
        source_from="store.example",
        source_id="<message-1@example>",
    )
    assert registry.register_package(
        "1ZSOURCE",
        "ups",
        "in_transit",
        source="carrier_email",
        source_from="ups.com",
        source_id="<message-2@example>",
    )

    sources = registry.packages["1ZSOURCE"]["sources"]
    assert len(sources) == 2
    assert sources[0]["source_id"] == "<message-1@example>"
    assert sources[1]["source_id"] == "<message-2@example>"


@pytest.mark.asyncio
async def test_provider_not_found_does_not_override_confirmed_carrier_status(registry):
    """17TRACK Not Found should not flag a carrier-confirmed active shipment."""
    await registry.async_load()
    registry.register_package(
        "540576743144",
        "fedex",
        "out_for_delivery",
        source="carrier_email",
    )
    registry.packages["540576743144"]["first_seen"] = (
        datetime.now(UTC) - timedelta(hours=72)
    ).isoformat()

    registry.reconcile_tracking_provider_packages(
        "seventeentrack",
        "entry-1",
        [{"tracking_number": "540576743144", "status": "Not Found"}],
    )

    package = registry.get_packages_list()[0]
    assert package["status"] == "out_for_delivery"
    assert package["exception"] is False
    assert package["awaiting_carrier_activation"] is False


@pytest.mark.asyncio
async def test_generic_merchant_order_can_attach_tracking(registry):
    """A pending Walmart order should accept manually retrieved carrier tracking."""
    await registry.async_load()
    assert registry.reconcile_merchant_orders(
        "Walmart",
        {
            "2000153-93327828": {
                "status": "awaiting_tracking",
                "expected_delivery": "2026-10-01",
                "item_count": 2,
                "description": "2 items",
            }
        },
    )

    assert registry.attach_tracking_to_order(
        "Walmart",
        "2000153-93327828",
        "123456789012",
        "fedex",
    )

    package = registry.packages["123456789012"]
    assert package["carrier"] == "fedex"
    assert package["source"] == "manual"
    assert package["merchant"]["merchant"] == "Walmart"
    assert package["merchant"]["order_id"] == "2000153-93327828"
    assert package["merchant"]["expected_delivery"] == "2026-10-01"

    order = registry.get_merchant_orders_list("Walmart")[0]
    assert order["tracking_number"] == "123456789012"
    assert order["carrier"] == "fedex"
    assert order["status"] == "shipped"

    data = registry.coordinator_data()
    assert data["registry_pending_orders_list"] == []


@pytest.mark.asyncio
async def test_attach_tracking_requires_existing_order(registry):
    """Manual order attachment should not invent an unknown merchant order."""
    await registry.async_load()
    assert not registry.attach_tracking_to_order(
        "Walmart",
        "missing",
        "123456789012",
        "fedex",
    )
    assert registry.packages == {}


@pytest.mark.asyncio
async def test_provider_not_found_has_grace_period(registry):
    """A newly discovered label should not be an exception while carriers activate it."""
    await registry.async_load()
    registry.register_package("9400111899560000000000", "usps", "detected")
    registry.packages["9400111899560000000000"]["first_seen"] = (
        datetime.now(UTC) - timedelta(hours=24)
    ).isoformat()

    registry.reconcile_tracking_provider_packages(
        "seventeentrack",
        "entry-1",
        [{"tracking_number": "9400111899560000000000", "status": "Not Found"}],
    )

    package = registry.get_packages_list()[0]
    assert package["exception"] is False
    assert package["awaiting_carrier_activation"] is True
    assert package["carrier_activation"]["state"] == "awaiting"
    assert package["carrier_activation"]["check_count"] == 1

    registry.packages["9400111899560000000000"]["first_seen"] = (
        datetime.now(UTC) - timedelta(hours=49)
    ).isoformat()
    registry.reconcile_tracking_provider_packages(
        "seventeentrack",
        "entry-1",
        [{"tracking_number": "9400111899560000000000", "status": "Not Found"}],
    )

    package = registry.get_packages_list()[0]
    assert package["exception"] is True
    assert package["awaiting_carrier_activation"] is False
    assert package["carrier_activation"]["state"] == "timed_out"
    assert package["carrier_activation"]["check_count"] == 2


@pytest.mark.asyncio
async def test_auto_expire(registry):
    """Expired completed records should archive while false detections are removed."""
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
    assert set(registry.archived_packages) == {"DELIVERED", "CLEARED"}
    assert "DETECTED" not in registry.archived_packages

    # Archive records continue to suppress rediscovery, while manual add is
    # an explicit override for a genuinely reused tracking number.
    assert not registry.register_package("DELIVERED", "ups", "in_transit")
    assert registry.add_package("DELIVERED", "ups")
    assert "DELIVERED" in registry.packages
    assert "DELIVERED" not in registry.archived_packages


@pytest.mark.asyncio
async def test_archived_merchant_order_reopens_only_for_meaningful_updates(registry):
    """Archived orders should ignore stale mail but accept new shipment evidence."""
    await registry.async_load()
    registry.reconcile_merchant_orders(
        "Amazon",
        {
            "111-2222222-3333333": {
                "status": "delivered",
                "tracking_number": "TRACK-A",
                "carrier": "ups",
            }
        },
    )
    key = "111-2222222-3333333"
    registry.merchant_orders[key]["last_updated"] = (
        datetime.now(UTC) - timedelta(days=4)
    ).isoformat()

    assert registry.auto_expire() == 1
    assert key not in registry.merchant_orders
    assert key in registry.archived_merchant_orders

    assert (
        registry.reconcile_merchant_orders(
            "Amazon",
            {
                "111-2222222-3333333": {
                    "status": "shipped",
                    "tracking_number": "TRACK-A",
                    "carrier": "ups",
                }
            },
        )
        == 0
    )
    assert key in registry.archived_merchant_orders

    assert (
        registry.reconcile_merchant_orders(
            "Amazon",
            {
                "111-2222222-3333333": {
                    "status": "delivered",
                    "tracking_numbers": ["TRACK-A", "TRACK-B"],
                }
            },
        )
        == 1
    )
    assert key in registry.merchant_orders
    assert key not in registry.archived_merchant_orders
    assert registry.merchant_orders[key]["tracking_numbers"] == [
        "TRACK-A",
        "TRACK-B",
    ]


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
