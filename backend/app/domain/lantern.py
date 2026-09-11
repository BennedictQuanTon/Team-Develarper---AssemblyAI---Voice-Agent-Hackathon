"""The Lantern — structured restaurant domain (menu / floor / 86 / takeout).

Source of truth for prices, availability, modifiers and table state. The voice
agent reads THROUGH these functions only — it must never invent a price, an
available->false item, or a seat. RAG is reserved for long free-text policy;
menu facts live here, deterministically.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
LANTERN_DIR = ROOT_DIR / "data" / "lantern"

ALLERGEN_FLAG = "none"  # a menu item with this value in allergens [] means "no flagged allergens"


@dataclass
class MenuItem:
    sku: str
    name: str
    category: str
    price: float
    description: str
    allergens: list[str]
    spicy_level: int
    available: bool
    ordered_count: int
    fits: list[str]
    goes_with: list[str]
    modifiers: list[str]

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


@dataclass
class Table:
    id: str
    name: str
    seats: int
    status: str


class LanternStore:
    """Thread-safe in-memory restaurant state, seeded from data/lantern/*.json."""

    def __init__(self, lantern_dir: Path = LANTERN_DIR):
        self._dir = lantern_dir
        self._lock = threading.RLock()
        self._menu: dict[str, MenuItem] = {}
        self._tables: dict[str, Table] = {}
        self._schedule: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._menu = self._load_menu()
            self._tables = self._load_tables()
            self._schedule = self._load_schedule()

    # ---- loaders -----------------------------------------------------------
    def _load_menu(self) -> dict[str, MenuItem]:
        path = self._dir / "menu.json"
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        menu: dict[str, MenuItem] = {}
        for item in payload.get("items", []):
            allergens = item.get("allergens") or []
            if not allergens:
                allergens = [ALLERGEN_FLAG]
            mi = MenuItem(
                sku=str(item["sku"]),
                name=str(item["name"]),
                category=str(item.get("category") or ""),
                price=float(item.get("price") or 0.0),
                description=str(item.get("description") or ""),
                allergens=allergens,
                spicy_level=int(item.get("spicy_level") or 0),
                available=bool(item.get("available", True)),
                ordered_count=int(item.get("ordered_count") or 0),
                fits=[str(f) for f in (item.get("fits") or [])],
                goes_with=[str(g) for g in (item.get("goes_with") or [])],
                modifiers=[str(m) for m in (item.get("modifiers") or [])],
            )
            menu[mi.sku] = mi
        return menu

    def _load_tables(self) -> dict[str, Table]:
        path = self._dir / "tables.json"
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        tables: dict[str, Table] = {}
        for t in payload.get("tables", []):
            tables[str(t["id"])] = Table(
                id=str(t["id"]),
                name=str(t.get("name") or t["id"]),
                seats=int(t.get("seats") or 0),
                status=str(t.get("status") or "free"),
            )
        return tables

    def _load_schedule(self) -> dict[str, Any]:
        path = self._dir / "schedule.json"
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    # ---- menu --------------------------------------------------------------
    def list_menu(self, *, available_only: bool = False) -> list[MenuItem]:
        with self._lock:
            items = list(self._menu.values())
        if available_only:
            items = [i for i in items if i.available]
        return sorted(items, key=lambda i: i.category)

    def get_item(self, sku: str) -> MenuItem | None:
        with self._lock:
            return self._menu.get(sku)

    def find_item(self, name_or_sku: str) -> MenuItem | None:
        """Resolve a spoken name or SKU to the canonical item."""
        key = (name_or_sku or "").strip()
        if not key:
            return None
        lowered = key.lower()
        with self._lock:
            if key in self._menu:
                return self._menu[key]
            for mi in self._menu.values():
                if mi.name.lower() == lowered:
                    return mi
            # substring match (best effort for spoken variants)
            matches = [mi for mi in self._menu.values() if lowered in mi.name.lower()]
            if matches:
                return max(matches, key=lambda m: m.ordered_count)
        return None

    def is_available(self, sku: str) -> bool:
        with self._lock:
            mi = self._menu.get(sku)
            return bool(mi and mi.available)

    def set_available(self, sku: str, available: bool) -> MenuItem | None:
        """86 (or restore) an item. Returns updated item or None if unknown."""
        with self._lock:
            mi = self._menu.get(sku)
            if mi is None:
                return None
            mi.available = bool(available)
            return mi

    # ---- floor -------------------------------------------------------------
    def list_tables(self) -> list[Table]:
        with self._lock:
            return list(self._tables.values())

    def get_table(self, table_id: str) -> Table | None:
        with self._lock:
            return self._tables.get(table_id)

    def find_free_table(self, party_size: int) -> Table | None:
        with self._lock:
            free = [
                t
                for t in self._tables.values()
                if t.status == "free" and t.seats >= party_size
            ]
            free.sort(key=lambda t: t.seats)
            return free[0] if free else None

    def seat_party(self, table_id: str, party_size: int) -> Table | None:
        with self._lock:
            t = self._tables.get(table_id)
            if t is None or t.status != "free":
                return None
            if t.seats < party_size:
                return None
            t.status = "seated"
            return t

    def free_table_statuses(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for t in self._tables.values():
                counts[t.status] = counts.get(t.status, 0) + 1
            return counts

    def set_table_status(self, table_id: str, status: str) -> Table | None:
        with self._lock:
            t = self._tables.get(table_id)
            if t is None:
                return None
            t.status = status
            return t

    # ---- schedule ----------------------------------------------------------
    @property
    def hours(self) -> dict[str, str]:
        with self._lock:
            return dict(self._schedule.get("hours", {}))

    def takeout_eta(self, line_count: int) -> int:
        bands = self._schedule.get("takeout_eta_minutes_by_lines", {})
        if line_count <= 2:
            return int(bands.get("1_2", 20))
        if line_count <= 4:
            return int(bands.get("3_4", 25))
        return int(bands.get("5_plus", 35))

    def restaurant_meta(self) -> dict[str, Any]:
        with self._lock:
            return {
                "restaurant": "The Lantern",
                "city": "Da Nang",
                "cuisine": "Vietnamese seafood & grill",
                "hours": self.hours,
                "item_count": len(self._menu),
                "table_count": len(self._tables),
            }

    # ---- keyterms (for AssemblyAI STT boost) -------------------------------
    def keyterms(self, limit: int = 100) -> list[str]:
        terms: list[str] = []
        with self._lock:
            for mi in self._menu.values():
                terms.append(mi.name)
                terms.extend(mi.modifiers)
        # de-dupe preserving order
        seen: set[str] = set()
        out: list[str] = []
        for t in terms:
            key = t.lower()
            if key not in seen:
                seen.add(key)
                out.append(t)
        return out[:limit]


_store: LanternStore | None = None
_store_lock = threading.Lock()


def get_lantern_store() -> LanternStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = LanternStore()
        return _store
