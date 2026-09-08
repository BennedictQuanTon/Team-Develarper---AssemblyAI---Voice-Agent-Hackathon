#!/usr/bin/env bash
# Voice demo playbook — cold cache (restart server first).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--start" ]] || [[ "${1:-}" == "--restart" ]]; then
  ./scripts/stop.sh >/dev/null 2>&1 || true
  ./scripts/start.sh
  echo
fi

cat <<'EOF'
═══════════════════════════════════════════════════════════
  KỊCH BẢN DEMO VOICE (bạn nói · cold cache)
═══════════════════════════════════════════════════════════

TRƯỚC KHI NÓI
  • Chrome + mic · English
  • Server mới restart (cache RAM trống)
  • Mở http://127.0.0.1:8000/ · meter 0 / 3 · Friendly guide
  • (Tuỳ chọn) bấm chip filler nghe thử trước

───────────────────────────────────────────────────────────
BẠN NÓI GÌ  →  ĐẦU RA KỲ VỌNG  →  LATENCY ỔN
───────────────────────────────────────────────────────────

TURN 1 — bấm Speak, nói rõ rồi dừng:

  Bạn nói:
    "When is the Dragon Bridge fire show?"

  Đầu ra ổn:
    1) Filler ~ngay (vd "let me check") — không im lặng dài
    2) Câu trả lời TTS: Saturday/Sunday · khoảng 9:00 PM
    3) Meter → 1 / 3 · sidebar có metrics + chunks
    4) spoken_cache_hit: false  (lần đầu cold)

  Latency ổn (nhìn metrics):
    • Filler cảm nhận:     < 0.5 s sau khi dứt lời
    • perceived_ttfb_ms:   ~1500–3500  (bắt đầu nghe câu trả lời)
    • rag_ms:              ~50–120
    • llm_total_ms:        ~800–2000
    • tts_total_ms:        ~1500–3500
    • e2e_turn_ms:         ~2500–5000  (cold, có Gemini+Cartesia)
    ✗ Không ổn: im >5s không filler · e2e >8–10s như trước optimize

TURN 2 — Speak lại:

  Bạn nói:
    "How do I get from the airport to the city?"

  Đầu ra ổn:
    1) Filler lại (thinking / checking)
    2) TTS: taxi / Grab · ~10–20 minutes tới city/beach
    3) Meter → 2 / 3 · chat còn turn 1

  Latency ổn: cùng band turn 1 (cold câu mới = vẫn gọi API)

TURN 3 — kết thúc:

  Bạn nói:
    "Thanks, that's all."

  Đầu ra ổn:
    1) "Anytime. Enjoy Da Nang." (local closer, không chờ Gemini)
    2) Chip Conversation ended · Speak/Send khoá
    3) Nút New conversation hiện ra
    4) providers llm/tts = local_farewell / local_closing

  Latency ổn:
    • e2e gần như tức thì  (< ~500 ms cảm nhận)
    ✗ Không ổn: chờ vài giây rồi mới closer

───────────────────────────────────────────────────────────
CHECKLIST PASS DEMO REALTIME
───────────────────────────────────────────────────────────
  [ ] Filler <0.5s mỗi turn nội dung
  [ ] perceived_ttfb khoảng 2–3.5s (không ~10s)
  [ ] Fact đúng (Dragon Bridge ngày + giờ · airport Grab/taxi)
  [ ] Turn 3 đóng sạch · New conversation reset về 0/3
  [ ] Cold: spoken_cache_hit false ở turn 1–2

FAIL → chưa demo
  • Không filler / đơ lâu
  • Sai fact / không có TTS
  • Không end sau "thanks that's all"

Lệnh:
  ./scripts/stop.sh && ./scripts/start.sh     # xoá cache RAM
  ./scripts/demo_voice_playbook.sh            # in lại kịch bản
  open http://127.0.0.1:8000/

EOF
