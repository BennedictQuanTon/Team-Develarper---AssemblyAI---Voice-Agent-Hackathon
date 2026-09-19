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


def render_verified_response(order: dict, action: str) -> dict:
    return {"order_id": order["order_id"], "revision": order["current_revision"], "status": order["status"], "action": action}
