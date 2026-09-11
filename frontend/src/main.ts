/**
 * Main application orchestrator for The Lantern Voice Agent.
 * Rebuilt completely in TypeScript following Apple Human Interface Guidelines
 * and ThoughtStream Design System (DESIGN.md).
 */

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";

import { PCMStreamPlayer } from "./audio/pcm_player";
import { MicRecorder } from "./audio/mic_recorder";
import { VoiceService } from "./services/voice_service";
import { OpsService } from "./services/ops_service";
import { GuestView } from "./components/guest_view";
import { OpsView } from "./components/ops_view";
import { LogsView } from "./components/logs_view";

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

  private statusDot: HTMLElement;
  private statusText: HTMLElement | null = null;
  private tabGuestBtn: HTMLButtonElement;
  private tabOpsBtn: HTMLButtonElement;
  private tabLogsBtn: HTMLButtonElement;
  private viewGuestPanel: HTMLElement;
  private viewOpsPanel: HTMLElement;
  private viewLogsPanel: HTMLElement;

  private isVoiceActive: boolean = false;

  constructor() {
    // DOM Elements
    this.statusDot = document.getElementById("statusDot")!;
    this.statusText = document.getElementById("statusText");
    this.tabGuestBtn = document.getElementById("tabGuest") as HTMLButtonElement;
    this.tabOpsBtn = document.getElementById("tabOps") as HTMLButtonElement;
    this.tabLogsBtn = document.getElementById("tabLogs") as HTMLButtonElement;
    this.viewGuestPanel = document.getElementById("viewGuest")!;
    this.viewOpsPanel = document.getElementById("viewOps")!;
    this.viewLogsPanel = document.getElementById("viewLogs")!;

    // Services
    this.voiceService = new VoiceService();
    this.opsService = new OpsService();

    // Components
    this.guestView = new GuestView(this.viewGuestPanel);
    this.opsView = new OpsView(this.viewOpsPanel);
    this.logsView = new LogsView(this.viewLogsPanel);

    this.initTabs();
    this.initVoiceEvents();
    this.initOpsEvents();
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
      this.opsService.connect();
    } else {
      this.opsService.disconnect();
    }

    if (isLogs) {
      this.logsView.fetchInitialHistory();
    }
  }

  private initVoiceEvents(): void {
    this.guestView.onMicClick = async () => {
      if (this.isVoiceActive) {
        this.stopVoiceSession();
      } else {
        await this.startVoiceSession();
      }
    };

    this.voiceService.onOpen = () => {
      this.statusDot.className = "status-dot live";
      if (this.statusText) this.statusText.textContent = "Live";
      this.logsView.addEvent("ws_connected", "Connected to /ws/realtime duplex socket", "accent");
    };

    this.voiceService.onClose = () => {
      this.statusDot.className = "status-dot";
      if (this.statusText) this.statusText.textContent = "Disconnected";
      this.logsView.addEvent("ws_close", "Realtime WebSocket disconnected", "warn");
      this.stopVoiceSession();
    };

    this.voiceService.onError = () => {
      this.statusDot.className = "status-dot error";
      if (this.statusText) this.statusText.textContent = "Error";
      this.logsView.addEvent("ws_error", "WebSocket communication error", "warn");
    };

    this.voiceService.onMessage = (msg) => {
      if (msg.type === "session_ready") {
        this.guestView.setOrbMode("listen");
        this.logsView.addEvent(
          "session_ready",
          `Session ${msg.session_id.slice(0, 8)} ready (ASR: ${msg.has_assemblyai ? "AssemblyAI" : "Stub"}, TTS: ${msg.has_cartesia ? "Cartesia" : "Stub"})`,
          "info"
        );
      } else if (msg.type === "speech_started") {
        this.guestView.setOrbMode("listen");
        this.logsView.addEvent("speech_started", "Voice activity detected by AssemblyAI streaming engine", "info");
      } else if (msg.type === "interim_transcript") {
        this.guestView.showInterimTranscript(msg.text);
        this.logsView.addEvent("interim_transcript", msg.text, "info");
      } else if (msg.type === "final_transcript") {
        this.guestView.showFinalTranscript(msg.text);
        this.guestView.setOrbMode("think");
        this.logsView.addEvent("final_transcript", `User: "${msg.text}"`, "accent");
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
        this.pcmPlayer.playChunk(pcm16, 16000);
        this.guestView.setOrbMode("speak");
        if (this.ttsAnalyser) {
          this.guestView.setAnalyser(this.ttsAnalyser);
        }
        if (msg.ttfb_ms) {
          this.logsView.addEvent("audio_chunk_ttfb", `First audio chunk received · TTFB: ${Math.round(msg.ttfb_ms)}ms`, "accent");
        }
      } else if (msg.type === "turn_complete") {
        if (msg.answer) {
          this.guestView.showAgentAnswer(msg.answer);
        }
        this.logsView.handleTurnComplete(msg);
      } else if (msg.type === "waiter_action") {
        if (msg.basket) {
          this.guestView.updateBasket(msg.basket, msg.total);
        }
        this.logsView.addEvent("waiter_action", `Action: ${msg.action} · Total: $${msg.total || 0}`, "accent");
      }
    };
  }

  private async startVoiceSession(): Promise<void> {
    this.ensureAudioContext();

    try {
      await this.voiceService.connect();
      if (this.micRecorder) {
        await this.micRecorder.start();
        this.micRecorder.onAudioChunk = (pcm16) => {
          this.voiceService.sendAudioChunk(pcm16);
        };
        this.guestView.setAnalyser(this.micRecorder.analyser);
      }

      this.isVoiceActive = true;
      this.guestView.setMicActive(true);
      this.guestView.setOrbMode("listen");
      this.logsView.addEvent("mic_started", "Microphone stream active · Sampling @ 16kHz PCM16", "accent");
    } catch (err) {
      console.error("Failed to start voice session:", err);
      this.statusDot.className = "status-dot error";
      if (this.statusText) this.statusText.textContent = "Mic Error";
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
    this.voiceService.disconnect();

    this.isVoiceActive = false;
    this.guestView.setMicActive(false);
    this.guestView.setOrbMode("idle");
    this.guestView.setIdlePrompt();
    this.logsView.addEvent("mic_stopped", "Microphone stream stopped", "info");
  }

  private initOpsEvents(): void {
    this.opsService.onSnapshot = (snapshot) => {
      this.opsView.update(snapshot.floor, snapshot.menu, snapshot.metrics);
    };

    this.opsView.onToggleAvailable = (sku, available) => {
      this.opsService.setAvailable(sku, available);
      this.logsView.addEvent("inventory_toggle", `Item ${sku} set available=${available}`, "info");
    };

    this.opsView.onTableAction = (tableId, action) => {
      if (action === "seat") {
        this.opsService.seatParty(tableId, 2);
        this.logsView.addEvent("table_seated", `Party seated at ${tableId}`, "info");
      } else {
        this.opsService.clearTable(tableId);
        this.logsView.addEvent("table_cleared", `Table ${tableId} cleared`, "info");
      }
    };
  }
}

// Bootstrap once DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  new LanternApp();
});
