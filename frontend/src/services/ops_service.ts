/**
 * Service feeding the Operations dashboard from the V2 backend.
 *
 * - /ws/ops pushes placed orders, kitchen decisions and every guest turn (`agent_turn`).
 * - Floor and menu come from GET /floor and GET /menu, refreshed while the tab is open.
 * - Actions go to the REST API: 86 toggles, table status and kitchen decisions.
 */

import type { FloorData, MenuData, Table, TableStatus } from "../types/ops";
import type { V2Order } from "../types/realtime";

export interface AgentTurnEvent {
  type: "agent_turn";
  table_id: string;
  order_id?: string | null;
  transcript: string;
  language_code?: string;
  status: string;
  response_text: string;
  items: string[];
  total?: number | null;
  placed: boolean;
  pipeline_ms?: number | null;
  voice_ttfb_ms?: number | null;
  at: number;
}

const REFRESH_MS = 4000;

export class OpsService {
  private ws: WebSocket | null = null;
  private refreshTimer: number | null = null;

  public onSnapshot?: (floor: FloorData, menu: MenuData) => void;
  public onOrders?: (orders: V2Order[]) => void;
  public onOrder?: (order: V2Order, decision?: string) => void;
  public onAgentTurn?: (turn: AgentTurnEvent) => void;
  public onOpen?: () => void;
  public onClose?: () => void;

  public connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/ops`);

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
        if (data.type === "kitchen_snapshot" && this.onOrders) {
          this.onOrders(data.orders as V2Order[]);
        } else if ((data.type === "order_update" || data.type === "kitchen_decision") && data.order && this.onOrder) {
          this.onOrder(data.order as V2Order, data.type === "kitchen_decision" ? data.decision?.action : undefined);
        } else if (data.type === "agent_turn" && this.onAgentTurn) {
          this.onAgentTurn(data as AgentTurnEvent);
        }
      } catch (_) {
        // Ignore malformed messages
      }
    };

    void this.refresh();
    if (this.refreshTimer === null) {
      this.refreshTimer = window.setInterval(() => void this.refresh(), REFRESH_MS);
    }
  }

  /** Pull the floor and the menu; the dashboard renders them as one snapshot. */
  public async refresh(): Promise<void> {
    try {
      const [floorRes, menuRes] = await Promise.all([fetch("/floor"), fetch("/menu")]);
      if (!floorRes.ok || !menuRes.ok) return;
      const floorJson = (await floorRes.json()) as { tables: Table[] };
      const menu = (await menuRes.json()) as MenuData;
      const tables = floorJson.tables || [];
      const count = (status: TableStatus) => tables.filter((t) => t.status === status).length;
      const floor: FloorData = { tables, counts: { free: count("free"), seated: count("seated"), reserved: count("reserved") } };
      if (this.onSnapshot) this.onSnapshot(floor, menu);
    } catch (_) {
      // The next refresh retries.
    }
  }

  public async setAvailable(sku: string, available: boolean): Promise<void> {
    await this.post("/menu/set-available", { sku, available });
    await this.refresh();
  }

  public async seatParty(tableId: string): Promise<void> {
    await this.post("/floor/status", { table_id: tableId, status: "seated" });
    await this.refresh();
  }

  public async clearTable(tableId: string): Promise<void> {
    await this.post("/floor/status", { table_id: tableId, status: "free" });
    await this.refresh();
  }

  /** Kitchen decision on a placed order; the result reaches the guest's device and is spoken. */
  public async decide(order: V2Order, action: string): Promise<V2Order | null> {
    const response = await this.post(`/api/kitchen/orders/${encodeURIComponent(order.order_id)}/decisions`,
      { action, expected_revision: order.current_revision });
    return response && response.ok ? ((await response.json()) as V2Order) : null;
  }

  private async post(url: string, body: unknown): Promise<Response | null> {
    try {
      return await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (_) {
      return null;
    }
  }

  public disconnect(): void {
    if (this.refreshTimer !== null) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}
