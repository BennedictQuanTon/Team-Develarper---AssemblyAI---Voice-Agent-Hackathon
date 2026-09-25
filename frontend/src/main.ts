import { MicRecorder } from "./audio/mic_recorder";
import { PcmPlayer } from "./audio/pcm_player";
import "./styles/app.css";

type ServerMessage = Record<string, unknown> & { type: string };
type BasketLine = { sku: string; name: string; quantity: number; line_total: number; modifiers?: string[] };
type Order = { order_id: string; table_id: string; status: string; current_revision: number; basket: BasketLine[]; total: number; allergies?: string[] };
type MenuItem = { sku: string; name: string; category: string; available: boolean };
type AgentTurn = { table_id: string; transcript: string; status: string; response_text: string; total: number | null;
  placed: boolean; pipeline_ms: number | null; voice_ttfb_ms: number | null; at: number };

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft · not sent to the kitchen yet",
  pending_kitchen: "New · waiting for the kitchen",
  committed: "Accepted",
  substitution_proposed: "Substitute proposed",
  clarification_required: "Needs clarification",
  awaiting_reply: "Waiting for the guest's answer",
  recommend: "Recommendation",
  menu_query: "Menu question",
  ready: "Ready",
  rejected: "Rejected",
  cancelled: "Cancelled",
};
const label = (status: string): string => STATUS_LABELS[status] ?? status;

// Prerecorded Kokoro clips (tools/generate_fillers.py) played while Qwen and Kokoro work on the answer.
const FILLER_DIR = "/audio/fillers";
function fillerFor(transcript: string, language: string): string {
  const said = transcript.toLowerCase();
  if (language.startsWith("es") || /[¿¡ñ]|\b(quiero|quisiera|por favor|para nosotros|recomienda)\b/.test(said)) return "generic_es";
  if (/\b(recommend|suggest|what's good|what is good|popular|special)/.test(said)) return "recommend_en";
  if (/\b(place|that's all|that is all|that's it|send it|finish|done)\b/.test(said)) return "place_en";
  if (said.includes("?") || /\b(do you have|is there|allerg|gluten|how much|what's in)\b/.test(said)) return "check_en";
  return "order_en";
}

const params = new URLSearchParams(location.search);
const tableId = params.get("table_id")?.trim() ?? "";
const root = document.querySelector<HTMLDivElement>("#root")!;
const wsUrl = (path: string): string => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
const text = (id: string, value: string): void => { document.getElementById(id)!.textContent = value; };
const money = (value: number): string => `$${value.toFixed(2)}`;

function basket(container: HTMLElement, lines: BasketLine[], total: number): void {
  container.replaceChildren();
  for (const line of lines) {
    const row = document.createElement("div");
    row.className = "row";
    const name = document.createElement("strong");
    name.textContent = `${line.quantity} × ${line.name}`;
    if (line.modifiers?.length) {
      const modifiers = document.createElement("small");
      modifiers.className = "modifiers";
      modifiers.textContent = line.modifiers.join(" · ");
      name.append(modifiers);
    }
    const amount = document.createElement("span");
    amount.textContent = money(line.line_total);
    row.append(name, amount);
    container.append(row);
  }
  const totalRow = document.createElement("div");
  totalRow.className = "row total";
  const totalLabel = document.createElement("strong");
  totalLabel.textContent = "Total";
  const amount = document.createElement("strong");
  amount.textContent = money(total);
  totalRow.append(totalLabel, amount);
  container.append(totalRow);
}

function allergyBanner(allergies: string[] | undefined): HTMLElement | null {
  if (!allergies?.length) return null;
  const banner = document.createElement("p");
  banner.className = "allergy";
  banner.textContent = `Allergy: ${allergies.join(", ")}`;
  return banner;
}

if (params.get("view") === "kitchen") {
  kitchenView();
} else {
  guestView();
}

function guestView(): void {
  const recorder = new MicRecorder();
  const player = new PcmPlayer();
  const orderKey = `lantern_order_${tableId}`;
  let socket: WebSocket | null = null;
  root.innerHTML = `
    <main class="shell">
      <nav><a href="?view=kitchen">Kitchen dashboard</a></nav>
      <header><span class="eyebrow">Multilingual table service</span><h1>The Lantern</h1></header>
      <section class="card">
        <div class="row"><strong>Device</strong><span id="table">Checking provisioning…</span></div>
        <div class="row"><strong>Status</strong><span id="status">Idle</span></div>
        <div class="row"><strong>Language</strong><span id="language">—</span></div>
        <div class="row"><strong>Latency</strong><span id="latency">—</span></div>
        <button id="toggle" disabled>Start voice service</button>
      </section>
      <section class="card transcript"><h2>Live transcript</h2><p id="transcript">Your speech will appear here.</p></section>
      <section class="card"><h2>Verified response</h2><p id="response">Waiting for an order.</p></section>
      <section class="card"><h2>Your order</h2><p id="order-status">No active order</p><div id="basket"></div></section>
      <section class="card"><h2>Type a request</h2><p>Use this when speech recognition is unavailable.</p><form id="manual"><input id="request" aria-label="Order request" placeholder="e.g. Add one sea bass" required /><button type="submit">Send request</button></form></section>
    </main>`;
  const button = document.getElementById("toggle") as HTMLButtonElement;
  const form = document.getElementById("manual") as HTMLFormElement;
  const input = document.getElementById("request") as HTMLInputElement;
  const setStatus = (value: string): void => text("status", value);

  function showOrder(order: Order | null): void {
    if (order) {
      localStorage.setItem(orderKey, order.order_id);
      text("order-status", `#${order.order_id.slice(0, 8)} · ${label(order.status)} · revision ${order.current_revision}`);
      const container = document.getElementById("basket")!;
      basket(container, order.basket, order.total);
      const banner = allergyBanner(order.allergies);
      if (banner) container.prepend(banner);
      if (["cancelled", "ready", "rejected"].includes(order.status)) localStorage.removeItem(orderKey);
    } else {
      localStorage.removeItem(orderKey);
      text("order-status", "No active order");
      document.getElementById("basket")!.replaceChildren();
    }
  }

  function handleMessage(message: ServerMessage): void {
    if (message.type === "session_ready") {
      setStatus("Connected — you can type a request now");
      showOrder((message.order as Order | null) ?? null);
    } else if (message.type === "provider_ready" && message.provider === "assemblyai") {
      setStatus("Speak or type your request");
      recorder.start((pcm) => { if (socket?.readyState === WebSocket.OPEN) socket.send(pcm); })
        .catch((error: Error) => setStatus(`Microphone unavailable: ${error.message}; typing is available`));
    } else if (message.type === "interim_transcript" || message.type === "final_transcript") {
      text("transcript", String(message.text ?? ""));
      if (message.language_code) text("language", String(message.language_code));
      if (message.type === "final_transcript" && String(message.text ?? "").trim()) {
        const clip = fillerFor(String(message.text), String(message.language_code ?? "en"));
        player.enqueueClip(`${FILLER_DIR}/${clip}.wav`).catch(() => { /* a missing filler must never block the reply */ });
      }
    } else if (message.type === "workflow_update" || message.type === "basket_update") {
      setStatus(message.status === "interpreting" ? "Understanding your request…" : label(String(message.status ?? "Order updated")));
      if (message.response_text) text("response", String(message.response_text));
      if (message.order_id) {
        showOrder({ order_id: String(message.order_id), table_id: tableId, status: String(message.status),
          current_revision: Number(message.current_revision ?? message.revision ?? 0),
          basket: (message.basket as BasketLine[]) ?? [], total: Number(message.total ?? 0),
          allergies: (message.allergies as string[]) ?? [] });
      }
    } else if (message.type === "audio_chunk") {
      player.enqueue(String(message.pcm_b64), Number(message.sample_rate)).catch(() => setStatus("Audio playback failed"));
    } else if (message.type === "barge_in") {
      player.stop();
      setStatus("Listening to your correction…");
    } else if (message.type === "turn_complete") {
      setStatus("Ready for your next request");
      text("latency", `pipeline ${message.pipeline_ms ?? "—"} ms · first audio ${message.voice_ttfb_ms ?? "—"} ms`);
    } else if (message.type === "provider_unavailable" || message.type === "error") {
      setStatus(`${message.provider ?? "Service"}: ${message.message ?? "unavailable"}`);
    }
  }

  async function stop(): Promise<void> {
    player.stop();
    await recorder.stop();
    socket?.close();
    socket = null;
    button.textContent = "Start voice service";
    setStatus("Stopped");
  }

  async function start(): Promise<void> {
    await player.resume();
    for (const clip of ["recommend_en", "order_en", "check_en", "place_en", "generic_es"]) {
      player.preload(`${FILLER_DIR}/${clip}.wav`).catch(() => { /* the reply still plays without a filler */ });
    }
    const query = new URLSearchParams({ table_id: tableId });
    const saved = localStorage.getItem(orderKey);
    if (saved) query.set("order_id", saved);
    socket = new WebSocket(wsUrl(`/ws/realtime?${query}`));
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event) => handleMessage(JSON.parse(event.data) as ServerMessage);
    socket.onerror = () => setStatus("Realtime connection failed");
    socket.onclose = () => { void recorder.stop(); button.textContent = "Start voice service"; socket = null; };
    button.textContent = "Stop voice service";
    setStatus("Connecting…");
  }

  button.addEventListener("click", () => { void (socket ? stop() : start()); });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (socket?.readyState !== WebSocket.OPEN) { setStatus("Connect first, then send your request"); return; }
    const request = input.value.trim();
    if (!request) return;
    socket.send(JSON.stringify({ type: "transcript", text: request, language_code: "en" }));
    input.value = "";
  });
  if (!tableId) { text("table", "Use ?table_id=T4 in the URL"); setStatus("Table ID required"); return; }
  void fetch("/floor").then((response) => response.json()).then((payload: { tables: Array<{ table_id?: string; id?: string }> }) => {
    const valid = payload.tables.some((table) => (table.table_id ?? table.id) === tableId);
    text("table", valid ? `Table ${tableId}` : "Unknown table");
    button.disabled = !valid;
    setStatus(valid ? "Ready to connect" : "Table ID not provisioned");
  }).catch(() => setStatus("Could not verify table provisioning"));
}

function kitchenView(): void {
  const orders = new Map<string, Order>();
  const activity: AgentTurn[] = [];
  let menu: MenuItem[] = [];
  let socket: WebSocket | null = null;
  root.innerHTML = `
    <main class="shell wide">
      <nav><a href="?table_id=${encodeURIComponent(tableId || "T4")}">Guest view</a></nav>
      <header><span class="eyebrow">Live operations</span><h1>Kitchen dashboard</h1></header>
      <section class="card"><div class="row"><strong>Connection</strong><span id="status">Connecting…</span></div><p id="notice"></p></section>
      <div class="ops">
        <div><h2 class="lane">Kitchen tickets <small>placed orders only</small></h2><div id="orders" class="order-grid"></div></div>
        <aside><h2 class="lane">Live table activity <small>every guest turn</small></h2><ol id="activity" class="activity"></ol></aside>
      </div>
    </main>`;

  function renderActivity(): void {
    const list = document.getElementById("activity")!;
    list.replaceChildren();
    if (!activity.length) { const empty = document.createElement("li"); empty.textContent = "Waiting for a guest to speak."; list.append(empty); }
    for (const turn of activity) {
      const item = document.createElement("li");
      item.className = `turn ${turn.placed ? "placed" : ""}`;
      const head = document.createElement("div");
      head.className = "turn-head";
      const where = document.createElement("strong");
      where.textContent = `Table ${turn.table_id} · ${new Date(turn.at * 1000).toLocaleTimeString()}`;
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = label(turn.status);
      head.append(where, badge);
      const heard = document.createElement("p");
      heard.className = "heard";
      heard.textContent = `“${turn.transcript}”`;
      const reply = document.createElement("p");
      reply.textContent = turn.response_text;
      const timing = document.createElement("small");
      const total = turn.total != null ? ` · ${money(turn.total)}` : "";
      timing.textContent = turn.pipeline_ms == null ? `kitchen action${total}`
        : `agent ${Math.round(turn.pipeline_ms)} ms · first audio ${turn.voice_ttfb_ms != null ? Math.round(turn.voice_ttfb_ms) + " ms" : "—"}${total}`;
      item.append(head, heard, reply, timing);
      list.append(item);
    }
  }

  function render(): void {
    const container = document.getElementById("orders")!;
    container.replaceChildren();
    const active = [...orders.values()].sort((a, b) => b.current_revision - a.current_revision);
    if (!active.length) { const empty = document.createElement("p"); empty.textContent = "No orders yet."; container.append(empty); }
    for (const order of active) {
      const card = document.createElement("section");
      card.className = "card";
      const title = document.createElement("h2");
      title.textContent = `Table ${order.table_id} · #${order.order_id.slice(0, 8)}`;
      const status = document.createElement("p");
      status.textContent = `${label(order.status)} · revision ${order.current_revision}`;
      const lines = document.createElement("div");
      basket(lines, order.basket, order.total);
      card.append(title, status);
      const banner = allergyBanner(order.allergies);
      if (banner) card.append(banner);
      card.append(lines);
      if (!["cancelled", "ready", "rejected"].includes(order.status)) {
        const actions = document.createElement("div");
        actions.className = "actions";
        for (const [label, action] of [["Accept", "accept"], ["Reject", "reject"], ["Mark ready", "mark_ready"]]) {
          const button = document.createElement("button");
          button.textContent = label;
          button.addEventListener("click", () => { void decide(order, action); });
          actions.append(button);
        }
        card.append(actions);
        const original = document.createElement("select");
        original.setAttribute("aria-label", "Item to replace");
        for (const line of order.basket) original.add(new Option(line.name, line.sku));
        const replacement = document.createElement("select");
        replacement.setAttribute("aria-label", "Replacement item");
        for (const item of menu.filter((item) => item.available)) replacement.add(new Option(item.name, item.sku));
        const propose = document.createElement("button");
        propose.textContent = "Propose substitute";
        propose.disabled = !order.basket.length || !replacement.options.length;
        propose.addEventListener("click", () => { void decide(order, "propose_substitute", [{ from_sku: original.value, to_sku: replacement.value }]); });
        const replacementRow = document.createElement("div");
        replacementRow.className = "replacement";
        replacementRow.append(original, replacement, propose);
        card.append(replacementRow);
      }
      container.append(card);
    }
  }

  async function decide(order: Order, action: string, substitutions: Array<{ from_sku: string; to_sku: string }> = []): Promise<void> {
    try {
      const response = await fetch(`/api/kitchen/orders/${encodeURIComponent(order.order_id)}/decisions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, expected_revision: order.current_revision, substitutions }),
      });
      const payload = await response.json();
      if (!response.ok) { text("notice", `Decision failed: ${JSON.stringify(payload.detail ?? payload)}`); return; }
      orders.set(order.order_id, payload as Order);
      text("notice", `${action} recorded for table ${order.table_id}`);
      render();
    } catch (error) { text("notice", `Decision failed: ${String(error)}`); }
  }

  renderActivity();
  void fetch("/menu").then((response) => response.json()).then((payload: { items: MenuItem[] }) => { menu = payload.items; render(); });
  function connect(): void {
    socket = new WebSocket(wsUrl("/ws/ops"));
    socket.onopen = () => text("status", "Live");
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as ServerMessage;
      if (message.type === "kitchen_snapshot") {
        orders.clear();
        for (const order of message.orders as Order[]) orders.set(order.order_id, order);
      } else if ((message.type === "order_update" || message.type === "kitchen_decision") && message.order) {
        const order = message.order as Order;
        orders.set(order.order_id, order);
        if (message.type === "kitchen_decision") {
          const decision = (message.decision as { action?: string } | undefined)?.action ?? "decision";
          activity.unshift({ table_id: order.table_id, transcript: `Kitchen: ${decision.replace(/_/g, " ")}`, status: order.status,
            response_text: "Sent to the guest's device and spoken in their language.", total: order.total, placed: true,
            pipeline_ms: null, voice_ttfb_ms: null, at: Date.now() / 1000 });
          activity.splice(12);
          renderActivity();
        }
      } else if (message.type === "agent_turn") {
        activity.unshift(message as unknown as AgentTurn);
        activity.splice(12);
        renderActivity();
        return;
      }
      render();
    };
    socket.onclose = () => { text("status", "Disconnected — reconnecting…"); setTimeout(connect, 2000); };
    socket.onerror = () => text("status", "Connection error");
  }
  connect();
}
