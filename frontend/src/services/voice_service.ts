/**
 * Service managing the /ws/realtime WebSocket connection.
 *
 * Speaks the V2 contract: the socket is opened for one provisioned table (`?table_id=`), resumes
 * that table's open order after a reload (`&order_id=`), streams 16 kHz PCM up, and can send a
 * typed request when the guest taps a suggestion.
 */

import type { V2ServerMessage } from "../types/realtime";

export class VoiceService {
  private ws: WebSocket | null = null;

  public onMessage?: (msg: V2ServerMessage) => void;
  public onOpen?: () => void;
  public onClose?: () => void;
  public onError?: (error: Event) => void;

  constructor(private readonly tableId: string) {}

  private get orderKey(): string {
    return `lantern_order_${this.tableId}`;
  }

  /** Remember the table's open order so a reload resumes it; forget it once it's closed. */
  public rememberOrder(orderId: string | undefined, status: string | undefined): void {
    try {
      if (!orderId) return;
      if (status && ["cancelled", "ready", "rejected"].includes(status)) localStorage.removeItem(this.orderKey);
      else localStorage.setItem(this.orderKey, orderId);
    } catch {
      // Storage can be unavailable (private mode); the session still works without resume.
    }
  }

  public async connect(): Promise<void> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const query = new URLSearchParams({ table_id: this.tableId });
    try {
      const saved = localStorage.getItem(this.orderKey);
      if (saved) query.set("order_id", saved);
    } catch {
      // No resume without storage.
    }
    this.ws = new WebSocket(`${proto}://${location.host}/ws/realtime?${query}`);
    this.ws.binaryType = "arraybuffer";

    return new Promise((resolve, reject) => {
      if (!this.ws) return reject(new Error("WebSocket not created"));

      this.ws.onopen = () => {
        resolve();
        if (this.onOpen) this.onOpen();
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
          const msg = JSON.parse(ev.data) as V2ServerMessage;
          if (this.onMessage) this.onMessage(msg);
        } catch (_) {
          // Non-JSON or binary message
        }
      };
    });
  }

  public sendAudioChunk(pcm16: Int16Array): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(pcm16.buffer);
    }
  }

  /** A typed request takes the same path as a final transcript. */
  public sendTranscript(text: string, languageCode: string = "en"): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "transcript", text, language_code: languageCode }));
    }
  }

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  public disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
