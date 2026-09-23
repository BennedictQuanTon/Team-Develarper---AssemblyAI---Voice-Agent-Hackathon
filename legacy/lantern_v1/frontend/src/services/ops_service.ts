/**
 * Service managing the /ws/ops WebSocket connection for floor & menu controls.
 */

import type { OpsSnapshot, OpsClientCommand } from "../types/ops";

export class OpsService {
  private ws: WebSocket | null = null;
  public onSnapshot?: (snapshot: OpsSnapshot) => void;
  public onOpen?: () => void;
  public onClose?: () => void;

  public connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    const proto = location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${proto}://${location.host}/ws/ops`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      if (this.onOpen) this.onOpen();
    };

    this.ws.onclose = () => {
      this.ws = null;
      if (this.onClose) this.onClose();
    };

    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "ops_snapshot" && this.onSnapshot) {
          this.onSnapshot(data as OpsSnapshot);
        }
      } catch (_) {
        // Ignore non-snapshot messages
      }
    };
  }

  public setAvailable(sku: string, available: boolean): void {
    this.sendCommand({ command: "set-available", sku, available });
  }

  public seatParty(tableId: string, partySize: number = 2): void {
    this.sendCommand({ command: "seat", table_id: tableId, party_size: partySize });
  }

  public clearTable(tableId: string): void {
    this.sendCommand({ command: "clear", table_id: tableId });
  }

  private sendCommand(cmd: OpsClientCommand): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(cmd));
    }
  }

  public disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}
