from __future__ import annotations

import re


ORDER_RECEIVED = {
    "en": "I sent your order to the kitchen for confirmation.",
    "es": "Envié su pedido a la cocina para confirmarlo.",
    "fr": "J'ai envoyé votre commande en cuisine pour confirmation.",
    "hi": "मैंने आपका ऑर्डर पुष्टि के लिए रसोई में भेज दिया है।",
    "it": "Ho inviato il tuo ordine in cucina per la conferma.",
    "ja": "ご注文を確認のためキッチンに送りました。",
    "zh": "我已将您的订单发送到厨房确认。",
    "pt-BR": "Enviei seu pedido para a cozinha confirmar.",
}


def immediate_acknowledgement(language: str) -> str:
    return ORDER_RECEIVED.get(language) or ORDER_RECEIVED.get(language.split("-")[0], ORDER_RECEIVED["en"])


def _money(value: float) -> str:
    return f"${value:.2f}"


def render_order_response(order: dict, action: str, language: str) -> str:
    es = language == "es"
    if action == "cancel_order" or order["status"] == "cancelled":
        return "Pedido cancelado." if es else "I cancelled your order."
    if action == "reject_substitute":
        return "No usaré ese sustituto. ¿Qué prefiere?" if es else "I won't use that substitute. What would you prefer?"
    lines = ", ".join(f"{line['quantity']} {line['name']}" for line in order["basket"])
    total = _money(order["total"])
    if action == "readback":
        return f"Su pedido tiene {lines}. Total: {total}." if es else f"Your order has {lines}. Total: {total}."
    if action == "place_order":
        if order.get("already_placed"):
            return f"Su pedido ya está en la cocina: {lines}. Total: {total}." if es else f"Your order is already with the kitchen: {lines}. Total: {total}."
        return f"Su pedido quedó registrado: {lines}. Total: {total}. Lo envié a la cocina." if es else f"Your order is in: {lines}. Total: {total}. I sent it to the kitchen."
    if not order.get("placed"):
        if es:
            return f"Su pedido ahora incluye {lines}. Total: {total}. Avíseme cuando quiera que lo envíe."
        return f"Your order now includes {lines}. Total: {total}. Tell me when you'd like me to place it."
    if es:
        return f"Su pedido ahora incluye {lines}. Total: {total}. Avisé a la cocina."
    return f"Your order now includes {lines}. Total: {total}. I updated the kitchen."


PROMPTS = {
    "confirm_cancel": ("¿Cancelo todo el pedido?", "Cancel the whole order?"),
    "confirm_place": ("¿Envío su pedido a la cocina ahora?", "Shall I place your order now?"),
    "declined_offer": ("Está bien. ¿Qué prefiere en su lugar?", "No problem. What would you like instead?"),
    "kept_order": ("De acuerdo, mantuve su pedido como está.", "Okay, I kept your order as it is."),
    "anything_else": ("Claro. ¿Qué más desea?", "Sure. What else would you like?"),
    "which_offered": ("¿Qué platos desea?", "Which dishes would you like?"),
    "nothing_pending": ("Perdón, ¿qué desea hacer?", "Sorry, what would you like to do?"),
    "no_order": ("Su pedido está vacío. ¿Qué desea?", "There's nothing in your order yet. What would you like?"),
    "clarify": ("¿Puede aclarar su pedido?", "Please clarify your order."),
}


def render_prompt(code: str, language: str) -> str:
    spanish, english = PROMPTS.get(code, PROMPTS["clarify"])
    return spanish if language == "es" else english


SKU_PATTERN = re.compile(r"\b[A-Z]+(?:_[A-Z]+)+\b")


def _safe_error(error: str, names: dict[str, str], es: bool) -> str:
    """Turn a validation or workflow error into a sentence for the guest: dish names, never SKUs."""
    skus = SKU_PATTERN.findall(error)
    fallback = "ese plato" if es else "that dish"
    name = names.get(skus[0], fallback) if skus else fallback
    if error.startswith("allergen conflict"):
        allergens = error.split(":", 1)[1].strip()
        return (f"{name} contiene {allergens}, que usted evita. ¿Desea otra cosa?" if es
                else f"{name} contains {allergens}, which you're avoiding. Would you like something else?")
    if error.startswith("unsupported modifier"):
        modifier = error.split(":", 1)[1].strip()
        return (f"No puedo preparar {name} con \"{modifier}\". ¿Lo quiere como viene?" if es
                else f"I can't do \"{modifier}\" for {name}. Would you like it as it comes?")
    if error.endswith("is not in the order"):
        return f"{name} no está en su pedido." if es else f"{name} isn't in your order."
    if error.startswith("unknown sku"):
        return ("No encontré ese plato en el menú. ¿Puede repetirlo?" if es
                else "I couldn't find that dish on the menu. Could you say it again?")
    if error in {"there is no current order", "there is no order to place"}:
        return render_prompt("no_order", "es" if es else "en")
    if error.startswith("this order is closed"):
        return "Ese pedido ya está cerrado. ¿Qué desea pedir?" if es else "That order is closed. What would you like to order?"
    if error == "the replacement is identical to the current item":
        return "Eso ya está en su pedido." if es else "That's already in your order."
    if "substitute" in error:
        return ("No tengo un sustituto pendiente. ¿Qué desea?" if es
                else "I don't have a substitute waiting. What would you like?")
    return "Perdón, no le entendí. ¿Puede repetirlo?" if es else "Sorry, I didn't catch that. Could you say it again?"


def render_clarification(result: dict, language: str, names: dict[str, str] | None = None) -> str:
    unavailable = result.get("unavailable_items") or []
    if unavailable:
        sku = unavailable[0]
        requested = result.get("requested_name") or sku
        alternatives = result.get("alternatives") or []
        if alternatives:
            suggestion = alternatives[0]["name"]
            return (f"{requested} está agotado. ¿Quiere {suggestion} en su lugar?" if language == "es"
                    else f"{requested} is sold out. Would you like {suggestion} instead?")
        return f"{requested} está agotado. ¿Qué prefiere?" if language == "es" else f"{requested} is sold out. What would you prefer?"
    errors = result.get("errors") or []
    return _safe_error(str(errors[0]), names or {}, language == "es") if errors else render_prompt("clarify", language)


def render_kitchen_decision(order: dict, decision: dict, language: str) -> str:
    action = decision["action"]
    if action == "propose_substitute":
        proposal = (decision.get("substitutions") or [{}])[0]
        from_name = proposal.get("from_name") or proposal.get("from_sku", "the item")
        to_name = proposal.get("to_name") or proposal.get("to_sku", "another item")
        return (f"La cocina no puede preparar {from_name}. ¿Acepta {to_name} en su lugar?"
                if language == "es" else f"The kitchen cannot prepare {from_name}. Would you accept {to_name} instead?")
    messages = {
        "accept": ("La cocina confirmó su pedido.", "The kitchen confirmed your order."),
        "reject": ("La cocina no puede preparar su pedido. ¿Qué prefiere?", "The kitchen cannot prepare this order. What would you prefer?"),
        "request_clarification": ("La cocina necesita más detalles sobre su pedido.", "The kitchen needs more detail about your order."),
        "mark_ready": ("Su pedido está listo.", "Your order is ready."),
        "set_eta": ("La cocina actualizó el tiempo de espera.", "The kitchen updated the wait time."),
    }
    spanish, english = messages.get(action, ("Actualización de la cocina.", "Kitchen update."))
    text = spanish if language == "es" else english
    if action == "set_eta" and decision.get("eta_minutes") is not None:
        text += f" {decision['eta_minutes']} min."
    return text
