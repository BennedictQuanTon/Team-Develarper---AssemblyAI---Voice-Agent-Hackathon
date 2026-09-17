/**
 * Service managing the /ws/realtime WebSocket connection.
 */

import type { ServerRealtimeMessage, ClientRealtimeCommand } from "../types/realtime";

export class VoiceService {
  private ws: WebSocket | null = null;
  private silenceTimer: number | null = null;
  private lastInterimText: string = "";

  public onMessage?: (msg: ServerRealtimeMessage) => void;
  public onOpen?: () => void;
  public onClose?: () => void;
  public onError?: (error: Event) => void;

  public async connect(): Promise<void> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${proto}://${location.host}/ws/realtime`;

    this.ws = new WebSocket(wsUrl);
    this.ws.binaryType = "arraybuffer";

    return new Promise((resolve, reject) => {
      if (!this.ws) return reject(new Error("WebSocket not created"));

      this.ws.onopen = () => {
        if (this.onOpen) this.onOpen();
        resolve();
      };

      this.ws.onclose = () => {
        if (this.onClose) this.onClose();
      };

      this.ws.onerror = (err) => {
        if (this.onError) this.onError(err);
        reject(err);
      };

      this.ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as ServerRealtimeMessage;
          this.handleIncoming(msg);
        } catch (_) {
          // Non-JSON or binary message
        }
      };
    });
  }

  private handleIncoming(msg: ServerRealtimeMessage): void {
    if (msg.type === "interim_transcript") {
      this.lastInterimText = msg.text;
      if (this.silenceTimer) clearTimeout(this.silenceTimer);
      // Client-side fallback silence timer (1.0s)
      this.silenceTimer = window.setTimeout(() => {
        if (this.lastInterimText && this.isConnected()) {
          this.sendCommand({ command: "endpoint" });
        }
      }, 1000);
    } else if (msg.type === "final_transcript" || msg.type === "barge_in") {
      if (this.silenceTimer) clearTimeout(this.silenceTimer);
      this.lastInterimText = "";
    }

    if (this.onMessage) {
      this.onMessage(msg);
    }
  }

  public sendAudioChunk(pcm16: Int16Array): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(pcm16.buffer);
    }
  }

  public sendCommand(cmd: ClientRealtimeCommand): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(cmd));
    }
  }

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  public disconnect(): void {
    if (this.silenceTimer) clearTimeout(this.silenceTimer);
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
