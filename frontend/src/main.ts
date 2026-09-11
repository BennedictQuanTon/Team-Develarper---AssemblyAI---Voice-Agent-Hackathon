/**
 * Main application orchestrator for The Lantern Voice Agent.
 * Rebuilt completely in TypeScript following Apple Human Interface Guidelines.
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

class LanternApp {
  private audioCtx: AudioContext | null = null;
  private pcmPlayer: PCMStreamPlayer | null = null;
  private micRecorder: MicRecorder | null = null;
  private voiceService: VoiceService;
  private opsService: OpsService;

  private guestView: GuestView;
  private opsView: OpsView;

  private statusDot: HTMLElement;
  private statusText: HTMLElement | null = null;
  private tabGuestBtn: HTMLButtonElement;
  private tabOpsBtn: HTMLButtonElement;
  private viewGuestPanel: HTMLElement;
  private viewOpsPanel: HTMLElement;

  private isVoiceActive: boolean = false;

  constructor() {
    // DOM Elements
    this.statusDot = document.getElementById("statusDot")!;
    this.statusText = document.getElementById("statusText");
    this.tabGuestBtn = document.getElementById("tabGuest") as HTMLButtonElement;
    this.tabOpsBtn = document.getElementById("tabOps") as HTMLButtonElement;
    this.viewGuestPanel = document.getElementById("viewGuest")!;
    this.viewOpsPanel = document.getElementById("viewOps")!;

    // Services
    this.voiceService = new VoiceService();
    this.opsService = new OpsService();

    // Components
    this.guestView = new GuestView(this.viewGuestPanel);
    this.opsView = new OpsView(this.viewOpsPanel);

    this.initTabs();
    this.initVoiceEvents();
    this.initOpsEvents();
  }

  private ensureAudioContext(): AudioContext {
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioCtx = new AudioContextClass();
      this.pcmPlayer = new PCMStreamPlayer(this.audioCtx);
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
  }

  private switchTab(tab: "guest" | "ops"): void {
    const isGuest = tab === "guest";
    this.tabGuestBtn.setAttribute("aria-selected", isGuest ? "true" : "false");
    this.tabOpsBtn.setAttribute("aria-selected", !isGuest ? "true" : "false");

    this.viewGuestPanel.classList.toggle("active", isGuest);
    this.viewOpsPanel.classList.toggle("active", !isGuest);

    if (!isGuest) {
      this.opsService.connect();
    } else {
      this.opsService.disconnect();
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
    };

    this.voiceService.onClose = () => {
      this.statusDot.className = "status-dot";
      if (this.statusText) this.statusText.textContent = "Disconnected";
      this.stopVoiceSession();
    };

    this.voiceService.onError = () => {
      this.statusDot.className = "status-dot error";
      if (this.statusText) this.statusText.textContent = "Error";
    };

    this.voiceService.onMessage = (msg) => {
      if (msg.type === "session_ready") {
        this.guestView.setOrbMode("listen");
      } else if (msg.type === "speech_started") {
        this.guestView.setOrbMode("listen");
      } else if (msg.type === "interim_transcript") {
        this.guestView.showInterimTranscript(msg.text);
      } else if (msg.type === "final_transcript") {
        this.guestView.showFinalTranscript(msg.text);
        this.guestView.setOrbMode("think");
      } else if (msg.type === "barge_in") {
        // Immediate interruption
        if (this.pcmPlayer) this.pcmPlayer.stop();
        this.guestView.setOrbMode("listen");
        if (this.micRecorder) {
          this.guestView.setAnalyser(this.micRecorder.analyser);
        }
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
      } else if (msg.type === "turn_complete") {
        if (msg.answer) {
          this.guestView.showAgentAnswer(msg.answer);
        }
      } else if (msg.type === "waiter_action") {
        if (msg.basket) {
          this.guestView.updateBasket(msg.basket, msg.total);
        }
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
    } catch (err) {
      console.error("Failed to start voice session:", err);
      this.statusDot.className = "status-dot error";
      if (this.statusText) this.statusText.textContent = "Mic Error";
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
  }

  private initOpsEvents(): void {
    this.opsService.onSnapshot = (snapshot) => {
      this.opsView.update(snapshot.floor, snapshot.menu, snapshot.metrics);
    };

    this.opsView.onToggleAvailable = (sku, available) => {
      this.opsService.setAvailable(sku, available);
    };

    this.opsView.onTableAction = (tableId, action) => {
      if (action === "seat") {
        this.opsService.seatParty(tableId, 2);
      } else {
        this.opsService.clearTable(tableId);
      }
    };
  }
}

// Bootstrap once DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  new LanternApp();
});
