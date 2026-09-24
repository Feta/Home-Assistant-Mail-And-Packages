"""Persistent package registry for Mail and Packages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "mail_and_packages.package_registry"

STATUS_RANK = {
    "detected": 0,
    "in_transit": 1,
    "out_for_delivery": 2,
    "delivered": 3,
    "cleared": 4,
}

PROVIDER_STATUS_LIFECYCLE = {
    "in_transit": "in_transit",
    "out_for_delivery": "out_for_delivery",
    "ready_to_be_picked_up": "in_transit",
    "delivered": "delivered",
}
PROVIDER_EXCEPTION_STATUSES = {
    "alert",
    "expired",
    "not_found",
    "undelivered",
}


class PackageRegistry:
    """Persist package lifecycle state across restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize persistent storage for one config entry."""
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}",
        )
        self._packages: dict[str, dict[str, Any]] = {}
        self._merchant_orders: dict[str, dict[str, Any]] = {}
        self._processed_uids: dict[str, str] = {}
        self._loaded = False

    @staticmethod
    def normalize_tracking_number(tracking_number: str) -> str:
        """Normalize a tracking number for stable registry keys."""
        return str(tracking_number).strip().upper()

    @property
    def packages(self) -> dict[str, dict[str, Any]]:
        """Return all package records, including cleared records."""
        return self._packages

    @property
    def merchant_orders(self) -> dict[str, dict[str, Any]]:
        """Return persisted merchant-order records."""
        return self._merchant_orders

    async def async_load(self) -> None:
        """Load registry data from Home Assistant storage once."""
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._packages = data.get("packages", {})
            self._merchant_orders = data.get("merchant_orders", {})
            self._processed_uids = data.get("processed_uids", {})
        self._loaded = True

    async def async_save(self) -> None:
        """Persist current registry data."""
        await self._store.async_save(
            {
                "packages": self._packages,
                "merchant_orders": self._merchant_orders,
                "processed_uids": self._processed_uids,
            }
        )

    async def async_remove(self) -> None:
        """Remove registry storage for this config entry."""
        await self._store.async_remove()

    @staticmethod
    def _enrich_existing_package(
        existing: dict[str, Any],
        carrier: str,
        source: str,
        now: str,
    ) -> bool:
        """Enrich an existing record without changing its lifecycle state."""
        changed = False
        if carrier != "unknown" and existing.get("carrier", "unknown") == "unknown":
            existing["carrier"] = carrier
            changed = True
        if source == "carrier_email" and not existing.get("carrier_confirmed"):
            existing["carrier_confirmed"] = True
            changed = True
        if changed:
            existing["last_updated"] = now
        return changed

    def register_package(
        self,
        tracking_number: str,
        carrier: str,
        status: str = "detected",
        source: str = "unknown",
        source_from: str = "",
        description: str = "",
    ) -> bool:
        """Add a package or advance an existing package to a later state."""
        if status not in STATUS_RANK:
            raise ValueError(f"Unsupported package status: {status}")
        tracking = self.normalize_tracking_number(tracking_number)
        if not tracking:
            return False
        carrier = str(carrier or "unknown").strip().lower()
        now = datetime.now(UTC).isoformat()

        if tracking in self._packages:
            existing = self._packages[tracking]
            if existing.get("status") == "cleared":
                return False
            current_rank = STATUS_RANK.get(existing.get("status", "detected"), 0)
            new_rank = STATUS_RANK[status]
            if new_rank <= current_rank:
                return self._enrich_existing_package(
                    existing,
                    carrier,
                    source,
                    now,
                )
            existing["status"] = status
            existing["last_updated"] = now
            if carrier != "unknown":
                existing["carrier"] = carrier
            if source == "carrier_email":
                existing["carrier_confirmed"] = True
            existing["exception"] = False
            return True

        self._packages[tracking] = {
            "carrier": carrier,
            "status": status,
            "exception": False,
            "source": source,
            "source_from": source_from,
            "description": description,
            "first_seen": now,
            "last_updated": now,
            "carrier_confirmed": source == "carrier_email",
            "forwarded_to": {},
        }
        return True

    def clear_package(self, tracking_number: str) -> bool:
        """Mark a package as cleared and suppress automatic re-detection."""
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") == "cleared":
            return False
        package["status"] = "cleared"
        package["last_updated"] = datetime.now(UTC).isoformat()
        return True

    def clear_all_delivered(self) -> int:
        """Mark all delivered packages as cleared and return the count."""
        count = 0
        now = datetime.now(UTC).isoformat()
        for package in self._packages.values():
            if package.get("status") == "delivered":
                package["status"] = "cleared"
                package["last_updated"] = now
                count += 1
        return count

    def mark_delivered(self, tracking_number: str) -> bool:
        """Manually advance a package to delivered."""
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") in ("delivered", "cleared"):
            return False
        package["status"] = "delivered"
        package["exception"] = False
        package["last_updated"] = datetime.now(UTC).isoformat()
        return True

    def add_package(self, tracking_number: str, carrier: str = "unknown") -> bool:
        """Manually add a package or explicitly re-add a cleared package."""
        tracking = self.normalize_tracking_number(tracking_number)
        if not tracking:
            return False
        if tracking in self._packages:
            package = self._packages[tracking]
            if package.get("status") != "cleared":
                return False
            package.update(
                {
                    "carrier": str(carrier or "unknown").lower(),
                    "status": "detected",
                    "exception": False,
                    "source": "manual",
                    "source_from": "",
                    "description": "Manually added",
                    "last_updated": datetime.now(UTC).isoformat(),
                    "carrier_confirmed": False,
                    "forwarded_to": {},
                }
            )
            return True
        return self.register_package(
            tracking,
            carrier,
            status="detected",
            source="manual",
            description="Manually added",
        )

    def is_uid_processed(self, uid: str) -> bool:
        """Return whether the universal scanner already processed a message UID."""
        return uid in self._processed_uids

    def mark_uid_processed(self, uid: str) -> bool:
        """Mark a message UID processed, returning whether state changed."""
        if uid in self._processed_uids:
            return False
        self._processed_uids[uid] = datetime.now(UTC).isoformat()
        return True

    def expire_processed_uids(self, max_age_days: int = 7) -> int:
        """Expire old processed UID markers and return the number removed."""
        now = datetime.now(UTC)
        removed = 0
        for uid, date_str in list(self._processed_uids.items()):
            try:
                processed = datetime.fromisoformat(date_str)
            except (TypeError, ValueError):
                self._processed_uids.pop(uid, None)
                removed += 1
                continue

            if processed.tzinfo is None:
                processed = processed.replace(tzinfo=UTC)

            if (now - processed).days > max_age_days:
                self._processed_uids.pop(uid, None)
                removed += 1
        return removed

    def is_forwarded(
        self,
        tracking_number: str,
        provider: str,
        config_entry_id: str | None = None,
    ) -> bool:
        """Return whether a package was forwarded to a provider account."""
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package:
            return False
        forwarded_to = package.get("forwarded_to", {})
        if not isinstance(forwarded_to, dict):
            return False
        provider_state = forwarded_to.get(provider)
        if not isinstance(provider_state, dict):
            return False
        if config_entry_id is None:
            return True
        return provider_state.get("config_entry_id") == config_entry_id

    def mark_forwarded(
        self,
        tracking_number: str,
        provider: str,
        config_entry_id: str,
        *,
        existing_remote: bool = False,
    ) -> bool:
        """Persist a successful tracking-provider handoff."""
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") in ("delivered", "cleared"):
            return False

        forwarded_to = package.setdefault("forwarded_to", {})
        provider_state = forwarded_to.get(provider)
        if (
            isinstance(provider_state, dict)
            and provider_state.get("config_entry_id") == config_entry_id
        ):
            return False

        forwarded_to[provider] = {
            "forwarded_at": datetime.now(UTC).isoformat(),
            "config_entry_id": config_entry_id,
            "existing_remote": existing_remote,
        }
        return True

    def get_forward_candidates(
        self,
        provider: str,
        config_entry_id: str | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return active packages not yet sent to a provider account."""
        candidates: list[tuple[str, dict[str, Any]]] = []
        for tracking, package in self._packages.items():
            if package.get("status") not in (
                "detected",
                "in_transit",
                "out_for_delivery",
            ):
                continue
            if self.is_forwarded(tracking, provider, config_entry_id):
                continue
            candidates.append((tracking, package))
        return candidates

    @staticmethod
    def _normalize_provider_status(status: Any) -> str:
        """Normalize a provider status to a stable snake-case key."""
        return "_".join(str(status or "").strip().lower().replace("-", " ").split())

    @staticmethod
    def _provider_metadata(
        provider: str,
        config_entry_id: str,
        remote: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        """Build a privacy-minimized provider snapshot for one package."""
        metadata = {
            "provider": provider,
            "config_entry_id": config_entry_id,
            "status": str(remote.get("status") or ""),
            "status_key": PackageRegistry._normalize_provider_status(
                remote.get("status")
            ),
            "location": remote.get("location"),
            "info_text": remote.get("info_text"),
            "timestamp": remote.get("timestamp"),
            "friendly_name": remote.get("friendly_name"),
            "origin_country": remote.get("origin_country"),
            "destination_country": remote.get("destination_country"),
            "package_type": remote.get("package_type"),
            "tracking_info_language": remote.get("tracking_info_language"),
            "synced_at": now,
        }
        return {
            key: value for key, value in metadata.items() if value not in (None, "")
        }

    def reconcile_tracking_provider_packages(
        self,
        provider: str,
        config_entry_id: str,
        remote_packages: list[dict[str, Any]],
    ) -> tuple[int, list[dict[str, Any]]]:
        """Merge tracking-provider status and metadata into the registry."""
        changed_count = 0
        transitions: list[dict[str, Any]] = []
        now = datetime.now(UTC).isoformat()

        for remote in remote_packages:
            if not isinstance(remote, dict):
                continue
            raw_tracking = remote.get("tracking_number")
            if not raw_tracking:
                continue

            tracking = self.normalize_tracking_number(raw_tracking)
            status_key = self._normalize_provider_status(remote.get("status"))
            lifecycle = PROVIDER_STATUS_LIFECYCLE.get(status_key)

            package = self._packages.get(tracking)
            if package is None:
                initial_status = lifecycle or "detected"
                if not self.register_package(
                    tracking,
                    "unknown",
                    initial_status,
                    source=provider,
                    description=str(remote.get("friendly_name") or ""),
                ):
                    continue
                package = self._packages[tracking]
                changed_count += 1

            if package.get("status") == "cleared":
                continue

            provider_metadata = self._provider_metadata(
                provider,
                config_entry_id,
                remote,
                now,
            )
            previous_provider = package.get("tracking_provider")
            comparable_previous = (
                {
                    key: value
                    for key, value in previous_provider.items()
                    if key != "synced_at"
                }
                if isinstance(previous_provider, dict)
                else {}
            )
            comparable_new = {
                key: value
                for key, value in provider_metadata.items()
                if key != "synced_at"
            }
            if comparable_previous != comparable_new:
                package["tracking_provider"] = provider_metadata
                package["last_updated"] = now
                changed_count += 1

            previous_status = package.get("status", "detected")
            if lifecycle and STATUS_RANK.get(lifecycle, 0) > STATUS_RANK.get(
                previous_status, 0
            ):
                package["status"] = lifecycle
                package["last_updated"] = now
                changed_count += 1
                transitions.append(
                    {
                        "tracking_number": tracking,
                        "carrier": package.get("carrier", "unknown"),
                        "status": lifecycle,
                        "previous_status": previous_status,
                        "source": provider,
                    }
                )

            provider_exception = status_key in PROVIDER_EXCEPTION_STATUSES
            if package.get("provider_exception", False) != provider_exception:
                package["provider_exception"] = provider_exception
                package["last_updated"] = now
                changed_count += 1

        return changed_count, transitions

    def reconcile_amazon_orders(
        self,
        orders: dict[str, dict[str, Any]],
    ) -> int:
        """Persist Amazon order metadata extracted from shipping emails."""
        if not isinstance(orders, dict):
            return 0

        changed = 0
        now = datetime.now(UTC).isoformat()
        allowed = {
            "name",
            "image",
            "status",
            "expected_delivery",
            "source_domain",
        }

        for order_id, metadata in orders.items():
            if not order_id or not isinstance(metadata, dict):
                continue

            incoming = {
                key: value
                for key, value in metadata.items()
                if key in allowed and value not in (None, "")
            }
            existing = self._merchant_orders.get(str(order_id))
            preserved = (
                {
                    key: value
                    for key, value in existing.items()
                    if key in allowed and value not in (None, "")
                }
                if isinstance(existing, dict)
                else {}
            )
            merged = {
                **preserved,
                **incoming,
                "merchant": "Amazon",
                "order_id": str(order_id),
            }
            comparable_existing = (
                {
                    key: value
                    for key, value in existing.items()
                    if key not in ("first_seen", "last_updated")
                }
                if isinstance(existing, dict)
                else {}
            )
            if comparable_existing == merged:
                continue

            first_seen = (
                existing.get("first_seen", now) if isinstance(existing, dict) else now
            )
            self._merchant_orders[str(order_id)] = {
                **merged,
                "first_seen": first_seen,
                "last_updated": now,
            }
            changed += 1

        # A universal-scan pass may have already linked an Amazon order ID to a
        # physical tracking number. Keep those package-level merchant records
        # synchronized with the richer metadata extracted by the Amazon parser.
        for package in self._packages.values():
            merchant = package.get("merchant")
            if not isinstance(merchant, dict):
                continue
            order_id = str(merchant.get("order_id") or "")
            order = self._merchant_orders.get(order_id)
            if not order:
                continue

            package_metadata = {
                key: value
                for key, value in order.items()
                if key
                in {
                    "merchant",
                    "order_id",
                    "name",
                    "image",
                    "expected_delivery",
                    "status",
                }
                and value not in (None, "")
            }
            existing_merchant = {
                key: value for key, value in merchant.items() if value not in (None, "")
            }
            merged_merchant = {**existing_merchant, **package_metadata}
            if merged_merchant == merchant:
                continue

            package["merchant"] = merged_merchant
            package["last_updated"] = now
            changed += 1

        return changed

    def enrich_package_merchant(
        self,
        tracking_number: str,
        merchant_data: dict[str, Any],
    ) -> bool:
        """Attach merchant metadata when an email links it to a tracking number."""
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") == "cleared":
            return False

        clean = {
            key: value
            for key, value in merchant_data.items()
            if key
            in {
                "merchant",
                "order_id",
                "name",
                "image",
                "expected_delivery",
                "status",
            }
            and value not in (None, "")
        }
        if not clean:
            return False

        existing = package.get("merchant")
        merged = {
            **(existing if isinstance(existing, dict) else {}),
            **clean,
        }
        if existing == merged:
            return False

        package["merchant"] = merged
        package["last_updated"] = datetime.now(UTC).isoformat()
        return True

    def get_amazon_orders_list(self) -> list[dict[str, Any]]:
        """Return Amazon order metadata for dashboard use."""
        return sorted(
            self._merchant_orders.values(),
            key=lambda item: item.get("last_updated", ""),
            reverse=True,
        )

    def set_exception(self, tracking_number: str, value: bool = True) -> bool:
        """Set or clear the exception flag for an active package."""
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") == "cleared":
            return False
        if package.get("exception") == value:
            return False
        package["exception"] = value
        package["last_updated"] = datetime.now(UTC).isoformat()
        return True

    def reconcile_tracking_details(
        self, tracking_details: dict[str, list[str]]
    ) -> list[dict[str, Any]]:
        """Reconcile carrier-parser tracking output into registry state."""
        transitions: list[dict[str, Any]] = []
        for suffix, status in (
            ("_delivering", "in_transit"),
            ("_delivered", "delivered"),
        ):
            for key, numbers in tracking_details.items():
                if not key.endswith(suffix) or not isinstance(numbers, list):
                    continue
                carrier = key[: -len(suffix)]
                for number in numbers:
                    tracking = self.normalize_tracking_number(number)
                    previous = self._packages.get(tracking, {}).get("status")
                    changed = self.register_package(
                        tracking,
                        carrier,
                        status=status,
                        source="carrier_email",
                    )
                    current = self._packages.get(tracking, {}).get("status")
                    if changed and current != previous:
                        transitions.append(
                            {
                                "tracking_number": tracking,
                                "carrier": carrier,
                                "status": current,
                                "previous_status": previous,
                                "source": "carrier_email",
                            }
                        )
        for key, numbers in tracking_details.items():
            if key.endswith("_exception") and isinstance(numbers, list):
                for number in numbers:
                    self.set_exception(number, True)
        return transitions

    def auto_expire(
        self,
        delivered_days: int = 3,
        detected_days: int = 14,
        cleared_days: int = 30,
    ) -> int:
        """Remove stale delivered, cleared, and unconfirmed detected records."""
        now = datetime.now(UTC)
        to_remove: list[str] = []
        for tracking, package in self._packages.items():
            try:
                last_updated = datetime.fromisoformat(package["last_updated"])
            except (KeyError, TypeError, ValueError):
                continue
            age_days = (now - last_updated).days
            status = package.get("status", "detected")
            if (
                (status == "delivered" and age_days >= delivered_days)
                or (status == "cleared" and age_days >= cleared_days)
                or (
                    status == "detected"
                    and not package.get("carrier_confirmed")
                    and age_days >= detected_days
                )
            ):
                to_remove.append(tracking)
        for tracking in to_remove:
            self._packages.pop(tracking, None)

        removed = len(to_remove)
        for order_id, order in list(self._merchant_orders.items()):
            try:
                order_updated = datetime.fromisoformat(order["last_updated"])
            except (KeyError, TypeError, ValueError):
                continue
            order_age = (now - order_updated).days
            order_status = order.get("status", "shipped")
            if (
                order_status == "delivered" and order_age >= delivered_days
            ) or order_age >= detected_days:
                self._merchant_orders.pop(order_id, None)
                removed += 1

        return removed

    def get_counts(self) -> dict[str, int]:
        """Return tracked, in-transit, and delivered package counts."""
        counts = {"tracked": 0, "in_transit": 0, "delivered": 0}
        for package in self._packages.values():
            status = package.get("status", "detected")
            if status == "cleared":
                continue
            counts["tracked"] += 1
            if status in ("detected", "in_transit", "out_for_delivery"):
                counts["in_transit"] += 1
            elif status == "delivered":
                counts["delivered"] += 1
        return counts

    def get_packages_list(
        self, status_filter: str | None = None
    ) -> list[dict[str, Any]]:
        """Return dashboard-friendly package records, optionally filtered."""
        result: list[dict[str, Any]] = []
        for tracking, package in self._packages.items():
            status = package.get("status", "detected")
            if status == "cleared":
                continue
            if status_filter == "in_transit" and status not in (
                "detected",
                "in_transit",
                "out_for_delivery",
            ):
                continue
            if status_filter not in (None, "in_transit") and status != status_filter:
                continue
            result.append(
                {
                    "tracking_number": tracking,
                    "carrier": package.get("carrier", "unknown"),
                    "status": status,
                    "exception": bool(
                        package.get("exception", False)
                        or package.get("provider_exception", False)
                    ),
                    "source": package.get("source", "unknown"),
                    "first_seen": package.get("first_seen", ""),
                    "last_updated": package.get("last_updated", ""),
                    "carrier_confirmed": package.get("carrier_confirmed", False),
                    "forwarded_to": sorted(
                        package.get("forwarded_to", {}).keys()
                        if isinstance(package.get("forwarded_to"), dict)
                        else []
                    ),
                    "tracking_provider": package.get("tracking_provider"),
                    "merchant": package.get("merchant"),
                }
            )
        return result

    def coordinator_data(self) -> dict[str, Any]:
        """Return registry data shaped for coordinator-backed sensors."""
        counts = self.get_counts()
        return {
            "registry_tracked": counts["tracked"],
            "registry_in_transit": counts["in_transit"],
            "registry_delivered": counts["delivered"],
            "registry_packages_list": self.get_packages_list(),
            "registry_in_transit_list": self.get_packages_list("in_transit"),
            "registry_delivered_list": self.get_packages_list("delivered"),
            "registry_amazon_orders_list": self.get_amazon_orders_list(),
        }
