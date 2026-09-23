from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


Action = Literal["menu_query", "recommend", "create_or_update_order", "replace_item", "remove_item", "accept_substitute", "reject_substitute", "cancel_order", "clarify"]


class IntentItem(BaseModel):
    sku: str
    quantity: int = Field(ge=1, le=50)
    modifiers: list[str] = []


class IntentProposal(BaseModel):
    source_language: str = "en"
    action: Action = "clarify"
    items: list[IntentItem] = []
    allergies: list[str] = []
    dietary_constraints: list[str] = []
    substitution_response: str | None = None
    replaces_sku: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None


class KitchenDecision(BaseModel):
    expected_revision: int
    action: Literal["accept", "reject", "request_clarification", "propose_substitute", "set_eta", "mark_ready"]
    eta_minutes: int | None = Field(default=None, ge=0, le=1440)
    substitutions: list[dict[str, Any]] = []
    reason: str | None = None
    actor: str = "kitchen"


class Revision(BaseModel):
    order_id: str
    table_id: str
    revision: int
    parent_revision: int | None
    transcript: str
    source_language: str
    items: list[dict[str, Any]]
    allergies: list[str]
    status: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
