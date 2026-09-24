"""Local universal tracking-number scanner.

The scanner only reads messages from the IMAP folders already configured for
Mail and Packages. It does not call external services or log message content.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any

from aioimaplib import IMAP4_SSL

from custom_components.mail_and_packages.utils.cache import EmailCache
from custom_components.mail_and_packages.utils.imap import _execute_single_search

from .registry import PackageRegistry

_LOGGER = logging.getLogger(__name__)

MAX_EMAIL_TEXT_CHARS = 250_000
TRACKING_CONTEXT_WINDOW = 180

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


@dataclass(slots=True)
class UniversalScanResult:
    """Summary of one universal scan pass."""

    scanned_messages: int = 0
    fetch_failures: int = 0
    state_changed: bool = False
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


def _message_search_text(raw_message: bytes) -> tuple[str, str, str]:
    """Return subject, body text, and sender domain for one raw message."""
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

    return subject, "\n".join(body_parts), sender


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
        subject, body, sender_domain = _message_search_text(raw_message)
    except (TypeError, ValueError, UnicodeError):
        return []

    search_text = f"{subject}\n{body}"
    text_upper = search_text.upper()
    text_lower = search_text.lower()
    header_lower = f"{sender_domain} {subject}".lower()

    candidates: list[TrackingCandidate] = []
    seen: set[str] = set()

    for pattern in TRACKING_PATTERNS:
        for match in pattern.regex.finditer(text_upper):
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
        return uid

    folders = getattr(account, "_folders", None)
    folder = getattr(account, "_current_folder", None)
    if not folder and isinstance(folders, (list, tuple)) and folders:
        folder = folders[0]
    return f"{folder or 'INBOX'}/{uid}"


async def async_scan_tracking_emails(
    account: IMAP4_SSL,
    cache: EmailCache,
    registry: PackageRegistry,
    since_date: str,
    *,
    max_messages: int,
    processed_uid_days: int,
) -> UniversalScanResult:
    """Scan unprocessed messages in configured folders for tracking numbers."""
    result = UniversalScanResult()

    expired_uids = registry.expire_processed_uids(processed_uid_days)
    if expired_uids:
        result.state_changed = True

    email_ids = await _execute_single_search(account, f"SINCE {since_date}")
    unprocessed = [
        email_id
        for email_id in email_ids
        if not registry.is_uid_processed(_processed_uid_key(account, email_id))
    ]
    if max_messages > 0:
        unprocessed = unprocessed[-max_messages:]

    _LOGGER.debug(
        "Universal tracking scan considering %s unprocessed message(s)",
        len(unprocessed),
    )

    for email_id in unprocessed:
        fetched = await cache.fetch(
            email_id,
            "(BODY.PEEK[])",
            shipper="universal",
        )
        if not isinstance(fetched, tuple) or len(fetched) < 2 or fetched[0] != "OK":
            result.fetch_failures += 1
            continue

        uid_key = _processed_uid_key(account, email_id)
        message_candidates: dict[str, TrackingCandidate] = {}
        for raw_message in _iter_raw_messages(fetched[1]):
            for candidate in extract_tracking_candidates(raw_message):
                message_candidates.setdefault(candidate.tracking_number, candidate)

        for candidate in message_candidates.values():
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

        if registry.mark_uid_processed(uid_key):
            result.state_changed = True
        result.scanned_messages += 1

    return result
