"""Package tracking helpers."""

from .forwarding import (
    ForwardingResult,
    ProviderSnapshot,
    async_forward_pending_to_seventeentrack,
    async_get_seventeentrack_snapshot,
)
from .registry import STATUS_RANK, PackageRegistry
from .scanner import UniversalScanResult, async_scan_tracking_emails

__all__ = [
    "STATUS_RANK",
    "ForwardingResult",
    "PackageRegistry",
    "ProviderSnapshot",
    "UniversalScanResult",
    "async_forward_pending_to_seventeentrack",
    "async_get_seventeentrack_snapshot",
    "async_scan_tracking_emails",
]
