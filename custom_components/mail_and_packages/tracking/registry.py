"""Persistent package registry for Mail and Packages."""

from __future__ import annotations

from datetime import datetime, timezone
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


class PackageRegistry:
    """Persist package lifecycle state across restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}",
        )
        self._packages: dict[str, dict[str, Any]] = {}
        self._processed_uids: dict[str, str] = {}
        self._loaded = False

    @staticmethod
    def normalize_tracking_number(tracking_number: str) -> str:
        return str(tracking_number).strip().upper()

    @property
    def packages(self) -> dict[str, dict[str, Any]]:
        return self._packages

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._packages = data.get("packages", {})
            self._processed_uids = data.get("processed_uids", {})
        self._loaded = True

    async def async_save(self) -> None:
        await self._store.async_save(
            {"packages": self._packages, "processed_uids": self._processed_uids}
        )

    async def async_remove(self) -> None:
        await self._store.async_remove()

    def register_package(
        self,
        tracking_number: str,
        carrier: str,
        status: str = "detected",
        source: str = "unknown",
        source_from: str = "",
        description: str = "",
    ) -> bool:
        if status not in STATUS_RANK:
            raise ValueError(f"Unsupported package status: {status}")
        tracking = self.normalize_tracking_number(tracking_number)
        if not tracking:
            return False
        carrier = str(carrier or "unknown").strip().lower()
        now = datetime.now(timezone.utc).isoformat()

        if tracking in self._packages:
            existing = self._packages[tracking]
            if existing.get("status") == "cleared":
                return False
            current_rank = STATUS_RANK.get(existing.get("status", "detected"), 0)
            new_rank = STATUS_RANK[status]
            if new_rank <= current_rank:
                if source == "carrier_email" and not existing.get("carrier_confirmed"):
                    existing["carrier_confirmed"] = True
                    existing["last_updated"] = now
                    if carrier != "unknown":
                        existing["carrier"] = carrier
                    return True
                return False
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
        }
        return True

    def clear_package(self, tracking_number: str) -> bool:
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") == "cleared":
            return False
        package["status"] = "cleared"
        package["last_updated"] = datetime.now(timezone.utc).isoformat()
        return True

    def clear_all_delivered(self) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for package in self._packages.values():
            if package.get("status") == "delivered":
                package["status"] = "cleared"
                package["last_updated"] = now
                count += 1
        return count

    def mark_delivered(self, tracking_number: str) -> bool:
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") in ("delivered", "cleared"):
            return False
        package["status"] = "delivered"
        package["exception"] = False
        package["last_updated"] = datetime.now(timezone.utc).isoformat()
        return True

    def add_package(self, tracking_number: str, carrier: str = "unknown") -> bool:
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
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "carrier_confirmed": False,
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

    def set_exception(self, tracking_number: str, value: bool = True) -> bool:
        tracking = self.normalize_tracking_number(tracking_number)
        package = self._packages.get(tracking)
        if not package or package.get("status") == "cleared":
            return False
        if package.get("exception") == value:
            return False
        package["exception"] = value
        package["last_updated"] = datetime.now(timezone.utc).isoformat()
        return True

    def reconcile_tracking_details(
        self, tracking_details: dict[str, list[str]]
    ) -> list[dict[str, Any]]:
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
        now = datetime.now(timezone.utc)
        to_remove: list[str] = []
        for tracking, package in self._packages.items():
            try:
                last_updated = datetime.fromisoformat(package["last_updated"])
            except (KeyError, TypeError, ValueError):
                continue
            age_days = (now - last_updated).days
            status = package.get("status", "detected")
            if status == "delivered" and age_days >= delivered_days:
                to_remove.append(tracking)
            elif status == "cleared" and age_days >= cleared_days:
                to_remove.append(tracking)
            elif (
                status == "detected"
                and not package.get("carrier_confirmed")
                and age_days >= detected_days
            ):
                to_remove.append(tracking)
        for tracking in to_remove:
            self._packages.pop(tracking, None)
        return len(to_remove)

    def get_counts(self) -> dict[str, int]:
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

    def get_packages_list(self, status_filter: str | None = None) -> list[dict[str, Any]]:
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
                    "exception": package.get("exception", False),
                    "source": package.get("source", "unknown"),
                    "first_seen": package.get("first_seen", ""),
                    "last_updated": package.get("last_updated", ""),
                    "carrier_confirmed": package.get("carrier_confirmed", False),
                }
            )
        return result

    def coordinator_data(self) -> dict[str, Any]:
        counts = self.get_counts()
        return {
            "registry_tracked": counts["tracked"],
            "registry_in_transit": counts["in_transit"],
            "registry_delivered": counts["delivered"],
            "registry_packages_list": self.get_packages_list(),
            "registry_in_transit_list": self.get_packages_list("in_transit"),
            "registry_delivered_list": self.get_packages_list("delivered"),
        }
