/**
 * Main application orchestrator for The Lantern Voice Agent.
 * Rebuilt completely in TypeScript following Apple Human Interface Guidelines
 * and ThoughtStream Design System (DESIGN.md).
 *
 * The views, markup and styles are the original V1 design. This file connects them to the V2
 * runtime: /ws/realtime for one provisioned table, /ws/ops and the REST API for operations.
 */

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";

import { PCMStreamPlayer } from "./audio/pcm_player";
import { MicRecorder } from "./audio/mic_recorder";
import { VoiceService } from "./services/voice_service";
import { OpsService, type AgentTurnEvent } from "./services/ops_service";
import { GuestView } from "./components/guest_view";
import { OpsView } from "./components/ops_view";
import { LogsView } from "./components/logs_view";
import type { BasketItem, TurnTelemetry, V2BasketLine, V2Order } from "./types/realtime";

// One device per table: /?table_id=T4. Defaults to T4 for the demo.
const TABLE_ID = new URLSearchParams(location.search).get("table_id")?.trim() || "T4";

// Prerecorded Kokoro clips (tools/generate_fillers.py) played while Qwen and Kokoro work.
const FILLER_DIR = "/audio/fillers";
const FILLERS = ["recommend_en", "order_en", "check_en", "place_en", "generic_es"];
function fillerFor(transcript: string, language: string): string {
  const said = transcript.toLowerCase();
  if (language.startsWith("es") || /[¿¡ñ]|\b(quiero|quisiera|por favor|para nosotros|recomienda)\b/.test(said)) return "generic_es";
  if (/\b(recommend|suggest|what's good|what is good|popular|special)/.test(said)) return "recommend_en";
  if (/\b(place|that's all|that is all|that's it|send it|finish|done)\b/.test(said)) return "place_en";
  if (said.includes("?") || /\b(do you have|is there|allerg|gluten|how much|what's in)\b/.test(said)) return "check_en";
  return "order_en";
}

/** V2 basket lines in the shape the guest basket card renders; modifiers ride along with the name. */
function toBasketItems(lines: V2BasketLine[] | undefined): BasketItem[] {
  return (lines || []).map((line) => ({
    sku: line.sku,
    name: line.modifiers?.length ? `${line.name} · ${line.modifiers.join(", ")}` : line.name,
    price: line.unit_price,
    quantity: line.quantity,
  }));
}

interface TurnInProgress {
  transcript: string;
  answer: string;
  status: string;
  startedAt: number;
  firstAudioLogged: boolean;
  order?: Partial<V2Order>;
}

class LanternApp {
  private audioCtx: AudioContext | null = null;
  private pcmPlayer: PCMStreamPlayer | null = null;
  private micRecorder: MicRecorder | null = null;
  private ttsAnalyser: AnalyserNode | null = null;
  private voiceService: VoiceService;
  private opsService: OpsService;

  private guestView: GuestView;
  private opsView: OpsView;
  private logsView: LogsView;

  // The header status dot was removed from the design; these stay optional.
  private statusDot: HTMLElement | null;
  private statusText: HTMLElement | null = null;
  private tabGuestBtn: HTMLButtonElement;
  private tabOpsBtn: HTMLButtonElement;
  private tabLogsBtn: HTMLButtonElement;
  private viewGuestPanel: HTMLElement;
  private viewOpsPanel: HTMLElement;
  private viewLogsPanel: HTMLElement;

  private isVoiceActive: boolean = false;
  private pendingPrompt: string | null = null;
  private turn: TurnInProgress | null = null;
  private providers: { asr?: string; llm?: string; tts?: string } = {};

  constructor() {
    // DOM Elements
    this.statusDot = document.getElementById("statusDot");
    this.statusText = document.getElementById("statusText");
    this.tabGuestBtn = document.getElementById("tabGuest") as HTMLButtonElement;
    this.tabOpsBtn = document.getElementById("tabOps") as HTMLButtonElement;
    this.tabLogsBtn = document.getElementById("tabLogs") as HTMLButtonElement;
    this.viewGuestPanel = document.getElementById("viewGuest")!;
    this.viewOpsPanel = document.getElementById("viewOps")!;
    this.viewLogsPanel = document.getElementById("viewLogs")!;

    // Services
    this.voiceService = new VoiceService(TABLE_ID);
    this.opsService = new OpsService();

    // Components
    this.guestView = new GuestView(this.viewGuestPanel);
    this.opsView = new OpsView(this.viewOpsPanel);
    this.logsView = new LogsView(this.viewLogsPanel);

    this.initTabs();
    this.initVoiceEvents();
    this.initOpsEvents();
    // Operations follow the backend from the start, so switching tabs mid-demo shows live state.
    this.opsService.connect();
    window.setInterval(() => this.opsView.tick(), 30000);
  }

  private setConnectionStatus(className: string, label: string): void {
    if (this.statusDot) this.statusDot.className = className;
    if (this.statusText) this.statusText.textContent = label;
  }

  private ensureAudioContext(): AudioContext {
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioCtx = new AudioContextClass();
      this.ttsAnalyser = this.audioCtx.createAnalyser();
      this.ttsAnalyser.fftSize = 256;
      this.pcmPlayer = new PCMStreamPlayer(this.audioCtx, this.ttsAnalyser);
      this.pcmPlayer.onPlaybackEnd = () => {
        if (this.isVoiceActive) {
          this.guestView.setOrbMode("listen");
          if (this.micRecorder) {
            this.guestView.setAnalyser(this.micRecorder.analyser);
          }
        } else {
          this.guestView.setOrbMode("idle");
        }
      };
      this.micRecorder = new MicRecorder(this.audioCtx);
      for (const clip of FILLERS) {
        this.pcmPlayer.preloadClip(`${FILLER_DIR}/${clip}.wav`).catch(() => { /* the reply still plays */ });
      }
    }
    if (this.audioCtx.state === "suspended") {
      this.audioCtx.resume();
    }
    return this.audioCtx;
  }

  private initTabs(): void {
    this.tabGuestBtn.addEventListener("click", () => this.switchTab("guest"));
    this.tabOpsBtn.addEventListener("click", () => this.switchTab("ops"));
    if (this.tabLogsBtn) {
      this.tabLogsBtn.addEventListener("click", () => this.switchTab("logs"));
    }
  }

  private switchTab(tab: "guest" | "ops" | "logs"): void {
    const isGuest = tab === "guest";
    const isOps = tab === "ops";
    const isLogs = tab === "logs";

    this.tabGuestBtn.setAttribute("aria-selected", isGuest ? "true" : "false");
    this.tabOpsBtn.setAttribute("aria-selected", isOps ? "true" : "false");
    if (this.tabLogsBtn) {
      this.tabLogsBtn.setAttribute("aria-selected", isLogs ? "true" : "false");
    }

    this.viewGuestPanel.classList.toggle("active", isGuest);
    this.viewOpsPanel.classList.toggle("active", isOps);
    if (this.viewLogsPanel) {
      this.viewLogsPanel.classList.toggle("active", isLogs);
    }

    if (isOps) {
      void this.opsService.refresh();
    }
  }

  private showOrder(order: Partial<V2Order> | null | undefined): void {
    if (!order) return;
    this.guestView.updateBasket(toBasketItems(order.basket), order.total ?? undefined);
    this.voiceService.rememberOrder(order.order_id, order.status);
  }

  private initVoiceEvents(): void {
    this.guestView.onMicClick = async () => {
      if (this.isVoiceActive) {
        this.stopVoiceSession();
      } else {
        await this.startVoiceSession();
      }
    };

    // Suggestion chips send their sentence as a typed request (starting the session if needed).
    this.guestView.onPromptSelect = async (prompt: string) => {
      if (this.voiceService.isConnected()) {
        this.voiceService.sendTranscript(prompt);
      } else {
        this.pendingPrompt = prompt;
        await this.startVoiceSession();
      }
    };

    this.voiceService.onOpen = () => {
      this.setConnectionStatus("status-dot live", "Live");
      this.logsView.addEvent("ws_connected", `Connected to /ws/realtime for table ${TABLE_ID}`, "accent");
    };

    this.voiceService.onClose = () => {
      this.setConnectionStatus("status-dot", "Disconnected");
      this.logsView.addEvent("ws_close", "Realtime WebSocket disconnected", "warn");
      if (this.isVoiceActive) this.stopVoiceSession();
    };

    this.voiceService.onError = () => {
      this.setConnectionStatus("status-dot error", "Error");
      this.logsView.addEvent("ws_error", "WebSocket communication error", "warn");
    };

    this.voiceService.onMessage = (msg) => {
      if (msg.type === "session_ready") {
        this.providers = msg.providers || {};
        this.guestView.setOrbMode("listen");
        this.showOrder(msg.order);
        const connecting = msg.asr_status === "connecting";
        this.guestView.setGuestStatus(connecting ? "Connecting to speech recognition…" : "Tap a suggestion to order");
        this.logsView.addEvent(
          "session_ready",
          `Table ${msg.table_id} ready · ASR ${this.providers.asr ?? "—"} (${msg.asr_status}) · LLM ${this.providers.llm ?? "—"} · TTS ${msg.has_tts ? this.providers.tts ?? "Kokoro" : "unavailable"}${msg.order ? ` · resumed order #${msg.order.order_id.slice(0, 8)}` : ""}`,
          "info"
        );
        if (this.pendingPrompt) {
          this.voiceService.sendTranscript(this.pendingPrompt);
          this.pendingPrompt = null;
        }
      } else if (msg.type === "provider_ready") {
        this.guestView.setGuestStatus("Listening…");
        this.logsView.addEvent("provider_ready", `${msg.provider} connected · speak naturally`, "accent");
      } else if (msg.type === "provider_unavailable") {
        this.guestView.setGuestStatus("Speech unavailable · tap a suggestion");
        this.logsView.addEvent("provider_unavailable", `${msg.provider}: ${msg.message ?? "unavailable"}`, "warn");
      } else if (msg.type === "interim_transcript") {
        this.guestView.showInterimTranscript(msg.text);
        this.logsView.addEvent("interim_transcript", msg.text, "info");
      } else if (msg.type === "final_transcript") {
        this.guestView.showFinalTranscript(msg.text);
        this.guestView.setOrbMode("think");
        this.turn = { transcript: msg.text, answer: "", status: "interpreting", startedAt: performance.now(), firstAudioLogged: false };
        this.logsView.addEvent("final_transcript", `User: "${msg.text}"`, "accent");
        if (this.pcmPlayer && msg.text.trim()) {
          const clip = fillerFor(msg.text, msg.language_code ?? "en");
          this.pcmPlayer.playClip(`${FILLER_DIR}/${clip}.wav`).catch(() => { /* a missing clip never blocks the reply */ });
        }
      } else if (msg.type === "workflow_update") {
        if (msg.status === "interpreting") return;
        if (msg.response_text) this.guestView.showAgentAnswer(msg.response_text);
        if (msg.order_id) this.showOrder(msg);
        if (this.turn && msg.source !== "kitchen") {
          this.turn.answer = msg.response_text ?? "";
          this.turn.status = msg.status;
          this.turn.order = msg;
        }
        const total = msg.total != null ? ` · Total: $${Number(msg.total).toFixed(2)}` : "";
        this.logsView.addEvent(msg.source === "kitchen" ? "kitchen_update" : "workflow_update", `Status: ${msg.status}${total}`, "accent");
      } else if (msg.type === "barge_in") {
        // Immediate interruption
        if (this.pcmPlayer) this.pcmPlayer.stop();
        this.guestView.setOrbMode("listen");
        if (this.micRecorder) {
          this.guestView.setAnalyser(this.micRecorder.analyser);
        }
        this.logsView.addEvent("barge_in", msg.reason || "User interrupted speech", "warn");
      } else if (msg.type === "audio_chunk") {
        if (!this.pcmPlayer) return;
        const binStr = atob(msg.pcm_b64);
        const bytes = new Uint8Array(binStr.length);
        for (let i = 0; i < binStr.length; i++) {
          bytes[i] = binStr.charCodeAt(i);
        }
        const pcm16 = new Int16Array(bytes.buffer);
        // Kokoro streams 24 kHz; playing it at V1's fixed 16 kHz would slow and deepen the voice.
        this.pcmPlayer.playChunk(pcm16, msg.sample_rate || 24000);
        this.guestView.setOrbMode("speak");
        if (this.ttsAnalyser) {
          this.guestView.setAnalyser(this.ttsAnalyser);
        }
        if (msg.ttfb_ms && this.turn && !this.turn.firstAudioLogged) {
          this.turn.firstAudioLogged = true;
          this.logsView.addEvent("audio_chunk_ttfb", `First audio chunk received · TTFB: ${Math.round(msg.ttfb_ms)}ms`, "accent");
        }
      } else if (msg.type === "turn_complete") {
        this.logsView.handleTurnComplete(this.telemetry(msg.pipeline_ms, msg.voice_ttfb_ms));
        this.turn = null;
      } else if (msg.type === "error") {
        this.logsView.addEvent("error", msg.message ?? "Server error", "warn");
      }
    };
  }

  /** One finished turn in the Logs view's shape: what was heard, said, done, and how long it took. */
  private telemetry(pipelineMs?: number | null, voiceTtfbMs?: number | null): TurnTelemetry {
    const turn = this.turn;
    const order = turn?.order;
    return {
      turn_id: `turn-${Date.now()}`,
      transcript: turn?.transcript,
      answer: turn?.answer ?? "",
      timestamp: new Date().toISOString(),
      provider: { asr: `assemblyai ${this.providers.asr ?? ""}`, llm: this.providers.llm, tts: this.providers.tts },
      e2e_turn_ms: turn ? Math.round(performance.now() - turn.startedAt) : undefined,
      ttfb_ms: voiceTtfbMs ?? undefined,
      timings_ms: { llm_ttft_ms: pipelineMs ?? undefined },
      tool_calls: [{
        tool: `workflow · ${turn?.status ?? "done"}`,
        args: {
          table: TABLE_ID,
          order: order?.order_id ? `#${order.order_id.slice(0, 8)} rev ${order.current_revision ?? ""}`.trim() : undefined,
          items: order?.basket?.map((line) => `${line.quantity} × ${line.name}${line.modifiers?.length ? ` (${line.modifiers.join(", ")})` : ""}`),
          total: order?.total,
        },
      }],
    };
  }

  private async startVoiceSession(): Promise<void> {
    this.ensureAudioContext();

    try {
      await this.voiceService.connect();
      this.isVoiceActive = true;
      this.guestView.setMicActive(true);
      this.guestView.setOrbMode("listen");
      if (this.micRecorder) {
        try {
          await this.micRecorder.start();
          this.micRecorder.onAudioChunk = (pcm16) => {
            this.voiceService.sendAudioChunk(pcm16);
          };
          this.guestView.setAnalyser(this.micRecorder.analyser);
          this.logsView.addEvent("mic_started", "Microphone stream active · Sampling @ 16kHz PCM16", "accent");
        } catch (err) {
          // Without a microphone the suggestion chips still work.
          this.guestView.setGuestStatus("Microphone unavailable · tap a suggestion");
          this.logsView.addEvent("mic_error", String(err), "warn");
        }
      }
    } catch (err) {
      console.error("Failed to start voice session:", err);
      this.setConnectionStatus("status-dot error", "Mic Error");
      this.logsView.addEvent("mic_error", String(err), "warn");
    }
  }

  private stopVoiceSession(): void {
    if (this.micRecorder) {
      this.micRecorder.stop();
    }
    if (this.pcmPlayer) {
      this.pcmPlayer.stop();
    }
    this.isVoiceActive = false;
    this.voiceService.disconnect();

    this.guestView.setMicActive(false);
    this.guestView.setOrbMode("idle");
    this.guestView.setIdlePrompt();
    this.logsView.addEvent("mic_stopped", "Microphone stream stopped", "info");
  }

  private initOpsEvents(): void {
    this.opsService.onSnapshot = (floor, menu) => {
      this.opsView.update(floor, menu);
    };

    this.opsService.onOrders = (orders) => this.opsView.setOrders(orders);

    this.opsService.onOrder = (order, decision) => {
      this.opsView.upsertOrder(order);
      if (decision) {
        this.logsView.addEvent("kitchen_decision", `Table ${order.table_id} · ${decision.replace(/_/g, " ")} · spoken to the guest`, "accent");
      }
    };

    this.opsService.onAgentTurn = (turn: AgentTurnEvent) => {
      // This device's own turns are already logged in detail; show the other tables' turns.
      if (turn.table_id !== TABLE_ID) {
        this.logsView.addEvent("agent_turn", `Table ${turn.table_id} · "${turn.transcript}" → ${turn.status}`, "info");
      }
    };

    this.opsView.onToggleAvailable = (sku, available) => {
      void this.opsService.setAvailable(sku, available);
      this.logsView.addEvent("inventory_toggle", `Item ${sku} set available=${available}`, "info");
    };

    this.opsView.onTableAction = (tableId, action) => {
      if (action === "seat") {
        void this.opsService.seatParty(tableId);
        this.logsView.addEvent("table_seated", `Party seated at ${tableId}`, "info");
      } else {
        void this.opsService.clearTable(tableId);
        this.logsView.addEvent("table_cleared", `Table ${tableId} cleared`, "info");
      }
    };

    this.opsView.onKitchenAction = async (orderId, action) => {
      const order = await fetch(`/api/orders/${encodeURIComponent(orderId)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      if (!order) return;
      const updated = await this.opsService.decide(order as V2Order, action);
      if (updated) this.opsView.upsertOrder(updated);
      this.logsView.addEvent("kitchen_decision", `Order #${orderId.slice(0, 8)} · ${action.replace(/_/g, " ")}`, updated ? "accent" : "warn");
    };
  }
}

// Bootstrap once DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  new LanternApp();
});
