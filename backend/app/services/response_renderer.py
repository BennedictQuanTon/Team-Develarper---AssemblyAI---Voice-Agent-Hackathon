from __future__ import annotations


def render_verified_response(order: dict, action: str) -> dict:
    return {"order_id": order["order_id"], "revision": order["current_revision"], "status": order["status"], "action": action}
