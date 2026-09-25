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

**Mark Crispy Squid as sold out:**

```bash
curl -s -X POST localhost:8000/menu/set-available -H 'Content-Type: application/json' -d '{"sku":"MAIN_SQUID","available":false}'
```

**Screen layout:**

| Left half | Right half |
|---|---|
| Guest tablet: `http://localhost:5173/?table_id=T4` | Kitchen: `http://localhost:5173/?view=kitchen` |

**Checklist:**
- [ ] Headset mic, quiet room. Speakers low, so the agent's voice doesn't reach the mic.
- [ ] Click **Start voice service**, allow the microphone, and wait for "Speak or type your request".
- [ ] Kitchen shows **Connection: Live** and "Waiting for a guest to speak".
- [ ] One throwaway line on **another table** (e.g. T9: "What do you recommend?") to warm everything, then reload the kitchen page so the feed is empty.
- [ ] Fresh DB file per take (the `DATABASE_PATH` above), so the board starts empty.

---

## 2. Timeline

| Time | Beat | What it proves |
|---|---|---|
| 0:00–0:25 | Hook | The problem |
| 0:25–0:45 | The two screens | Guest device + live kitchen |
| 0:45–2:45 | **Six-turn order** | Memory, references, sold-out, correction, placement |
| 2:45–3:25 | Kitchen substitute loop | Kitchen ↔ guest closed loop |
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

> "Left: a table device at table 4. Right: the kitchen. Everything runs locally: AssemblyAI
> streaming for speech, Qwen 3 4B on Ollama, Kokoro for the voice, SQLite for memory."

Point at: the empty kitchen board, and **Live table activity: Waiting for a guest to speak**.

### 0:45–2:45 · The six turns

Speak each line naturally and wait for the reply to finish. A short prerecorded filler
("Sure, one moment.") plays first; that is intentional, see §5.

| # | Guest says | Filler | Expected reply (verbatim) | Point at |
|---|---|---|---|---|
| 1 | **"What would you recommend for a mild couple?"** | "Let me see what's good tonight." | "I recommend Pomelo Salad with Shrimp ($6.50), Lemongrass Chicken ($9.00)." | Feed: **Recommendation**. The kitchen board stays empty: nothing ordered yet |
| 2 | **"We'll take those two please."** | "Sure, one moment." | "Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken. Total: $15.50. Tell me when you'd like me to place it." | "**Those two**" resolved from memory. Guest basket fills in. Feed: **Draft · not sent to the kitchen yet** |
| 3 | **"I'd like the crispy squid too."** | "Sure, one moment." | "Crispy Squid is sold out. Would you like Grilled Seabass instead?" | Live availability, real alternative. Feed: **Needs clarification** |
| 4 | **"Okay, make it a grilled seabass instead."** | "Sure, one moment." | "Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass. Total: $31.50. Tell me when you'd like me to place it." | "**Instead**" means the refused squid, so **nothing was removed**. This is the bug small models get wrong |
| 5 | **"And stir-fried morning glory on the side."** | "Sure, one moment." | "Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass, 1 Stir-fried Morning Glory. Total: $36.50. Tell me when you'd like me to place it." | **No invented modifiers** (the model proposes some; code drops what the guest didn't say) |
| 6 | **"That's all, please place the order."** | "Perfect, sending it through." | "Your order is in: 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass, 1 Stir-fried Morning Glory. Total: $36.50. I sent it to the kitchen." | **The ticket appears on the kitchen board now**, and only now. Feed card turns green |

Voice-over after turn 6:

> "Six turns, one order, $36.50, nothing invented. The model only interprets each sentence; the
> waiter's memory lives in SQLite."

### 2:45–3:25 · Kitchen substitute loop

1. **On the kitchen screen**, in the T4 ticket, choose **Stir-fried Morning Glory → Lemongrass Tofu** and click **Propose substitute**.
   - Guest device speaks: *"The kitchen cannot prepare Stir-fried Morning Glory. Would you accept Lemongrass Tofu instead?"*
   - Feed: **Kitchen: propose substitute**.
2. **Guest says: "Yes, that's fine."**
   - Reply: *"Your order now includes 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken, 1 Grilled Seabass, 1 Lemongrass Tofu. Total: $37.50. I updated the kitchen."*
3. **Kitchen clicks Accept.** Guest hears: *"The kitchen confirmed your order."*
4. **Kitchen clicks Mark ready.** Guest hears: *"Your order is ready."*

Voice-over:

> "The kitchen never talks to the guest directly. Every decision is checked against the menu,
> the guest's allergies and the latest revision, then spoken to the guest."

### 3:25–3:55 · Spanish guest

Open `http://localhost:5173/?table_id=T5`, start voice, and speak:

| Guest says | Expected reply (verbatim) |
|---|---|
| **"¿Qué nos recomienda para una pareja, algo suave?"** | "Le recomiendo Pomelo Salad with Shrimp ($6.50), Lemongrass Chicken ($9.00)." |
| **"Queremos esos dos, por favor."** | "Su pedido ahora incluye 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken. Total: $15.50. Avíseme cuando quiera que lo envíe." |
| **"Eso es todo, envíe el pedido, por favor."** | "Su pedido quedó registrado: 1 Pomelo Salad with Shrimp, 1 Lemongrass Chicken. Total: $15.50. Lo envié a la cocina." |

Point at: the kitchen gets the same English ticket for T5, whatever language the guest spoke.

### 3:55–4:35 · How it works (voice-over, with a diagram or the kitchen feed on screen)

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
| ASR mishears a line | Use the **Type a request** box with the exact line. It takes the same path |
| "Please clarify your order." | Repeat the line more slowly, or type it |
| Status stuck on "Connecting…" for more than 20 s | Click Stop, then Start voice service again. The order resumes from SQLite |
| Kitchen board shows an old order | Restart the backend with a new `DATABASE_PATH` |
| Squid gets added | You forgot the `set-available` command in §1 |

---

## 5. Honesty notes for the voice-over

- The **filler clips** ("Sure, one moment.") are prerecorded Kokoro audio played while Qwen and Kokoro work. They make the wait feel shorter. **Don't present them as response latency.** First-generated-audio numbers come from the benchmark.
- The six-turn numbers above come from **synthetic WAVs and typed input**, not a live microphone in a noisy room.
- Menu names stay in English in Spanish replies. Only the sentence around them is translated.

---

## 6. Optional beats (only if rehearsed and there's time)

- **Reload mid-order:** after turn 3, press ⌘R on the guest page and click Start again. Then say turn 4. The waiter still knows the squid was refused.
- **Barge-in:** start speaking while the waiter is reading back a long order. Playback stops and it listens. This depends on the mic and room, so rehearse it first.
