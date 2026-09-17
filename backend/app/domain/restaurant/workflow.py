from __future__ import annotations

import uuid
from .models import IntentProposal
from .repository import SQLiteOrderRepository
from .store import LanternStore
from .validation import validate_intent


class OrderWorkflow:
    def __init__(self, repository: SQLiteOrderRepository, store: LanternStore):
        self.repository, self.store = repository, store

    def submit(self, table_id: str, guest_session_id: str, transcript: str, intent: IntentProposal) -> dict:
        errors = validate_intent(intent, self.store)
        if errors:
            return {"status": "clarification_required", "errors": errors, "intent": intent.model_dump()}
        order_id = str(uuid.uuid4())
        return self.repository.create_revision(order_id, table_id, guest_session_id, intent.source_language, transcript, [i.model_dump() for i in intent.items], intent.allergies)
