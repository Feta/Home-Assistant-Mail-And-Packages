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
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any

from aioimaplib import IMAP4_SSL

from custom_components.mail_and_packages.utils.amazon import extract_amazon_order_details
from custom_components.mail_and_packages.utils.cache import EmailCache
from custom_components.mail_and_packages.utils.imap import _execute_single_search

from .registry import PackageRegistry

_LOGGER = logging.getLogger(__name__)

MAX_EMAIL_TEXT_CHARS = 250_000
TRACKING_CONTEXT_WINDOW = 180
SCANNER_UID_VERSION = 2
AMAZON_ORDER_PATTERN = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")

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
    merchant: dict[str, str] | None = None


@dataclass(slots=True)
class UniversalScanResult:
    """Summary of one universal scan pass."""

    scanned_messages: int = 0
    fetch_failures: int = 0
    state_changed: bool = False
    timed_out: bool = False
    detected: list[dict[str, str]] = field(default_factory=list)


TRACKING_PATTERNS: tuple[TrackingPattern, ...] = (
    TrackingPattern(
        "ups",
        re.compile(r"\b(1Z[0-9A-Z]{16})\b", re.IGNORECASE),
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

        remaining = MAX_EMAIL_TEXT_CHARS - chars
        if remaining <= 0:
            break
        text = text[:remaining]
        body_parts.append(text)
        chars += len(text)

    return subject, "\n".join(body_parts), sender, message


def _amazon_merchant_metadata(
    message: Any,
    subject: str,
    body: str,
    sender_domain: str,
) -> dict[str, str] | None:
    """Extract merchant metadata only from Amazon-authored shipping mail."""
    if "amazon." not in sender_domain:
        return None

    combined = f"{subject}\n{body}"
    match = AMAZON_ORDER_PATTERN.search(combined)
    details = extract_amazon_order_details(subject, body, message) or {}
    if not match and not details:
        return None

    metadata: dict[str, str] = {"merchant": "Amazon"}
    if match:
        metadata["order_id"] = match.group(0)
    for key in ("name", "image"):
        value = details.get(key)
        if value:
            metadata[key] = value
    return metadata


def _has_context(
    text_lower: str,
    header_lower: str,
    start: int,
    end: int,
    pattern: TrackingPattern,
) -> bool:
    """Require carrier and shipping context for ambiguous numeric formats."""
    left = max(0, start - TRACKING_CONTEXT_WINDOW)
    right = min(len(text_lower), end + TRACKING_CONTEXT_WINDOW)
    nearby = text_lower[left:right]

    local_shipping = any(keyword in nearby for keyword in TRACKING_CONTEXT_KEYWORDS)
    carrier_nearby = any(hint in nearby for hint in pattern.carrier_hints)
    carrier_header = any(hint in header_lower for hint in pattern.carrier_hints)

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

    candidates: list[TrackingCandidate] = []
    seen: set[str] = set()

    for pattern in TRACKING_PATTERNS:
        for match in pattern.regex.finditer(search_text):
            tracking = match.group(1).upper()
            if tracking in seen:
                continue
            if pattern.requires_context and not _has_context(
                text_lower,
                header_lower,
                match.start(1),
                match.end(1),
                pattern,
            ):
                continue

            # USPS prefixed numbers are more specific than generic FedEx numeric
            # lengths and are claimed earlier in TRACKING_PATTERNS.
            seen.add(tracking)
            candidates.append(
                TrackingCandidate(
                    tracking_number=tracking,
                    carrier=pattern.carrier,
                    source_domain=sender_domain,
                    merchant=merchant,
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


def _register_message_candidates(
    registry: PackageRegistry,
    candidates: dict[str, TrackingCandidate],
    result: UniversalScanResult,
) -> None:
    """Register candidates and collect new-package events."""
    for candidate in candidates.values():
        was_new = candidate.tracking_number not in registry.packages
        changed = registry.register_package(
            candidate.tracking_number,
            candidate.carrier,
            status="detected",
            source="universal_scan",
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
        if was_new and changed:
            result.detected.append(
                {
                    "tracking_number": candidate.tracking_number,
                    "carrier": candidate.carrier,
                    "status": "detected",
                    "previous_status": "",
                    "source": "universal_scan",
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
