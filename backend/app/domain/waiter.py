"""Waiter toolkit — deterministic restaurant operations for the voice waiter.

Purely Python and JSON-serializable, testable without any LLM/API. The Gemini
agent is only the language front-end: it DECIDES which tool to call, but every
tool here computes state locally (prices, 86, tables) so the model can never
invent inventory.

The "mention stack" is what makes "I'll take those two" resolve to the correct
SKUs: it records the dishes surfaced by tool results in this conversation, so
`add_items_from_mention` maps pronouns back to real items deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.domain.lantern import ALLERGEN_FLAG, LanternStore, MenuItem, get_lantern_store


@dataclass
class BasketLine:
    sku: str
    name: str
    price: float
    qty: int = 1
    modifiers: list[str] = field(default_factory=list)

    def total(self) -> float:
        return round(self.price * self.qty, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "price": self.price,
            "qty": self.qty,
            "modifiers": self.modifiers,
            "line_total": self.total(),
        }


class WaiterSession:
    """One ordering conversation: basket + mention stack + ticket type + party."""

    def __init__(self, store: LanternStore, session_id: str = "waiter"):
        self.store = store
        self.session_id = session_id
        self.basket: list[BasketLine] = []
        self.ticket_type: str = "dine_in"  # dine_in | takeout
        self.table_id: str | None = None
        self.party_size: int = 2
        self.guest_tags: list[str] = []
        self.mentioned: list[dict[str, Any]] = []  # sku/name surfaced in the convo
        self.placed = False

    # ---- helpers -----------------------------------------------------------
    def _record_mentions(self, items: list[MenuItem] | list[dict[str, Any]]) -> None:
        for it in items:
            if isinstance(it, dict):
                sku = it.get("sku")
                name = it.get("name")
            else:
                sku = it.sku
                name = it.name
            if not sku:
                continue
            self.mentioned = [m for m in self.mentioned if m["sku"] != sku]
            self.mentioned.append({"sku": sku, "name": name})
            if len(self.mentioned) > 6:
                self.mentioned = self.mentioned[-6:]

    def total(self) -> float:
        return round(sum(line.total() for line in self.basket), 2)

    # ---- guest profile -----------------------------------------------------
    def set_guest(self, ticket_type: str | None = None, party_size: int | None = None, tags: list[str] | None = None) -> dict[str, Any]:
        if ticket_type in ("dine_in", "takeout"):
            self.ticket_type = ticket_type
        if party_size is not None and party_size > 0:
            self.party_size = party_size
        if tags is not None:
            self.guest_tags = tags
        return {"ticket_type": self.ticket_type, "party_size": self.party_size, "tags": self.guest_tags}

    # ---- tools (each returns a JSON-safe dict) -----------------------------
    def search_menu(self, query: str, limit: int = 5) -> dict[str, Any]:
        q = (query or "").lower()
        matches: list[MenuItem] = []
        for it in self.store.list_menu():
            hay = f"{it.name} {it.category} {it.description}".lower()
            if any(word in hay for word in q.split()) if q else False:
                matches.append(it)
        matches = matches[:limit] if matches else self.store.list_menu(available_only=True)[:limit]
        self._record_mentions(matches)
        return {"items": [self._item_view(it) for it in matches]}

    def recommend_dishes(self, tags: list[str] | str, party_size: int, limit: int = 2) -> dict[str, Any]:
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        tags = [t.lower() for t in (tags or [])]
        available = [it for it in self.store.list_menu() if it.available]
        scored: list[tuple[int, MenuItem]] = []
        for it in available:
            score = 0
            for t in tags:
                if t in it.fits:
                    score += 3
            # popularity baseline
            score += min(it.ordered_count, 250) // 250
            # party-size fit heuristic
            if party_size >= 4 and "share_table" in it.fits:
                score += 1
            scored.append((score, it))
        scored.sort(key=lambda pair: (pair[0], pair[1].ordered_count), reverse=True)
        top = [it for _, it in scored[:limit]]
        self._record_mentions(top)
        reasons = [self._reason(it, tags, party_size) for it in top]
        return {"items": [self._item_view(it) for it in top], "reasons": reasons}

    def _reason(self, it: MenuItem, tags: list[str], party_size: int) -> str:
        reasons: list[str] = []
        if "cheap" not in tags and it.category in ("Grill", "Mains"):
            reasons.append(f"popular this week ({it.ordered_count} orders)")
        for t in tags:
            if t in it.fits:
                reasons.append(f"good for '{t}'")
        if party_size >= 4 and "share_table" in it.fits:
            reasons.append("easy to share at the table")
        if it.goes_with:
            reasons.append(f"pairs with {self.store.get_item(it.goes_with[0]).name if self.store.get_item(it.goes_with[0]) else ''}")
        return "; ".join(reasons[:2]) or "a guest favorite"

    def check_availability(self, sku: str) -> dict[str, Any]:
        it = self.store.find_item(sku)
        if it is None:
            return {"sku": sku, "found": False, "available": False}
        return {"sku": it.sku, "name": it.name, "available": it.available, "found": True}

    def add_item(self, sku: str, qty: int = 1, modifiers: list[str] | None = None) -> dict[str, Any]:
        it = self.store.find_item(sku)
        if it is None:
            return {"error": f"I don't have '{sku}' on the menu."}
        if not it.available:
            sub = self._substitute(it)
            return {
                "error": f"{it.name} just sold out.",
                "suggested_substitute": sub,
                "not_added": True,
            }
        line = BasketLine(sku=it.sku, name=it.name, price=it.price, qty=1 if qty <= 0 else qty, modifiers=modifiers or [])
        self.basket.append(line)
        self._record_mentions([it])
        return {"added": line.as_dict(), "basket_count": len(self.basket), "total": self.total()}

    def _substitute(self, it: MenuItem) -> dict[str, Any] | None:
        same_cat = [x for x in self.store.list_menu() if x.category == it.category and x.available and x.sku != it.sku]
        for tag in ("mild", "kids", "couple", "spicy_ok", "vegetarian"):
            for x in same_cat:
                if tag in x.fits:
                    return self._item_view(x)
        return self._item_view(same_cat[0]) if same_cat else None

    def add_items_from_mention(self, ref: str) -> dict[str, Any]:
        """Resolve pronouns 'both' / 'those two' / 'that one' / 'the first' against
        the dishes last surfaced in conversation."""
        r = (ref or "").strip().lower()
        if not self.mentioned:
            return {"clarify": "Which dishes would you like?"}
        if r in ("both", "two", "those", "those two", "both of them", "the two", "cả hai", "hết", "hai món đó"):
            targets = self.mentioned[-2:]
            if len(targets) < 2:
                last = self.mentioned[-1]
                return {"clarify": f"You mean {last['name']} — and what else?"}
        elif r in ("that one", "that", "it", "the last one", "món đó"):
            targets = [self.mentioned[-1]]
        elif r in ("the first", "first", "the first one"):
            targets = [self.mentioned[0]] if self.mentioned else []
        elif r in ("the second", "second"):
            targets = [self.mentioned[1]] if len(self.mentioned) > 1 else []
        else:
            return {"clarify": "Which dishes would you like?"}
        added: list[dict[str, Any]] = []
        for m in targets:
            res = self.add_item(m["sku"])
            if "added" in res:
                added.append(res["added"])
            else:
                added.append({"sku": m["sku"], "error": res.get("error")})
        return {"added": added, "total": self.total()}

    def set_modifier(self, line_or_sku: str, modifiers: list[str] | None) -> dict[str, Any]:
        idx = None
        try:
            idx = int(line_or_sku) - 1
            if 0 <= idx < len(self.basket):
                target = self.basket[idx]
        except (TypeError, ValueError):
            target = None
            for line in self.basket:
                if line.sku == line_or_sku or line.name.lower() == str(line_or_sku).lower():
                    target = line
                    break
        if target is None and self.basket:
            target = self.basket[-1]
        if target is None:
            return {"error": "There's nothing in the order yet."}
        target.modifiers = modifiers or []
        return {"updated": target.as_dict()}

    def remove_item(self, sku_or_name: str) -> dict[str, Any]:
        it = self.store.find_item(sku_or_name)
        sku = it.sku if it else sku_or_name
        before = len(self.basket)
        self.basket = [l for l in self.basket if l.sku != sku and l.name.lower() != str(sku_or_name).lower()]
        removed = before - len(self.basket)
        if removed == 0:
            return {"error": "That item isn't in the order."}
        return {"removed": removed, "basket_count": len(self.basket), "total": self.total()}

    def get_floor(self) -> dict[str, Any]:
        tables = self.store.list_tables()
        free = [t for t in tables if t.status == "free"]
        return {
            "free_tables": [{"id": t.id, "name": t.name, "seats": t.seats} for t in free],
            "status_counts": self.store.free_table_statuses(),
        }

    def seat_party(self, table_id: str, party_size: int) -> dict[str, Any]:
        t = self.store.seat_party(table_id, party_size)
        if t is None:
            return {"error": f"Table {table_id} isn't free or too small."}
        self.table_id = table_id
        self.party_size = party_size
        return {"seated": {"id": t.id, "name": t.name, "seats": t.seats}}

    def readback(self) -> dict[str, Any]:
        if not self.basket:
            return {"empty": True, "text": "Your order is empty."}
        lines = [f"{l.qty}x {l.name}" + (f" ({', '.join(l.modifiers)})" if l.modifiers else "") for l in self.basket]
        return {
            "empty": False,
            "lines": lines,
            "total": self.total(),
            "ticket_type": self.ticket_type,
            "eta_minutes": self.store.takeout_eta(len(self.basket)) if self.ticket_type == "takeout" else None,
            "table_id": self.table_id,
        }

    def place_order(self) -> dict[str, Any]:
        if not self.basket:
            return {"error": "The order is empty — nothing to place."}
        if self.ticket_type == "dine_in" and self.table_id is None:
            # auto-seat a table if the guest gave a party size
            free = self.store.find_free_table(self.party_size)
            if free:
                self.store.seat_party(free.id, self.party_size)
                self.table_id = free.id
        self.placed = True
        rb = self.readback()
        return {
            "placed": True,
            "ticket_id": f"LAN-{self.session_id[-6:].upper()}-{len(self.basket):02d}",
            **rb,
        }

    # ---- view --------------------------------------------------------------
    def _item_view(self, it: MenuItem) -> dict[str, Any]:
        allergens = [] if it.allergens == [ALLERGEN_FLAG] else it.allergens
        return {
            "sku": it.sku,
            "name": it.name,
            "category": it.category,
            "price": it.price,
            "spicy_level": it.spicy_level,
            "available": it.available,
            "allergens": allergens,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "basket": [l.as_dict() for l in self.basket],
            "total": self.total(),
            "ticket_type": self.ticket_type,
            "table_id": self.table_id,
            "party_size": self.party_size,
            "guest_tags": list(self.guest_tags),
            "mentioned": [dict(m) for m in self.mentioned[-6:]],
            "placed": self.placed,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Roll back to a prior snapshot (used when a turn is cancelled mid-way by
        barge-in so no half-applied tool mutations leak into the kept basket)."""
        self.basket = [
            BasketLine(
                sku=l["sku"],
                name=l["name"],
                price=l["price"],
                qty=l["qty"],
                modifiers=list(l.get("modifiers") or []),
            )
            for l in state.get("basket", [])
        ]
        self.ticket_type = state.get("ticket_type", "dine_in")
        self.table_id = state.get("table_id")
        self.party_size = state.get("party_size", 2)
        self.guest_tags = list(state.get("guest_tags") or [])
        self.mentioned = [dict(m) for m in state.get("mentioned", [])]
        self.placed = bool(state.get("placed", False))


def setup_party(session: WaiterSession, ticket_type: str | None, party_size: int | None, tags: list[str] | None) -> None:
    session.set_guest(ticket_type=ticket_type, party_size=party_size, tags=tags)
