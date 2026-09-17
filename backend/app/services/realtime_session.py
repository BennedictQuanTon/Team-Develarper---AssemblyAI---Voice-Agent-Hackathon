from __future__ import annotations

import uuid
from ..domain.restaurant.models import IntentProposal
from ..domain.restaurant.workflow import OrderWorkflow
from ..providers.llm.ollama import OllamaClient
from ..services.language_router import resolve_language


class RealtimeSession:
    def __init__(self, table_id: str, workflow: OrderWorkflow, llm: OllamaClient | None = None):
        self.table_id, self.session_id = table_id, str(uuid.uuid4())
        self.workflow, self.llm = workflow, llm

    async def handle_transcript(self, transcript: str, language: str = "en") -> dict:
        if self.llm:
            intent = await self.llm.extract_intent(transcript, {"menu": [i.as_dict() for i in self.workflow.store.list_menu(available_only=True)]})
        else:
            intent = IntentProposal(source_language=language, action="clarify", needs_clarification=True, clarification_question="Please confirm your order.")
        resolved, supported = resolve_language(intent.source_language or language)
        if intent.needs_clarification:
            return {"type": "workflow_update", "status": "clarification_required", "language_code": resolved, "tts_supported": supported, "question": intent.clarification_question}
        return self.workflow.submit(self.table_id, self.session_id, transcript, intent)
