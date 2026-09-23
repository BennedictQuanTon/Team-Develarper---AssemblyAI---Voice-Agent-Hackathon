from __future__ import annotations


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
    if action == "cancel_order" or order["status"] == "cancelled":
        return "Pedido cancelado." if language == "es" else "I cancelled your order."
    if action == "reject_substitute":
        return "No usaré ese sustituto. ¿Qué prefiere?" if language == "es" else "I won't use that substitute. What would you prefer?"
    lines = ", ".join(f"{line['quantity']} {line['name']}" for line in order["basket"])
    total = _money(order["total"])
    if language == "es":
        return f"Su pedido ahora incluye {lines}. Total: {total}. Avisé a la cocina."
    return f"Your order now includes {lines}. Total: {total}. I updated the kitchen."


def render_clarification(result: dict, language: str) -> str:
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
    return str(errors[0]) if errors else ("¿Puede aclarar su pedido?" if language == "es" else "Please clarify your order.")


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
