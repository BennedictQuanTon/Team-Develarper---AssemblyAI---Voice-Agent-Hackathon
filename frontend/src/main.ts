import { MicRecorder } from "./audio/mic_recorder";
import { PcmPlayer } from "./audio/pcm_player";
import "./styles/app.css";

type ServerMessage = Record<string, unknown> & { type: string };

const params = new URLSearchParams(location.search);
const tableId = params.get("table_id")?.trim() ?? "";
const root = document.querySelector<HTMLDivElement>("#root")!;
const recorder = new MicRecorder();
const player = new PcmPlayer();
let socket: WebSocket | null = null;

root.innerHTML = `
  <main class="shell">
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
  </main>`;

const element = (id: string) => document.querySelector<HTMLElement>(`#${id}`)!;
const button = element("toggle") as HTMLButtonElement;
function setStatus(value: string): void { element("status").textContent = value; }

async function provisioned(): Promise<boolean> {
  if (!tableId) return false;
  try {
    const response = await fetch("/floor");
    const payload = await response.json() as { tables: Array<{ table_id?: string; id?: string }> };
    return payload.tables.some((table) => (table.table_id ?? table.id) === tableId);
  } catch {
    return /^T\d+$/i.test(tableId);
  }
}

function handleMessage(message: ServerMessage): void {
  if (message.type === "session_ready") {
    setStatus("Connected — starting speech recognition…");
  } else if (message.type === "provider_ready" && message.provider === "assemblyai") {
    setStatus("Speak your order");
    recorder.start((pcm) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(pcm);
    }).catch((error: Error) => setStatus(`Microphone unavailable: ${error.message}`));
  } else if (message.type === "interim_transcript" || message.type === "final_transcript") {
    element("transcript").textContent = String(message.text ?? "");
    if (message.language_code) element("language").textContent = String(message.language_code);
  } else if (message.type === "workflow_update") {
    setStatus(message.status === "interpreting" ? "Understanding your order…" : String(message.status ?? "Kitchen update"));
  } else if (message.type === "basket_update") {
    setStatus("Waiting for kitchen verification…");
    element("response").textContent = String(message.response_text ?? "Order sent to the kitchen.");
  } else if (message.type === "audio_chunk") {
    player.enqueue(String(message.pcm_b64), Number(message.sample_rate)).catch(() => setStatus("Audio playback failed"));
  } else if (message.type === "barge_in") {
    player.stop();
    setStatus("Listening to your correction…");
  } else if (message.type === "turn_complete") {
    setStatus("Ready for your next request");
    element("latency").textContent = `pipeline ${message.pipeline_ms ?? "—"} ms · first audio ${message.voice_ttfb_ms ?? "—"} ms`;
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
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/realtime?table_id=${encodeURIComponent(tableId)}`);
  socket.binaryType = "arraybuffer";
  socket.onmessage = (event) => handleMessage(JSON.parse(event.data) as ServerMessage);
  socket.onerror = () => setStatus("Realtime connection failed");
  socket.onclose = () => { void recorder.stop(); button.textContent = "Start voice service"; socket = null; };
  button.textContent = "Stop voice service";
  setStatus("Connecting…");
}

button.addEventListener("click", () => { void (socket ? stop() : start()); });

void provisioned().then((valid) => {
  element("table").textContent = valid ? `Table ${tableId}` : "Not provisioned — use a valid ?table_id=T4 URL";
  button.disabled = !valid;
  setStatus(valid ? "Ready to connect" : "Microphone disabled");
  if (valid) localStorage.setItem("lantern_table_id", tableId);
});
