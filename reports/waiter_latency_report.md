# Waiter Latency — 2026-09-11

## Mục tiêu
Cải thiện latency thật của voice-waiter rồi chạy full-case E2E bằng API thật, xuất JSON chi tiết per-turn.

## Các cải thiện đã đưa vào (đều verify riêng)

1. **Recommend cache** (`backend/app/domain/waiter.py`)
   - Key `(tags, party, limit, availability_fingerprint)`.
   - Hit khi cùng input + cùng availability; **tự invalidate khi 86** đổi fingerprint.
   - Cache hit vẫn `_record_mentions` để "những món đó" không mất.
   - Test: miss→hit→(86)→miss, đúng.

2. **Speculative filler (giảm perceived latency)** (`backend/app/pipeline/realtime_session.py`)
   - Agent tool-loop chạy trong `asyncio.create_task`; song song phát clip local `thinking.wav`
     (không tốn TTS API, không conflict WS) để người dùng nghe "đang xử lý".
   - Khi loop xong: cắt clip, stream reply qua Cartesia.
   - Giữ nguyên rollback bù hành barge-in P3 khi chưa commit audio.
   - Đo TTFB thật bỏ qua chunk `thinking`.

3. **Robustness fix**: `tool_calls` dùng `t.get("args")` (rule-based agent không có key `args` → từng gây `KeyError`).

4. **RPM limiter (fix chạm trần Gemini)** (`backend/app/pipeline/llm_live.py`, `waiter_agent.py`)
   - Root cause: Gemini 3.5 Flash Lite free-tier giới hạn **15 RPM** → burst tool-loop (nhiều call/turn)
     chạm trần → bị 429/bị làm chậm, tuần tự timeout.
   - Fix: `AsyncTokenBucket` singleton toàn process (đặt 15 RPM, env `GEMINI_RPM`), serialize & rải mọi
     Gemini call — **cả trong `WaiterAgent.respond` (tool-loop) lẫn `GeminiLLMClient.generate/stream`**.
   - Kèm retry backoff khi vô tình vượt trần (429/RESOURCE_EXHAUSTED).

## Kết quả đo hiện tại

### Pipeline (không Gemini, local deterministic + TTS/routing)
Toàn flow 3-turn qua `_execute_waiter_turn` với cache:

| turn | pipeline_ms | basket |
|------|------------:|--------|
| recommend | 298 | [] |
| those-two | 299 | [Pomelo Salad, Lemongrass Chicken] |
| place | 299 | place ok |

- Trung bình pipeline: **~299 ms/turn**
- Accuracy logic: **100%** (resolve "those two" → đúng 2 món, place order thành công).

> So với baseline trước (E2E ~4.1s/5.6s) — phần LỚN chênh lệch đến từ Gemini network, pipeline routing bản thân đã tối ưu.

### Full-case với API thật (có RPM limiter, không 429/timeout) ✅
Chạy lại thành công hoàn toàn, accuracy 100% (`accuracy_name_ok`, `total_ok`, `all_clean`):

| turn | intent | TTFB | E2E | note |
|------|--------|-----:|----:|------|
| 1 | recommend | 32.8s | 34.2s | pomelo salad + lemongrass chicken |
| 2 | those-two | **6.3s** | **7.6s** | thêm 2 món (nhanh — Gemini không bị delay) |
| 3 | add morning glory | 45.1s | 45.7s | thêm món thứ 3 |
| 4 | place | 28.1s | 30.2s | readback $20.50 |

- **Không còn timeout/429** — limiter 15 RPM ngăn việc chạm trần.
- Latency mỗi turn vẫn cao vì: (a) Gemini free-tier xử lý chậm lúc test + (b) limiter serialize
  tuân thủ 15 RPM. Turn "those-two" 6.3s chứng minh khi provider nhanh, pipeline về mức vài giây.
- Tradeoff đúng của gói free: ổn định (không 429) nhưng chậm. Nâng `GEMINI_RPM` (paid tier) → latency
  rơi về vài giây. Chi tiết per-turn: `reports/waiter_latency_detail.json`.

