/**
 * Component managing the Restaurant Floor & Menu Operations Dashboard.
 * Features:
 * - 2/3 Interactive Spatial Floor Map with architectural zones (Terrace, Main Hall, VIP Booths).
 * - 1/3 Real-time 86-Inventory Control with live search & category chips.
 * - Live Kitchen Display System (KDS) with cook tickets, prep timers & bump actions.
 * - Table Reservations & VIP Guest Book with instant table seating actions.
 * - Full executive KPI summary bar.
 */

import type {
  FloorData,
  MenuData,
  OpsMetrics,
  Table,
  FloorZone,
  KDSStatus,
  KDSTicket,
  ReservationItem,
} from "../types/ops";
import type { V2Order } from "../types/realtime";

// The menu's categories are "Starter", "Mains"…; the filter chips say "Appetizer", "Main".
const CATEGORY_ALIASES: Record<string, string[]> = { appetizer: ["appetizer", "starter"] };

function minutesAgo(iso?: string): string {
  if (!iso) return "just now";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return minutes < 1 ? "just now" : `${minutes}m ago`;
}

export class OpsView {
  private container: HTMLElement;
  private mFreeEl: HTMLElement;
  private mSeatedEl: HTMLElement;
  private mOccupancyEl: HTMLElement | null;
  private m86El: HTMLElement;
  private mKdsActiveEl: HTMLElement | null;

  private floorMapViewport: HTMLElement;
  private zoneTerraceTables: HTMLElement;
  private zoneMainTables: HTMLElement;
  private zoneVipTables: HTMLElement;

  private tableQuickDrawer: HTMLElement;
  private drawerTableName: HTMLElement;
  private drawerTableSeats: HTMLElement;
  private drawerTableStatus: HTMLElement;
  private drawerTableZone: HTMLElement;
  private drawerActionBtn: HTMLButtonElement;
  private drawerCloseBtn: HTMLButtonElement;

  private menuList: HTMLElement;
  private menuCountBadge: HTMLElement | null;
  private menuSearchInput: HTMLInputElement | null;
  private categoryChipsContainer: HTMLElement | null;

  private kdsTicketsList: HTMLElement | null;
  private reservationsList: HTMLElement | null;
  private resCountMeta: HTMLElement | null;

  public onToggleAvailable?: (sku: string, available: boolean) => void;
  public onTableAction?: (tableId: string, action: "seat" | "clear") => void;
  /** A KDS bump that the backend must record: "accept" (start cooking) or "mark_ready". */
  public onKitchenAction?: (orderId: string, action: "accept" | "mark_ready") => void;

  // Local state
  private floorData: FloorData | null = null;
  private menuData: MenuData | null = null;
  private activeZone: FloorZone = "all";
  private selectedTable: Table | null = null;
  private searchQuery: string = "";
  private selectedCategory: string = "all";

  // Live KDS tickets: every order the guest has placed, built from /ws/ops.
  private kdsTickets: KDSTicket[] = [];
  private orders = new Map<string, V2Order>();
  // "Plating" and "served" are kitchen-floor steps the backend doesn't track.
  private plating = new Set<string>();
  private served = new Set<string>();

  // Mock Reservations with live interaction
  private reservations: ReservationItem[] = [
    {
      id: "RES-401",
      guestName: "Arthur & Evelyn Pendelton",
      partySize: 4,
      time: "19:00",
      tableId: "T3",
      tableName: "Table 3",
      status: "seated",
      notes: "Anniversary celebration · Vintage champagne requested",
    },
    {
      id: "RES-402",
      guestName: "Dr. Elena Rostova",
      partySize: 6,
      time: "19:30",
      tableId: "T6",
      tableName: "Table 6",
      status: "arrived",
      notes: "Window terrace table requested",
    },
    {
      id: "RES-403",
      guestName: "Ambassador Chen & Delegation",
      partySize: 8,
      time: "20:00",
      tableId: "T8",
      tableName: "Table 8",
      status: "confirmed",
      notes: "Grand Cru wine pairing selection",
    },
    {
      id: "RES-404",
      guestName: "Imperial Burgundy Gala",
      partySize: 10,
      time: "20:30",
      tableId: "T10",
      tableName: "Table 10",
      status: "confirmed",
      notes: "Chef's tasting menu pre-authorized",
    },
    {
      id: "RES-405",
      guestName: "Sophia Vance",
      partySize: 2,
      time: "21:00",
      tableId: "T1",
      tableName: "Table 1",
      status: "confirmed",
      notes: "Quiet table near art gallery",
    },
  ];

  constructor(container: HTMLElement) {
    this.container = container;

    // KPI Elements
    this.mFreeEl = this.container.querySelector<HTMLElement>("#mFree")!;
    this.mSeatedEl = this.container.querySelector<HTMLElement>("#mSeated")!;
    this.mOccupancyEl = this.container.querySelector<HTMLElement>("#mOccupancy");
    this.m86El = this.container.querySelector<HTMLElement>("#m86")!;
    this.mKdsActiveEl = this.container.querySelector<HTMLElement>("#mKdsActive");

    // Floor Map Elements
    this.floorMapViewport = this.container.querySelector<HTMLElement>("#floorMapViewport")!;
    this.zoneTerraceTables = this.container.querySelector<HTMLElement>("#zoneTerraceTables")!;
    this.zoneMainTables = this.container.querySelector<HTMLElement>("#zoneMainTables")!;
    this.zoneVipTables = this.container.querySelector<HTMLElement>("#zoneVipTables")!;

    // Drawer Elements
    this.tableQuickDrawer = this.container.querySelector<HTMLElement>("#tableQuickDrawer")!;
    this.drawerTableName = this.container.querySelector<HTMLElement>("#drawerTableName")!;
    this.drawerTableSeats = this.container.querySelector<HTMLElement>("#drawerTableSeats")!;
    this.drawerTableStatus = this.container.querySelector<HTMLElement>("#drawerTableStatus")!;
    this.drawerTableZone = this.container.querySelector<HTMLElement>("#drawerTableZone")!;
    this.drawerActionBtn = this.container.querySelector<HTMLButtonElement>("#drawerActionBtn")!;
    this.drawerCloseBtn = this.container.querySelector<HTMLButtonElement>("#drawerCloseBtn")!;

    // Menu Inventory Elements
    this.menuList = this.container.querySelector<HTMLElement>("#menuList")!;
    this.menuCountBadge = this.container.querySelector<HTMLElement>("#menuCountBadge");
    this.menuSearchInput = this.container.querySelector<HTMLInputElement>("#menuSearchInput");
    this.categoryChipsContainer = this.container.querySelector<HTMLElement>("#categoryChips");

    // KDS & Reservations
    this.kdsTicketsList = this.container.querySelector<HTMLElement>("#kdsTicketsList");
    this.reservationsList = this.container.querySelector<HTMLElement>("#reservationsList");
    this.resCountMeta = this.container.querySelector<HTMLElement>("#resCountMeta");

    this.initEventListeners();
    this.renderKDS();
    this.renderReservations();
  }

  private initEventListeners(): void {
    // Zone Filter Buttons
    const zoneBtns = this.container.querySelectorAll<HTMLButtonElement>(".zone-btn");
    zoneBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        zoneBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        this.activeZone = (btn.getAttribute("data-zone") || "all") as FloorZone;
        this.filterZones();
      });
    });

    // Drawer Dismiss
    this.drawerCloseBtn.addEventListener("click", () => {
      this.closeDrawer();
    });

    // Drawer Seat/Clear Action
    this.drawerActionBtn.addEventListener("click", () => {
      if (!this.selectedTable || !this.onTableAction) return;
      const isFree = this.selectedTable.status === "free";
      const action = isFree ? "seat" : "clear";
      this.onTableAction(this.selectedTable.id, action);

      // Optimistic update
      this.selectedTable.status = isFree ? "seated" : "free";
      this.updateDrawer(this.selectedTable);
      if (this.floorData) this.renderFloor(this.floorData);
    });

    // Menu Search Input
    if (this.menuSearchInput) {
      this.menuSearchInput.addEventListener("input", (e) => {
        this.searchQuery = (e.target as HTMLInputElement).value.toLowerCase().trim();
        if (this.menuData) this.renderMenu(this.menuData);
      });
    }

    // Category Chips
    if (this.categoryChipsContainer) {
      const chips = this.categoryChipsContainer.querySelectorAll<HTMLButtonElement>(".category-chip");
      chips.forEach((chip) => {
        chip.addEventListener("click", () => {
          chips.forEach((c) => c.classList.remove("active"));
          chip.classList.add("active");
          this.selectedCategory = chip.getAttribute("data-cat") || "all";
          if (this.menuData) this.renderMenu(this.menuData);
        });
      });
    }
  }

  public update(floor: FloorData, menu: MenuData, _metrics?: OpsMetrics): void {
    this.floorData = floor;
    this.menuData = menu;

    // 1. KPI Counters
    const totalTables = floor.tables?.length || 10;
    const freeCount = floor.counts.free ?? 0;
    const seatedCount = floor.counts.seated ?? 0;
    const occPct = totalTables > 0 ? Math.round((seatedCount / totalTables) * 100) : 0;

    this.mFreeEl.textContent = String(freeCount);
    this.mSeatedEl.textContent = String(seatedCount);
    if (this.mOccupancyEl) {
      this.mOccupancyEl.textContent = `${occPct}% occ`;
    }

    const offCount = (menu.items || []).filter((i) => !i.available).length;
    this.m86El.textContent = String(offCount);

    if (this.mKdsActiveEl) {
      this.mKdsActiveEl.textContent = String(this.kdsTickets.length);
    }

    // 2. Render Spatial Floor Map
    this.renderFloor(floor);

    // 3. Render Menu Inventory
    this.renderMenu(menu);

    // Keep drawer in sync if open
    if (this.selectedTable) {
      const updated = (floor.tables || []).find((t) => t.id === this.selectedTable?.id);
      if (updated) {
        this.selectedTable = updated;
        this.updateDrawer(updated);
      }
    }
  }

  private filterZones(): void {
    const zones = this.floorMapViewport.querySelectorAll<HTMLElement>(".map-zone");
    zones.forEach((zone) => {
      const zoneType = zone.getAttribute("data-zone");
      if (this.activeZone === "all" || zoneType === this.activeZone) {
        zone.style.display = "block";
      } else {
        zone.style.display = "none";
      }
    });
  }

  private getTableZone(tableId: string): { zone: "terrace" | "main" | "vip"; name: string } {
    const num = parseInt(tableId.replace(/\D/g, ""), 10);
    if (num >= 5 && num <= 7) {
      return { zone: "terrace", name: "Window Terrace & Garden" };
    }
    if (num >= 8 && num <= 10) {
      return { zone: "vip", name: "Private VIP Wine Booths" };
    }
    return { zone: "main", name: "Main Dining Room" };
  }

  private renderFloor(floor: FloorData): void {
    const tables = floor.tables || [];

    // Clear zones
    this.zoneTerraceTables.innerHTML = "";
    this.zoneMainTables.innerHTML = "";
    this.zoneVipTables.innerHTML = "";

    for (const table of tables) {
      const { zone } = this.getTableZone(table.id);
      const node = this.createTableNode(table);

      if (zone === "terrace") {
        this.zoneTerraceTables.appendChild(node);
      } else if (zone === "vip") {
        this.zoneVipTables.appendChild(node);
      } else {
        this.zoneMainTables.appendChild(node);
      }
    }
  }

  private createTableNode(table: Table): HTMLElement {
    const card = document.createElement("div");
    const isSelected = this.selectedTable?.id === table.id;
    card.className = `floor-table-node ${table.status} ${isSelected ? "selected" : ""}`;
    card.setAttribute("data-id", table.id);

    const isFree = table.status === "free";
    const statusLabel = isFree ? "Available" : "Occupied";
    const actionHint = isFree ? "Seat Guests" : "Clear Table";

    // Miniature visual chairs
    let chairsHtml = "";
    const chairCount = Math.min(table.seats, 10);
    for (let i = 0; i < chairCount; i++) {
      chairsHtml += '<span class="chair-dot"></span>';
    }

    card.innerHTML = `
      <div class="table-node-header">
        <span class="table-node-name">Table ${table.name}</span>
        <span class="table-node-seats">${table.seats} Seats</span>
      </div>
      <div class="table-node-center">
        <div class="table-node-shape seats-${table.seats}">
          <span class="table-center-code">T${table.name}</span>
        </div>
        <div class="table-chairs-row">
          ${chairsHtml}
        </div>
      </div>
      <div class="table-node-footer">
        <span class="table-node-status-pill ${table.status}">${statusLabel}</span>
        <span class="table-action-hint">${actionHint}</span>
      </div>
    `;

    card.addEventListener("click", () => {
      this.selectTable(table);
    });

    return card;
  }

  private selectTable(table: Table): void {
    this.selectedTable = table;

    // Highlight selected card visually
    const allCards = this.floorMapViewport.querySelectorAll(".floor-table-node");
    allCards.forEach((c) => c.classList.remove("selected"));

    const clicked = this.floorMapViewport.querySelector(`.floor-table-node[data-id="${table.id}"]`);
    if (clicked) clicked.classList.add("selected");

    this.updateDrawer(table);
  }

  private updateDrawer(table: Table): void {
    const { name: zoneName } = this.getTableZone(table.id);
    this.drawerTableName.textContent = `Table ${table.name}`;
    this.drawerTableSeats.textContent = `${table.seats} Seats Capacity`;
    this.drawerTableZone.textContent = zoneName;

    const isFree = table.status === "free";
    this.drawerTableStatus.className = `table-status-pill ${table.status}`;
    this.drawerTableStatus.textContent = isFree ? "Available" : "Occupied";

    this.drawerActionBtn.textContent = isFree ? `Seat Guests (${table.seats}p)` : "Clear & Reset Table";
    this.drawerActionBtn.className = `btn-drawer-action ${isFree ? "seat" : "clear"}`;

    this.tableQuickDrawer.style.display = "flex";
  }

  private closeDrawer(): void {
    this.selectedTable = null;
    this.tableQuickDrawer.style.display = "none";
    const allCards = this.floorMapViewport.querySelectorAll(".floor-table-node");
    allCards.forEach((c) => c.classList.remove("selected"));
  }

  private renderMenu(menu: MenuData): void {
    const rawItems = menu.items || [];
    if (this.menuCountBadge) {
      this.menuCountBadge.textContent = `${rawItems.length} items`;
    }

    // Apply category & search filter
    const filtered = rawItems.filter((item) => {
      const matchSearch = !this.searchQuery || item.name.toLowerCase().includes(this.searchQuery);
      let matchCat = true;
      if (this.selectedCategory !== "all") {
        const wanted = this.selectedCategory.toLowerCase();
        const category = (item.category || "").toLowerCase();
        matchCat = (CATEGORY_ALIASES[wanted] ?? [wanted]).some((name) => category.includes(name));
      }
      return matchSearch && matchCat;
    });

    this.menuList.innerHTML = "";

    if (filtered.length === 0) {
      this.menuList.innerHTML = `
        <div class="empty-menu-state">
          <p>No dishes match your filter.</p>
        </div>
      `;
      return;
    }

    for (const item of filtered) {
      const row = document.createElement("div");
      row.className = `menu-item-row ${item.available ? "" : "is-86"}`;

      const left = document.createElement("div");
      left.className = "menu-item-info";
      left.innerHTML = `
        <span class="menu-item-name">${item.name}</span>
        <span class="menu-item-meta">${item.category || "General"}</span>
      `;

      const right = document.createElement("div");
      right.className = "menu-item-actions";

      const price = document.createElement("span");
      price.className = "menu-price";
      price.textContent = `$${item.price.toFixed(2)}`;

      const toggleBtn = document.createElement("button");
      toggleBtn.className = `btn-86 ${item.available ? "active" : "off"}`;
      toggleBtn.textContent = item.available ? "Available" : "86'd";
      toggleBtn.setAttribute("aria-label", `Toggle availability for ${item.name}`);

      toggleBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (this.onToggleAvailable) {
          this.onToggleAvailable(item.sku, !item.available);
        }
      });

      right.appendChild(price);
      right.appendChild(toggleBtn);

      row.appendChild(left);
      row.appendChild(right);
      this.menuList.appendChild(row);
    }
  }

  private renderKDS(): void {
    if (!this.kdsTicketsList) return;
    this.kdsTicketsList.innerHTML = "";

    if (this.kdsTickets.length === 0) {
      this.kdsTicketsList.innerHTML = `
        <div class="empty-kds-state">
          <p>All kitchen orders have been served. Kitchen is clear.</p>
        </div>
      `;
      return;
    }

    for (const ticket of this.kdsTickets) {
      const card = document.createElement("div");
      card.className = `kds-ticket-card status-${ticket.status}`;

      let itemsHtml = "";
      for (const item of ticket.items) {
        itemsHtml += `
          <div class="kds-dish-item">
            <span class="kds-dish-qty">${item.quantity}×</span>
            <div class="kds-dish-desc">
              <span class="kds-dish-name">${item.name}</span>
              ${item.note ? `<span class="kds-dish-note">Note: ${item.note}</span>` : ""}
            </div>
          </div>
        `;
      }

      const nextStatusLabel =
        ticket.status === "queued"
          ? "Start Cooking"
          : ticket.status === "cooking"
          ? "Mark Plating"
          : ticket.status === "plating"
          ? "Ready to Serve"
          : "Mark Served";

      card.innerHTML = `
        <div class="kds-ticket-header">
          <div class="kds-header-meta">
            <span class="kds-ticket-number">#${ticket.id}</span>
            <span class="kds-table-badge">${ticket.tableName}</span>
          </div>
          <div class="kds-header-status">
            <span class="kds-time-timer">${ticket.timeStarted}</span>
            <span class="kds-status-pill ${ticket.status}">${ticket.status}</span>
          </div>
        </div>
        <div class="kds-ticket-body">
          ${itemsHtml}
        </div>
        <div class="kds-ticket-footer">
          <span class="kds-server-tag">Server: ${ticket.server}</span>
          <button type="button" class="btn-kds-bump" data-ticket="${ticket.id}">${nextStatusLabel}</button>
        </div>
      `;

      const bumpBtn = card.querySelector<HTMLButtonElement>(".btn-kds-bump");
      if (bumpBtn) {
        bumpBtn.addEventListener("click", () => {
          this.bumpTicket(ticket.id);
        });
      }

      this.kdsTicketsList.appendChild(card);
    }
  }

  /** Replace every ticket with the placed orders from the /ws/ops snapshot. */
  public setOrders(orders: V2Order[]): void {
    this.orders.clear();
    for (const order of orders) this.orders.set(order.order_id, order);
    this.rebuildTickets();
  }

  /** One order changed (placed, edited, or decided by the kitchen). */
  public upsertOrder(order: V2Order): void {
    this.orders.set(order.order_id, order);
    this.rebuildTickets();
  }

  /** Refresh the "Xm ago" timers without new data. */
  public tick(): void {
    this.rebuildTickets();
  }

  private ticketStatus(order: V2Order): KDSStatus | null {
    if (this.served.has(order.order_id)) return null;
    if (order.status === "ready") return "ready";
    if (order.status === "committed") return this.plating.has(order.order_id) ? "plating" : "cooking";
    if (order.status === "pending_kitchen" || order.status === "substitution_proposed") return "queued";
    return null; // drafts, cancelled and rejected orders are not kitchen work
  }

  private rebuildTickets(): void {
    const tickets: KDSTicket[] = [];
    const orders = [...this.orders.values()].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
    for (const order of orders) {
      const status = this.ticketStatus(order);
      if (!status || !order.basket?.length) continue;
      const tableNumber = order.table_id.replace(/\D/g, "") || order.table_id;
      const allergy = order.allergies?.length ? `Allergy: ${order.allergies.join(", ")}` : "";
      tickets.push({
        id: order.order_id.slice(0, 6).toUpperCase(),
        tableId: order.table_id,
        tableName: `Table ${tableNumber}`,
        timeStarted: minutesAgo(order.created_at),
        status,
        server: "Lantern Voice",
        items: order.basket.map((line, index) => ({
          name: line.name,
          quantity: line.quantity,
          note: [line.modifiers?.join(", "), index === 0 ? allergy : ""].filter(Boolean).join(" · ") || undefined,
        })),
      });
    }
    this.kdsTickets = tickets;
    if (this.mKdsActiveEl) {
      this.mKdsActiveEl.textContent = String(this.kdsTickets.length);
    }
    this.renderKDS();
  }

  private bumpTicket(ticketId: string): void {
    const order = [...this.orders.values()].find((o) => o.order_id.slice(0, 6).toUpperCase() === ticketId);
    if (!order) return;
    const status = this.ticketStatus(order);

    if (status === "queued") {
      if (order.status === "substitution_proposed") return; // waiting for the guest's answer
      this.onKitchenAction?.(order.order_id, "accept"); // guest hears "The kitchen confirmed your order."
    } else if (status === "cooking") {
      this.plating.add(order.order_id);
    } else if (status === "plating") {
      this.onKitchenAction?.(order.order_id, "mark_ready"); // guest hears "Your order is ready."
    } else {
      this.served.add(order.order_id);
    }
    this.rebuildTickets();
  }

  private renderReservations(): void {
    if (!this.reservationsList) return;
    this.reservationsList.innerHTML = "";

    if (this.resCountMeta) {
      this.resCountMeta.textContent = `${this.reservations.length} Bookings Tonight`;
    }

    for (const res of this.reservations) {
      const row = document.createElement("div");
      row.className = `reservation-row ${res.status}`;

      const isSeated = res.status === "seated";

      row.innerHTML = `
        <div class="res-col-time">
          <span class="res-time-text">${res.time}</span>
          <span class="res-party-badge">${res.partySize} Guests</span>
        </div>
        <div class="res-col-guest">
          <span class="res-guest-name">${res.guestName}</span>
          <span class="res-notes-text">${res.notes || "Standard table setting"}</span>
        </div>
        <div class="res-col-table">
          <span class="res-table-tag">${res.tableName}</span>
          <span class="res-status-pill ${res.status}">${res.status}</span>
        </div>
        <div class="res-col-action">
          <button type="button" class="btn-res-action ${isSeated ? "disabled" : ""}" ${isSeated ? "disabled" : ""}>
            ${isSeated ? "Seated" : "Seat Table"}
          </button>
        </div>
      `;

      const seatBtn = row.querySelector<HTMLButtonElement>(".btn-res-action");
      if (seatBtn && !isSeated) {
        seatBtn.addEventListener("click", () => {
          res.status = "seated";
          this.renderReservations();
          if (this.onTableAction) {
            this.onTableAction(res.tableId, "seat");
          }
        });
      }

      this.reservationsList.appendChild(row);
    }
  }
}
