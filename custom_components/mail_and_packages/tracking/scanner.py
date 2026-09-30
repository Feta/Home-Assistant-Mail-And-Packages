"""Local universal tracking-number scanner.

The scanner only reads messages from the IMAP folders already configured for
Mail and Packages. It does not call external services or log message content.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from aioimaplib import IMAP4_SSL

from custom_components.mail_and_packages.utils.amazon import (
    extract_amazon_order_details,
)
from custom_components.mail_and_packages.utils.cache import EmailCache
from custom_components.mail_and_packages.utils.imap import _execute_single_search

from .registry import PackageRegistry

_LOGGER = logging.getLogger(__name__)

MAX_EMAIL_TEXT_CHARS = 250_000
TRACKING_CONTEXT_WINDOW = 180
SCANNER_UID_VERSION = 7
AMAZON_ORDER_PATTERN = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")
WALMART_ORDER_PATTERN = re.compile(r"\b#?(\d{7}-\d{7,8})\b")
WALMART_ARRIVES_PATTERN = re.compile(
    r"\bArrives\s+(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+)?"
    r"([A-Za-z]{3,9})\s+(\d{1,2})\b",
    re.IGNORECASE,
)
WALMART_ITEM_COUNT_PATTERN = re.compile(r"\b(\d+)\s+items?\b", re.IGNORECASE)
WALMART_PART_PATTERN = re.compile(
    r"\bPart\s+(\d+)\s+of\s+(\d+)\b(.*?)(?=\bPart\s+\d+\s+of\s+\d+\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)
WALMART_TRACKING_LABEL_PATTERN = re.compile(
    r"\b(FedEx|UPS|USPS|DHL)\s+tracking\s+number\s*[:#-]?\s*\[?([A-Z0-9]+)",
    re.IGNORECASE,
)
AMAZON_ITEM_COUNT_PATTERN = re.compile(r"\b(\d+)\s+items?\b", re.IGNORECASE)
AMAZON_ARRIVING_WEEKDAY_PATTERN = re.compile(
    r"\bArriving\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
    re.IGNORECASE,
)
AMAZON_ARRIVING_DATE_PATTERN = re.compile(
    r"\bArriving\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?[,]?\s*"
    r"([A-Za-z]{3,9})\s+(\d{1,2})\b",
    re.IGNORECASE,
)

CARRIER_EMAIL_DOMAINS: dict[str, tuple[str, ...]] = {
    "fedex": ("fedex.com",),
    "ups": ("ups.com",),
    "usps": ("tracking.usps.com",),
    "dhl": ("dhl.com",),
}

TRACKING_CONTEXT_KEYWORDS = (
    "tracking",
    "track your",
    "track package",
    "track shipment",
    "shipment",
    "shipped",
    "shipping",
    "delivery",
    "deliver",
    "package",
    "parcel",
    "carrier",
    "in transit",
    "out for delivery",
    "on the way",
    "dispatched",
    "consignment",
)


@dataclass(frozen=True, slots=True)
class TrackingPattern:
    """One universal tracking-number pattern."""

    carrier: str
    regex: re.Pattern[str]
    requires_context: bool = False
    carrier_hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrackingCandidate:
    """Tracking number extracted from one message."""

    tracking_number: str
    carrier: str
    source_domain: str = ""
    merchant: dict[str, Any] | None = None
    status: str = "detected"
    source: str = "universal_scan"


@dataclass(slots=True)
class UniversalScanResult:
    """Summary of one universal scan pass."""

    scanned_messages: int = 0
    fetch_failures: int = 0
    state_changed: bool = False
    timed_out: bool = False
    detected: list[dict[str, str]] = field(default_factory=list)
    transitions: list[dict[str, str]] = field(default_factory=list)


TRACKING_PATTERNS: tuple[TrackingPattern, ...] = (
    TrackingPattern(
        "ups",
        re.compile(r"\b(1Z[0-9A-Z]{16})\b", re.IGNORECASE),
        requires_context=True,
        carrier_hints=("ups ", "ups:", "ups.com", "united parcel service"),
    ),
    TrackingPattern(
        "amazon",
        re.compile(r"\b(TBA\d{12})\b", re.IGNORECASE),
    ),
    TrackingPattern(
        "usps",
        re.compile(r"(?<!\d)(9[2345]\d{15,26})(?!\d)"),
    ),
    TrackingPattern(
        "dhl",
        re.compile(r"\b((?:JJD|JD)\d{8,18})\b", re.IGNORECASE),
    ),
    TrackingPattern(
        "royal",
        re.compile(r"\b([A-Z]{2}\d{9}GB)\b", re.IGNORECASE),
    ),
    TrackingPattern(
        "auspost",
        re.compile(r"\b([A-Z]{2}\d{9}AU)\b", re.IGNORECASE),
    ),
    TrackingPattern(
        "dpd",
        re.compile(r"(?<![A-Z0-9])(\d{13}[A-Z0-9]{1,2})(?![A-Z0-9])", re.IGNORECASE),
        requires_context=True,
        carrier_hints=("dpd",),
    ),
    TrackingPattern(
        "fedex",
        re.compile(r"(?<!\d)(\d{12}|\d{15}|\d{20}|\d{22})(?!\d)"),
        requires_context=True,
        carrier_hints=("fedex", "fed ex"),
    ),
)


def _sender_domain(message: Any) -> str:
    """Return only the sender domain, never the full email address."""
    address = parseaddr(str(message.get("From", "")))[1].lower()
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[1]


def _decode_text_part(part: Any) -> str:
    """Decode one text MIME part without raising on malformed email."""
    payload = part.get_payload(decode=True)
    if not isinstance(payload, (bytes, bytearray)):
        return ""

    charset = part.get_content_charset() or "utf-8"
    try:
        decoded = bytes(payload).decode(charset, "ignore")
    except (LookupError, UnicodeError):
        decoded = bytes(payload).decode("utf-8", "ignore")
    return html.unescape(decoded)


def _message_search_text(raw_message: bytes) -> tuple[str, str, str, Any]:
    """Return subject, body text, sender domain, and parsed message."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    subject = str(message.get("Subject", "") or "")
    sender = _sender_domain(message)

    body_parts: list[str] = []
    chars = 0
    for part in message.walk():
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        disposition = str(part.get("Content-Disposition", "")).lower()
        if disposition.startswith("attachment"):
            continue

        text = _decode_text_part(part)
        if not text:
            continue
        if part.get_content_type() == "text/html":
            # Search visible email text rather than href/src tokens. Tracking IDs
            # embedded only in opaque URLs are not evidence of a real package.
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)

        remaining = MAX_EMAIL_TEXT_CHARS - chars
        if remaining <= 0:
            break
        text = text[:remaining]
        body_parts.append(text)
        chars += len(text)

    return subject, "\n".join(body_parts), sender, message


def _amazon_expected_delivery(message: Any, body: str) -> str | None:
    """Extract a simple Amazon arrival date from common US email wording."""
    sent = _message_datetime(message).date()

    if match := AMAZON_ARRIVING_DATE_PATTERN.search(body):
        if expected := _date_from_month_day(message, match.group(1), match.group(2)):
            return expected

    if match := AMAZON_ARRIVING_WEEKDAY_PATTERN.search(body):
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target = weekdays[match.group(1).lower()]
        return (sent + timedelta(days=(target - sent.weekday()) % 7)).isoformat()

    lower = body.lower()
    if "arriving tomorrow" in lower:
        return (sent + timedelta(days=1)).isoformat()
    if "arriving today" in lower:
        return sent.isoformat()
    return None


def _amazon_status(subject: str, message: Any) -> str | None:
    """Map common Amazon subjects/senders to a merchant-order lifecycle."""
    lower_subject = subject.lower()
    if "out for delivery" in lower_subject:
        return "out_for_delivery"
    if lower_subject.startswith("delivered"):
        return "delivered"
    sender = str(message.get("From", "")).lower()
    if lower_subject.startswith("shipped") or "shipment-tracking@" in sender:
        return "shipped"
    if lower_subject.startswith("ordered"):
        return "ordered"
    return None


def _item_count_metadata(
    pattern: re.Pattern[str],
    text: str,
) -> dict[str, Any]:
    """Return item count and display text using the largest count in a message."""
    counts = [int(found.group(1)) for found in pattern.finditer(text)]
    if not counts:
        return {}
    item_count = max(counts)
    return {
        "item_count": item_count,
        "description": (
            f"{item_count} item" if item_count == 1 else f"{item_count} items"
        ),
    }


def _amazon_merchant_metadata(
    message: Any,
    subject: str,
    body: str,
    sender_domain: str,
) -> dict[str, Any] | None:
    """Extract Amazon order and shipment metadata from Amazon-authored mail."""
    if "amazon." not in sender_domain:
        return None

    combined = f"{subject}\n{body}"
    order_match = AMAZON_ORDER_PATTERN.search(combined)
    details = extract_amazon_order_details(subject, body, message) or {}
    if not order_match and not details:
        return None

    metadata: dict[str, Any] = {
        "merchant": "Amazon",
        **{key: value for key, value in details.items() if value},
    }
    if order_match:
        metadata["order_id"] = order_match.group(0)
    if status := _amazon_status(subject, message):
        metadata["status"] = status

    metadata.update(_item_count_metadata(AMAZON_ITEM_COUNT_PATTERN, combined))
    if expected := _amazon_expected_delivery(message, combined):
        metadata["expected_delivery"] = expected
    return metadata


def _message_datetime(message: Any) -> datetime:
    """Return the message timestamp or current UTC time when unavailable."""
    try:
        sent = parsedate_to_datetime(str(message.get("Date", "") or ""))
    except (TypeError, ValueError, OverflowError):
        sent = None
    if sent is None:
        return datetime.now(UTC)
    if sent.tzinfo is None:
        return sent.replace(tzinfo=UTC)
    return sent


def _month_number(month_text: str) -> int | None:
    """Parse an abbreviated or full English month name."""
    for fmt in ("%b", "%B"):
        try:
            return datetime.strptime(month_text, fmt).month
        except ValueError:
            continue
    return None


def _date_from_month_day(
    message: Any,
    month_text: str,
    day_text: str,
) -> str | None:
    """Resolve an email-relative month/day to an ISO date."""
    sent = _message_datetime(message)
    month = _month_number(month_text)
    if month is None:
        return None
    try:
        candidate = datetime(sent.year, month, int(day_text), tzinfo=UTC)
    except ValueError:
        return None
    if (sent - candidate).days > 180:
        try:
            candidate = candidate.replace(year=sent.year + 1)
        except ValueError:
            return None
    return candidate.date().isoformat()


def _walmart_expected_delivery(message: Any, body: str) -> str | None:
    """Extract the first Walmart arrival date as an ISO date."""
    match = WALMART_ARRIVES_PATTERN.search(body)
    if not match:
        return None
    return _date_from_month_day(message, match.group(1), match.group(2))


def _walmart_shipments(message: Any, body: str) -> list[dict[str, Any]]:
    """Extract shipment parts from Walmart split-shipment email content."""
    shipments: dict[str, dict[str, Any]] = {}

    for part_match in WALMART_PART_PATTERN.finditer(body):
        part_number = int(part_match.group(1))
        part_count = int(part_match.group(2))
        block = part_match.group(3)

        tracking_match = WALMART_TRACKING_LABEL_PATTERN.search(block)
        if not tracking_match:
            continue

        carrier = tracking_match.group(1).lower()
        if carrier == "fedex":
            carrier = "fedex"
        tracking = tracking_match.group(2).upper()

        shipment: dict[str, Any] = {
            "part_number": part_number,
            "part_count": part_count,
            "tracking_number": tracking,
            "carrier": carrier,
            "status": "shipped",
        }
        if expected := _walmart_expected_delivery(message, block):
            shipment["expected_delivery"] = expected

        item_counts = [
            int(found.group(1)) for found in WALMART_ITEM_COUNT_PATTERN.finditer(block)
        ]
        if item_counts:
            item_count = max(item_counts)
            shipment["item_count"] = item_count
            shipment["description"] = (
                f"{item_count} item" if item_count == 1 else f"{item_count} items"
            )

        shipments[tracking] = shipment

    return sorted(
        shipments.values(),
        key=lambda item: (
            int(item.get("part_number", 0)),
            str(item.get("tracking_number", "")),
        ),
    )


def _walmart_status(combined: str) -> str:
    """Map Walmart mail wording to a merchant-order lifecycle."""
    lower = combined.lower()
    if "delivered" in lower or "arrived:" in lower:
        return "delivered"
    if "out for delivery" in lower:
        return "out_for_delivery"
    if "let you know when" in lower and "on the way" in lower:
        return "awaiting_tracking"
    if "shipped" in lower or "on the way" in lower:
        return "shipped"
    return "awaiting_tracking"


def _walmart_shipment_summary(shipments: list[dict[str, Any]]) -> dict[str, Any]:
    """Build order-level summary fields from parsed Walmart shipment parts."""
    metadata: dict[str, Any] = {
        "shipments": shipments,
        "tracking_numbers": [shipment["tracking_number"] for shipment in shipments],
    }

    expected_dates = [
        shipment["expected_delivery"]
        for shipment in shipments
        if shipment.get("expected_delivery")
    ]
    if expected_dates:
        metadata["expected_delivery"] = max(expected_dates)

    item_counts = [
        int(shipment["item_count"])
        for shipment in shipments
        if shipment.get("item_count") is not None
    ]
    if item_counts:
        item_count = sum(item_counts)
        item_label = f"{item_count} item" if item_count == 1 else f"{item_count} items"
        metadata["item_count"] = item_count
        metadata["description"] = (
            f"{item_label} • {len(shipments)} shipments"
            if len(shipments) > 1
            else item_label
        )

    if len(shipments) == 1:
        metadata["tracking_number"] = shipments[0]["tracking_number"]
        metadata["carrier"] = shipments[0]["carrier"]
    return metadata


def _walmart_fallback_summary(message: Any, body: str, combined: str) -> dict[str, Any]:
    """Build metadata for Walmart mail that does not expose shipment parts."""
    metadata = _item_count_metadata(WALMART_ITEM_COUNT_PATTERN, combined)
    if expected := _walmart_expected_delivery(message, body):
        metadata["expected_delivery"] = expected
    return metadata


def _walmart_merchant_metadata(
    message: Any,
    subject: str,
    body: str,
    sender_domain: str,
) -> dict[str, Any] | None:
    """Extract Walmart order metadata, including split shipment parts."""
    if not (sender_domain == "walmart.com" or sender_domain.endswith(".walmart.com")):
        return None

    combined = f"{subject}\n{body}"
    order_match = WALMART_ORDER_PATTERN.search(combined)
    if not order_match:
        return None

    metadata: dict[str, Any] = {
        "merchant": "Walmart",
        "order_id": order_match.group(1),
        "status": _walmart_status(combined),
        "source_domain": sender_domain,
    }
    shipments = _walmart_shipments(message, body)
    if shipments:
        metadata.update(_walmart_shipment_summary(shipments))
    else:
        metadata.update(_walmart_fallback_summary(message, body, combined))
    return metadata


def _shipment_merchant_metadata(
    merchant: dict[str, Any] | None,
    tracking_number: str,
) -> dict[str, Any] | None:
    """Return package-specific merchant metadata for one shipment tracking ID."""
    if not isinstance(merchant, dict):
        return merchant

    shipments = merchant.get("shipments")
    if not isinstance(shipments, list):
        return merchant

    tracking = tracking_number.upper()
    for shipment in shipments:
        if not isinstance(shipment, dict):
            continue
        if str(shipment.get("tracking_number") or "").upper() != tracking:
            continue
        return {
            "merchant": merchant.get("merchant"),
            "order_id": merchant.get("order_id"),
            "status": shipment.get("status", merchant.get("status")),
            "expected_delivery": shipment.get("expected_delivery"),
            "item_count": shipment.get("item_count"),
            "description": shipment.get("description"),
            "part_number": shipment.get("part_number"),
            "part_count": shipment.get("part_count"),
        }
    return merchant


def extract_merchant_orders(raw_message: bytes) -> list[dict[str, Any]]:
    """Extract pre-tracking merchant orders from one raw email."""
    try:
        subject, body, sender_domain, message = _message_search_text(raw_message)
    except (TypeError, ValueError, UnicodeError):
        return []

    orders: list[dict[str, Any]] = []

    amazon = _amazon_merchant_metadata(
        message,
        subject,
        body,
        sender_domain,
    )
    if amazon and amazon.get("order_id"):
        orders.append(amazon)

    walmart = _walmart_merchant_metadata(
        message,
        subject,
        body,
        sender_domain,
    )
    if walmart:
        orders.append(walmart)

    return orders


def _has_disqualifying_numeric_label(
    text_lower: str,
    start: int,
    pattern: TrackingPattern,
) -> bool:
    """Reject labeled non-tracking IDs that resemble numeric carrier numbers."""
    if pattern.carrier != "fedex":
        return False

    lookback = text_lower[max(0, start - 120) : start].rstrip()
    return bool(
        re.search(
            r"(?:purchase\s+order(?:\s+number)?|invoice(?:\s+number)?|"
            r"reference|customer(?:\s+number)?|account(?:\s+number)?)"
            r"\s*[:#-]?\s*$",
            lookback,
        )
    )


def _sender_matches_carrier(sender_domain: str, carrier: str) -> bool:
    """Return whether the sender domain belongs to the detected carrier."""
    suffixes = CARRIER_EMAIL_DOMAINS.get(carrier, ())
    return any(
        sender_domain == suffix or sender_domain.endswith(f".{suffix}")
        for suffix in suffixes
    )


def _carrier_email_lifecycle(
    subject: str,
    body: str,
    sender_domain: str,
    carrier: str,
) -> str | None:
    """Translate trustworthy carrier-authored email wording to registry lifecycle."""
    if not _sender_matches_carrier(sender_domain, carrier):
        return None

    text = f"{subject}\n{body[:1000]}".lower()
    if "delivered" in text:
        return "delivered"
    if "out for delivery" in text:
        return "out_for_delivery"
    if any(
        phrase in text
        for phrase in (
            "on the way",
            "in transit",
            "scheduled for delivery",
            "shipment picked up",
            "we have your package",
        )
    ):
        return "in_transit"
    return "detected"


def _has_context(
    text_lower: str,
    header_lower: str,
    start: int,
    end: int,
    pattern: TrackingPattern,
    merchant_context: bool = False,
) -> bool:
    """Require carrier and shipping context for ambiguous numeric formats."""
    left = max(0, start - TRACKING_CONTEXT_WINDOW)
    right = min(len(text_lower), end + TRACKING_CONTEXT_WINDOW)
    nearby = text_lower[left:right]

    local_shipping = any(keyword in nearby for keyword in TRACKING_CONTEXT_KEYWORDS)
    carrier_nearby = any(hint in nearby for hint in pattern.carrier_hints)
    carrier_header = any(hint in header_lower for hint in pattern.carrier_hints)

    # 1Z-shaped values can appear inside unrelated opaque tokens. Generic
    # words such as "package" or "delivery" are not enough on their own.
    # Accept explicit UPS evidence or a recognized merchant message that
    # genuinely exposes a shipment/tracking value.
    if pattern.carrier == "ups":
        return local_shipping and (
            carrier_nearby or carrier_header or merchant_context
        )

    if carrier_nearby and local_shipping:
        return True

    if carrier_header:
        return local_shipping or any(
            keyword in text_lower for keyword in TRACKING_CONTEXT_KEYWORDS
        )

    return False


def extract_tracking_candidates(raw_message: bytes) -> list[TrackingCandidate]:
    """Extract conservative tracking candidates from one raw email."""
    try:
        subject, body, sender_domain, message = _message_search_text(raw_message)
    except (TypeError, ValueError, UnicodeError):
        return []

    search_text = f"{subject}\n{body}"
    text_lower = search_text.lower()
    header_lower = f"{sender_domain} {subject}".lower()
    merchant = _amazon_merchant_metadata(
        message,
        subject,
        body,
        sender_domain,
    )
    if merchant is None:
        merchant = _walmart_merchant_metadata(
            message,
            subject,
            body,
            sender_domain,
        )

    candidates: list[TrackingCandidate] = []
    seen: set[str] = set()

    for pattern in TRACKING_PATTERNS:
        for match in pattern.regex.finditer(search_text):
            tracking = match.group(1).upper()
            if tracking in seen:
                continue
            if _has_disqualifying_numeric_label(
                text_lower,
                match.start(1),
                pattern,
            ):
                continue
            if pattern.requires_context and not _has_context(
                text_lower,
                header_lower,
                match.start(1),
                match.end(1),
                pattern,
                merchant_context=merchant is not None,
            ):
                continue

            # USPS prefixed numbers are more specific than generic FedEx numeric
            # lengths and are claimed earlier in TRACKING_PATTERNS.
            seen.add(tracking)
            lifecycle = _carrier_email_lifecycle(
                subject,
                body,
                sender_domain,
                pattern.carrier,
            )
            candidates.append(
                TrackingCandidate(
                    tracking_number=tracking,
                    carrier=pattern.carrier,
                    source_domain=sender_domain,
                    merchant=_shipment_merchant_metadata(merchant, tracking),
                    status=lifecycle or "detected",
                    source=(
                        "carrier_email" if lifecycle is not None else "universal_scan"
                    ),
                )
            )

    return candidates


def _iter_raw_messages(fetch_lines: Any):
    """Yield plausible RFC822 message bytes from an IMAP fetch response."""
    if not isinstance(fetch_lines, list):
        return

    for item in fetch_lines:
        raw: bytes | None = None
        if isinstance(item, (bytes, bytearray)):
            raw = bytes(item)
        elif (
            isinstance(item, tuple)
            and len(item) > 1
            and isinstance(item[1], (bytes, bytearray))
        ):
            raw = bytes(item[1])

        if raw is not None and (b"\n" in raw or b"\r" in raw):
            yield raw


def _processed_uid_key(account: IMAP4_SSL, email_id: str | bytes) -> str:
    """Build a folder-aware stable key for a message UID."""
    uid = email_id.decode() if isinstance(email_id, bytes) else str(email_id)
    if "/" in uid:
        return f"v{SCANNER_UID_VERSION}:{uid}"

    folders = getattr(account, "_folders", None)
    folder = getattr(account, "_current_folder", None)
    if not folder and isinstance(folders, (list, tuple)) and folders:
        folder = folders[0]
    return f"v{SCANNER_UID_VERSION}:{folder or 'INBOX'}/{uid}"


def _select_unprocessed_email_ids(
    account: IMAP4_SSL,
    registry: PackageRegistry,
    email_ids: list[str | bytes],
    max_messages: int,
) -> list[str | bytes]:
    """Return a bounded list of messages that have not been scanned yet."""
    unprocessed = [
        email_id
        for email_id in email_ids
        if not registry.is_uid_processed(_processed_uid_key(account, email_id))
    ]
    if max_messages > 0:
        return unprocessed[-max_messages:]
    return unprocessed


def _extract_fetch_candidates(fetch_lines: Any) -> dict[str, TrackingCandidate]:
    """Extract de-duplicated candidates from one IMAP fetch response."""
    candidates: dict[str, TrackingCandidate] = {}
    for raw_message in _iter_raw_messages(fetch_lines):
        for candidate in extract_tracking_candidates(raw_message):
            candidates.setdefault(candidate.tracking_number, candidate)
    return candidates


def _extract_fetch_merchant_orders(fetch_lines: Any) -> list[dict[str, Any]]:
    """Extract de-duplicated merchant orders from one IMAP fetch response."""
    orders: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_message in _iter_raw_messages(fetch_lines):
        for order in extract_merchant_orders(raw_message):
            merchant = str(order.get("merchant") or "")
            order_id = str(order.get("order_id") or "")
            if merchant and order_id:
                orders[(merchant.lower(), order_id)] = order
    return list(orders.values())


def _register_message_orders(
    registry: PackageRegistry,
    orders: list[dict[str, Any]],
    result: UniversalScanResult,
) -> None:
    """Persist merchant orders even when no carrier tracking exists yet."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for order in orders:
        merchant = str(order.get("merchant") or "").strip()
        order_id = str(order.get("order_id") or "").strip()
        if not merchant or not order_id:
            continue
        metadata = {
            key: value
            for key, value in order.items()
            if key not in {"merchant", "order_id"}
        }
        grouped.setdefault(merchant, {})[order_id] = metadata

    for merchant, merchant_orders in grouped.items():
        if registry.reconcile_merchant_orders(merchant, merchant_orders):
            result.state_changed = True


def _register_message_candidates(
    registry: PackageRegistry,
    candidates: dict[str, TrackingCandidate],
    result: UniversalScanResult,
) -> None:
    """Register candidates and collect new-package events."""
    for candidate in candidates.values():
        existing = registry.packages.get(candidate.tracking_number)
        previous_status = (
            str(existing.get("status") or "detected")
            if isinstance(existing, dict)
            else ""
        )
        was_new = existing is None
        changed = registry.register_package(
            candidate.tracking_number,
            candidate.carrier,
            status=candidate.status,
            source=candidate.source,
            source_from=candidate.source_domain,
            description="Detected from configured shipping mail",
        )
        if changed:
            result.state_changed = True
        if candidate.merchant and registry.enrich_package_merchant(
            candidate.tracking_number,
            candidate.merchant,
        ):
            result.state_changed = True
        if registry.sync_tracking_status_to_merchant_order(candidate.tracking_number):
            result.state_changed = True
        if was_new and changed:
            event = {
                "tracking_number": candidate.tracking_number,
                "carrier": candidate.carrier,
                "status": candidate.status,
                "previous_status": "",
                "source": candidate.source,
            }
            result.detected.append(event)
            if candidate.status != "detected":
                result.transitions.append(event)
        elif changed and candidate.status not in {"detected", previous_status}:
            result.transitions.append(
                {
                    "tracking_number": candidate.tracking_number,
                    "carrier": candidate.carrier,
                    "status": candidate.status,
                    "previous_status": previous_status,
                    "source": candidate.source,
                }
            )


async def _async_scan_messages(
    account: IMAP4_SSL,
    cache: EmailCache,
    registry: PackageRegistry,
    email_ids: list[str | bytes],
    result: UniversalScanResult,
) -> None:
    """Fetch and process a bounded list of messages."""
    for email_id in email_ids:
        fetched = await cache.fetch(
            email_id,
            "(BODY.PEEK[])",
            shipper="universal",
        )
        if not isinstance(fetched, tuple) or len(fetched) < 2 or fetched[0] != "OK":
            result.fetch_failures += 1
            continue

        merchant_orders = _extract_fetch_merchant_orders(fetched[1])
        _register_message_orders(registry, merchant_orders, result)

        candidates = _extract_fetch_candidates(fetched[1])
        _register_message_candidates(registry, candidates, result)

        uid_key = _processed_uid_key(account, email_id)
        if registry.mark_uid_processed(uid_key):
            result.state_changed = True
        result.scanned_messages += 1


async def _async_scan_once(
    account: IMAP4_SSL,
    cache: EmailCache,
    registry: PackageRegistry,
    since_date: str,
    max_messages: int,
    processed_uid_days: int,
    result: UniversalScanResult,
) -> None:
    """Run one bounded universal scan pass."""
    if registry.expire_processed_uids(processed_uid_days):
        result.state_changed = True

    email_ids = await _execute_single_search(account, f"SINCE {since_date}")
    unprocessed = _select_unprocessed_email_ids(
        account,
        registry,
        email_ids,
        max_messages,
    )
    _LOGGER.debug(
        "Universal tracking scan considering %s unprocessed message(s)",
        len(unprocessed),
    )
    await _async_scan_messages(
        account,
        cache,
        registry,
        unprocessed,
        result,
    )


async def async_scan_tracking_emails(
    account: IMAP4_SSL,
    cache: EmailCache,
    registry: PackageRegistry,
    since_date: str,
    *,
    max_messages: int,
    processed_uid_days: int,
    timeout_seconds: float | None = None,
) -> UniversalScanResult:
    """Scan unprocessed messages in configured folders for tracking numbers."""
    result = UniversalScanResult()

    if timeout_seconds is None:
        await _async_scan_once(
            account,
            cache,
            registry,
            since_date,
            max_messages,
            processed_uid_days,
            result,
        )
        return result

    try:
        async with asyncio.timeout(timeout_seconds):
            await _async_scan_once(
                account,
                cache,
                registry,
                since_date,
                max_messages,
                processed_uid_days,
                result,
            )
    except TimeoutError:
        result.timed_out = True

    return result
