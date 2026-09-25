# The Lantern: 5-minute demo script

**What the video has to prove:** a guest can hold a long, messy ordering conversation with a
local voice waiter. The waiter remembers what was said, never invents a dish or a price, handles
a sold-out item and a kitchen substitute, speaks the guest's language, and the kitchen sees every
step live.

Every expected reply below is **verbatim from a rehearsal on the live stack** (qwen3:4b, Kokoro,
SQLite, `/ws/realtime`, `/ws/ops`) on 2026-09-25. Latency varies by machine, so rehearse on the
demo machine first.

---

## 1. Before recording (10 minutes)

**Terminals, one command each:**

```bash
ollama serve
```
```bash
cd ~/Downloads/Everything/Github/Team-Develarper---AssemblyAI---Voice-Agent-Hackathon && DATABASE_PATH=var/demo-$(date +%H%M).sqlite3 .venv/bin/python -m uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8000
```
```bash
cd ~/Downloads/Everything/Github/Team-Develarper---AssemblyAI---Voice-Agent-Hackathon/frontend && npm run dev
```

**Wait until the backend is ready** (Kokoro takes 10–20 s to load):

```bash
curl -s localhost:8000/ready
```

It must show `"ready":true`, with `llm_warm` and `tts_warm` both `true`.

**Screen layout** (two browser windows, same app):

| Left half | Right half |
|---|---|
| Table device: `http://localhost:5173/?table_id=T4`, **Dining** tab | Staff: `http://localhost:5173/?table_id=T9`, **Management** tab |

The staff window uses another table ID so its own session never mixes with table 4.

**Checklist:**
- [ ] Headset mic, quiet room. Speakers low, so the agent's voice doesn't reach the mic.
- [ ] **Crispy Squid must be available at the start.** You mark it 86'd on camera (0:25).
- [ ] Left window: tap the microphone, allow access, and wait until the status above the guest card reads **Listening…**.
- [ ] Right window, Management: **Active kitchen tickets 0**, and the Kitchen Display System says the kitchen is clear.
- [ ] One throwaway line on **another table** (e.g. T8: "What do you recommend?") to warm Qwen and Kokoro.
- [ ] Fresh DB file per take (the `DATABASE_PATH` above), so the KDS starts empty.

---

## 2. Timeline

| Time | Beat | What it proves |
|---|---|---|
| 0:00–0:25 | Hook | The problem |
| 0:25–0:45 | The two screens + 86 the squid | Guest device, live operations |
| 0:45–2:45 | **Six-turn order** | Memory, references, sold-out, correction, placement |
| 2:45–3:25 | Kitchen Display loop | Kitchen ↔ guest closed loop |
| 3:25–3:55 | Spanish guest | Multilingual |
| 3:55–4:35 | How it works + numbers | Local SLM + code-owned memory |
| 4:35–5:00 | Close | |

---

## 3. Script

### 0:00–0:25 · Hook (voice-over)

> "Small local models forget. Ask one to take a six-step restaurant order and it adds the wrong
> dishes, drops the side, or cancels when you say 'place the order'. We measured that: zero out of
> twelve. The Lantern fixes it without a bigger model."

### 0:25–0:45 · The two screens (voice-over)

> "Left: a table device at table 4. Right: the restaurant's operations view. Everything runs
> locally: AssemblyAI streaming for speech, Qwen 3 4B on Ollama, Kokoro for the voice, SQLite for
> memory. The kitchen just ran out of squid…"

**On camera, right window:** in **Menu & 86'd Stock**, type `squid` in the search box and click
**Available**. It flips to **86'd**, and the KPI **86'd Out of Stock** becomes 1.

### 0:45–2:45 · The six turns

Speak each line naturally and wait for the reply to finish. A short prerecorded filler
("Sure, one moment.") plays first; that is intentional, see §5.

| # | Guest says | Filler | Expected reply (verbatim) | Point at |
|---|---|---|---|---|
| 1 | **"What would you recommend for a mild couple?"** | "Let me see what's good tonight." | "I recommend Pomelo Salad with Shrimp ($6.50), Lemongrass Chicken ($9.00)." | The orb thinks, then speaks. Nothing reaches the KDS: nothing is ordered yet |
| 2 | **"We'll take those two please."** | "Sure, one moment." | "Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken. Total: $15.50. Tell me when you'd like me to place it." | "**Those two**" resolved from memory. **Your Table Order** appears. Still a draft: the KDS stays empty |
| 3 | **"I'd like the crispy squid too."** | "Sure, one moment." | "Crispy Squid is sold out. Would you like Grilled Seabass instead?" | The squid you 86'd a minute ago, refused live, with a real alternative |
| 4 | **"Okay, make it a grilled seabass instead."** | "Sure, one moment." | "Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass. Total: $31.50. Tell me when you'd like me to place it." | "**Instead**" means the refused squid, so **nothing was removed**. This is the bug small models get wrong |
| 5 | **"And stir-fried morning glory on the side."** | "Sure, one moment." | "Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass, 1 Stir-fried Morning Glory. Total: $36.50. Tell me when you'd like me to place it." | **No invented modifiers** (the model proposes some; code drops what the guest didn't say) |
| 6 | **"That's all, please place the order."** | "Perfect, sending it through." | "Your order is in: 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass, 1 Stir-fried Morning Glory. Total: $36.50. I sent it to the kitchen." | **Right window: the ticket appears in the KDS now**, and only now. Active kitchen tickets: 1 |

Voice-over after turn 6:

> "Six turns, one order, $36.50, nothing invented. The model only interprets each sentence; the
> waiter's memory lives in SQLite."

### 2:45–3:25 · Kitchen Display loop

In the right window, the **Kitchen Display System** now holds the table 4 ticket: 4 dishes,
status **QUEUED**.

1. Click **Start Cooking**. The ticket moves to **COOKING**, and the table device speaks: *"The kitchen confirmed your order."*
2. Click **Mark Plating**. The ticket moves to **PLATING**; this step is shown to the kitchen only.
3. Click **Ready to Serve**. The ticket moves to **READY**, and the table device speaks: *"Your order is ready."*
4. Click **Mark Served**. The ticket leaves the board.

Voice-over:

> "The kitchen never talks to the guest directly. Each step is recorded against the order's
> latest revision and spoken on the guest's device."

Optional, if time allows: open the **Logs** tab to show the latest turn's latency breakdown and
workflow state change.

### 3:25–3:55 · Spanish guest

Open `http://localhost:5173/?table_id=T5` in the left window, tap the microphone, and speak:

| Guest says | Expected reply (verbatim) |
|---|---|
| **"¿Qué nos recomienda para una pareja, algo suave?"** | "Le recomiendo Pomelo Salad with Shrimp ($6.50), Lemongrass Chicken ($9.00)." |
| **"Queremos esos dos, por favor."** | "Su pedido ahora incluye 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken. Total: $15.50. Avíseme cuando quiera que lo envíe." |
| **"Eso es todo, envíe el pedido, por favor."** | "Su pedido quedó registrado: 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken. Total: $15.50. Lo envié a la cocina." |

Point at: the KDS gets the same English ticket for Table 5, whatever language the guest spoke.

### 3:55–4:35 · How it works (voice-over, with a diagram or the Logs tab on screen)

> "Every turn, Qwen gets a prompt of the same size: rules, the menu, and a short state block.
> It returns one schema-checked intent. Code resolves 'those two' and 'instead', validates the
> dish, modifiers and allergies, and writes a new SQLite revision. So the conversation can run for
> fifty turns without the context window growing."

**Numbers you can cite** (from our own benchmarks, #26 and #39):

- Old architecture, local model: **0 of 12** six-turn orders correct.
- Now: **10 of 10** direct runs with live Qwen, and **5 of 5** full voice sessions through AssemblyAI. RTX 3060, Tường's benchmark.
- The six turns also survive a page reload mid-order (3/3).

### 4:35–5:00 · Close

> "The Lantern: a local voice waiter that remembers the whole table, never invents a dish, and
> keeps the kitchen in the loop, in the guest's language."

---

## 4. If something goes wrong on camera

| Symptom | What to do |
|---|---|
| ASR mishears a line | Repeat it more slowly. Record each turn as its own clip so a retake is cheap |
| "Please clarify your order." | Repeat the line more slowly |
| Status stuck on "Connecting to speech recognition…" for more than 20 s | Tap the microphone to stop, then tap again. The order resumes from SQLite |
| The KDS shows an old ticket | Restart the backend with a new `DATABASE_PATH` |
| Squid gets added | It wasn't 86'd. Toggle it in **Menu & 86'd Stock** before turn 3 |
| No sound from the waiter | Click once anywhere on the page first, since browsers block audio until a click. Check the system output device |

---

## 5. Honesty notes for the voice-over

- The **filler clips** ("Sure, one moment.") are prerecorded Kokoro audio played while Qwen and Kokoro work. They make the wait feel shorter. **Don't present them as response latency.** First-generated-audio numbers come from the benchmark.
- The six-turn numbers above come from **synthetic WAVs and typed input**, not a live microphone in a noisy room.
- Menu names stay in English in Spanish replies. Only the sentence around them is translated.

---

## 6. Optional beats (only if rehearsed and there's time)

- **Reload mid-order:** after turn 3, press ⌘R on the guest page and click Start again. Then say turn 4. The waiter still knows the squid was refused.
- **Barge-in:** start speaking while the waiter is reading back a long order. Playback stops and it listens. This depends on the mic and room, so rehearse it first.
