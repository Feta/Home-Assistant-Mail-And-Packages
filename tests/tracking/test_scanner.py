"""Tests for the local universal tracking-number scanner."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.mail_and_packages.tracking.registry import PackageRegistry
from custom_components.mail_and_packages.tracking.scanner import (
    async_scan_tracking_emails,
    extract_tracking_candidates,
)


def _message(
    body: str,
    *,
    subject: str = "Shipping update",
    sender: str = "store@example.com",
) -> bytes:
    """Build a small RFC822 message for scanner tests."""
    return (
        f"From: {sender}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode()


@pytest.fixture
def registry():
    """Create an in-memory package registry."""
    with patch("custom_components.mail_and_packages.tracking.registry.Store"):
        yield PackageRegistry(MagicMock(), "entry")


@pytest.fixture
def account():
    """Create an IMAP account mock scoped to the Packages folder."""
    account = MagicMock()
    account._folders = ["Packages"]
    account._current_folder = "Packages"
    return account


def test_extracts_distinctive_tracking_formats():
    """Distinctive carrier formats should not require sender-specific parsing."""
    candidates = extract_tracking_candidates(
        _message(
            "UPS 1Z999AA10123456784, Amazon TBA123456789012, "
            "USPS 9400111899560000000000, DHL JD014600007180009616"
        )
    )

    found = {(item.carrier, item.tracking_number) for item in candidates}
    assert ("ups", "1Z999AA10123456784") in found
    assert ("amazon", "TBA123456789012") in found
    assert ("usps", "9400111899560000000000") in found
    assert ("dhl", "JD014600007180009616") in found


def test_extracts_international_alpha_suffix_formats():
    """Royal Mail and Australia Post formats should be recognized."""
    candidates = extract_tracking_candidates(
        _message("Parcels AB123456789GB and CD123456789AU are on the way")
    )

    found = {(item.carrier, item.tracking_number) for item in candidates}
    assert ("royal", "AB123456789GB") in found
    assert ("auspost", "CD123456789AU") in found


def test_fedex_numeric_requires_shipping_context():
    """Ambiguous numeric strings should not be accepted without carrier context."""
    candidates = extract_tracking_candidates(
        _message(
            "Invoice reference 123456789012 and customer number 123456789012345",
            subject="Your invoice",
        )
    )

    assert not candidates


def test_fedex_numeric_accepted_with_carrier_context():
    """FedEx numeric tracking should be accepted when carrier context is present."""
    candidates = extract_tracking_candidates(
        _message(
            "Track your shipment with tracking number 123456789012.",
            subject="FedEx shipment update",
            sender="TrackingUpdates@fedex.com",
        )
    )

    assert [(item.carrier, item.tracking_number) for item in candidates] == [
        ("fedex", "123456789012")
    ]


def test_common_sixteen_digit_number_is_not_tracking():
    """A generic 16-digit number should not be treated as a tracking number."""
    candidates = extract_tracking_candidates(
        _message("Order 1234567812345678 has been paid.", subject="Receipt")
    )

    assert not candidates


def test_usps_specific_number_is_not_reclassified_as_fedex():
    """USPS-prefixed numeric formats should win over generic FedEx lengths."""
    candidates = extract_tracking_candidates(
        _message(
            "FedEx and USPS tracking update: 94001118995600000000",
            subject="Package tracking",
        )
    )

    assert [(item.carrier, item.tracking_number) for item in candidates] == [
        ("usps", "94001118995600000000")
    ]


@pytest.mark.asyncio
async def test_scan_registers_package_and_marks_uid(registry, account):
    """A successful scan should register packages and persist UID dedupe state."""
    cache = MagicMock()
    cache.fetch = AsyncMock(
        return_value=("OK", [_message("Track package 1Z999AA10123456784")])
    )

    with patch(
        "custom_components.mail_and_packages.tracking.scanner._execute_single_search",
        AsyncMock(return_value=[b"123"]),
    ):
        result = await async_scan_tracking_emails(
            account,
            cache,
            registry,
            "20-Sep-2026",
            max_messages=75,
            processed_uid_days=7,
        )

    assert result.scanned_messages == 1
    assert len(result.detected) == 1
    assert result.state_changed
    assert registry.packages["1Z999AA10123456784"]["source"] == "universal_scan"
    assert registry.packages["1Z999AA10123456784"]["source_from"] == "example.com"
    assert registry.is_uid_processed("v1:Packages/123")


@pytest.mark.asyncio
async def test_scan_skips_previously_processed_uid(registry, account):
    """Previously processed messages should not be fetched again."""
    registry.mark_uid_processed("v1:Packages/123")
    cache = MagicMock()
    cache.fetch = AsyncMock()

    with patch(
        "custom_components.mail_and_packages.tracking.scanner._execute_single_search",
        AsyncMock(return_value=[b"123"]),
    ):
        result = await async_scan_tracking_emails(
            account,
            cache,
            registry,
            "20-Sep-2026",
            max_messages=75,
            processed_uid_days=7,
        )

    assert result.scanned_messages == 0
    cache.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_retries_fetch_failure(registry, account):
    """A failed fetch should not mark the UID processed."""
    cache = MagicMock()
    cache.fetch = AsyncMock(return_value=("BAD", ["temporary failure"]))

    with patch(
        "custom_components.mail_and_packages.tracking.scanner._execute_single_search",
        AsyncMock(return_value=[b"123"]),
    ):
        result = await async_scan_tracking_emails(
            account,
            cache,
            registry,
            "20-Sep-2026",
            max_messages=75,
            processed_uid_days=7,
        )

    assert result.fetch_failures == 1
    assert not registry.is_uid_processed("v1:Packages/123")


@pytest.mark.asyncio
async def test_scan_processes_newest_messages_in_bounded_batches(registry, account):
    """A large backlog should be processed in bounded newest-first batches."""
    cache = MagicMock()
    cache.fetch = AsyncMock(return_value=("OK", [_message("No tracking here")]))

    with patch(
        "custom_components.mail_and_packages.tracking.scanner._execute_single_search",
        AsyncMock(return_value=[b"1", b"2", b"3", b"4"]),
    ):
        result = await async_scan_tracking_emails(
            account,
            cache,
            registry,
            "20-Sep-2026",
            max_messages=2,
            processed_uid_days=7,
        )

    assert result.scanned_messages == 2
    assert not registry.is_uid_processed("v1:Packages/1")
    assert not registry.is_uid_processed("v1:Packages/2")
    assert registry.is_uid_processed("v1:Packages/3")
    assert registry.is_uid_processed("v1:Packages/4")


@pytest.mark.asyncio
async def test_scan_timeout_preserves_partial_progress(registry, account):
    """Timeout should return already-processed state instead of discarding it."""
    cache = MagicMock()

    async def _fetch(email_id, *args, **kwargs):
        if email_id == b"1":
            return ("OK", [_message("Track package 1Z999AA10123456784")])
        await asyncio.sleep(0.05)
        return ("OK", [_message("Track package TBA123456789012")])

    cache.fetch = AsyncMock(side_effect=_fetch)

    with patch(
        "custom_components.mail_and_packages.tracking.scanner._execute_single_search",
        AsyncMock(return_value=[b"1", b"2"]),
    ):
        result = await async_scan_tracking_emails(
            account,
            cache,
            registry,
            "20-Sep-2026",
            max_messages=75,
            processed_uid_days=7,
            timeout_seconds=0.01,
        )

    assert result.timed_out
    assert result.scanned_messages == 1
    assert registry.is_uid_processed("v1:Packages/1")
    assert not registry.is_uid_processed("v1:Packages/2")
    assert "1Z999AA10123456784" in registry.packages
    assert "TBA123456789012" not in registry.packages


@pytest.mark.asyncio
async def test_scan_does_not_resurrect_cleared_tracking(registry, account):
    """Cleared tracking numbers should remain suppressed if an old email is rescanned."""
    registry.register_package("1Z999AA10123456784", "ups", "delivered")
    registry.clear_package("1Z999AA10123456784")
    cache = MagicMock()
    cache.fetch = AsyncMock(
        return_value=("OK", [_message("Track package 1Z999AA10123456784")])
    )

    with patch(
        "custom_components.mail_and_packages.tracking.scanner._execute_single_search",
        AsyncMock(return_value=[b"123"]),
    ):
        result = await async_scan_tracking_emails(
            account,
            cache,
            registry,
            "20-Sep-2026",
            max_messages=75,
            processed_uid_days=7,
        )

    assert not result.detected
    assert registry.packages["1Z999AA10123456784"]["status"] == "cleared"
    assert registry.is_uid_processed("v1:Packages/123")
