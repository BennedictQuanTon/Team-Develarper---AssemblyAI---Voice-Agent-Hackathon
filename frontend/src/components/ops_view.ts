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
  KDSTicket,
  ReservationItem,
} from "../types/ops";

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

  // Local state
  private floorData: FloorData | null = null;
  private menuData: MenuData | null = null;
  private activeZone: FloorZone = "all";
  private selectedTable: Table | null = null;
  private searchQuery: string = "";
  private selectedCategory: string = "all";

  // Mock KDS tickets with live interaction
  private kdsTickets: KDSTicket[] = [
    {
      id: "K-102",
      tableId: "T3",
      tableName: "Table 3",
      timeStarted: "7m ago",
      status: "cooking",
      server: "Julian",
      items: [
        { name: "Wagyu Ribeye Steak", quantity: 1, note: "Medium-rare, red wine jus" },
        { name: "Truffle Potato Puree", quantity: 1 },
      ],
    },
    {
      id: "K-105",
      tableId: "T6",
      tableName: "Table 6",
      timeStarted: "14m ago",
      status: "plating",
      server: "Marcus",
      items: [
        { name: "Grilled Chilean Seabass", quantity: 2, note: "Extra lemon butter" },
        { name: "Seafood Bouillabaisse", quantity: 1 },
      ],
    },
    {
      id: "K-108",
      tableId: "T8",
      tableName: "Table 8",
      timeStarted: "3m ago",
      status: "queued",
      server: "Claire",
      items: [
        { name: "Roasted Duck Breast", quantity: 2, note: "Crispy skin" },
        { name: "French Onion Velouté", quantity: 2 },
      ],
    },
  ];

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
    card.className = `floor-table-card ${table.status} ${isSelected ? "selected" : ""}`;

    const statusLabel = table.status === "free" ? "Available" : "Occupied";

    // Generate miniature visual chair dots
    let chairsHtml = "";
    const chairCount = Math.min(table.seats, 10);
    for (let i = 0; i < chairCount; i++) {
      chairsHtml += '<span class="chair-dot"></span>';
    }

    card.innerHTML = `
      <div class="table-card-top">
        <span class="table-id-tag">Table ${table.name}</span>
        <span class="table-status-badge ${table.status}">${statusLabel}</span>
      </div>
      <div class="table-center-geom">
        <div class="table-shape seats-${table.seats}">
          <span class="table-seat-count">${table.seats}p</span>
        </div>
      </div>
      <div class="table-card-chairs">
        ${chairsHtml}
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
    const allCards = this.floorMapViewport.querySelectorAll(".floor-table-card");
    allCards.forEach((c) => c.classList.remove("selected"));

    const clicked = this.floorMapViewport.querySelector(`.floor-table-card[data-id="${table.id}"]`);
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
    const allCards = this.floorMapViewport.querySelectorAll(".floor-table-card");
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
        matchCat = (item.category || "").toLowerCase().includes(this.selectedCategory.toLowerCase());
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

  private bumpTicket(ticketId: string): void {
    const idx = this.kdsTickets.findIndex((t) => t.id === ticketId);
    if (idx === -1) return;

    const current = this.kdsTickets[idx];
    if (current.status === "queued") {
      current.status = "cooking";
    } else if (current.status === "cooking") {
      current.status = "plating";
    } else if (current.status === "plating") {
      current.status = "ready";
    } else {
      // Completed, remove ticket
      this.kdsTickets.splice(idx, 1);
    }

    if (this.mKdsActiveEl) {
      this.mKdsActiveEl.textContent = String(this.kdsTickets.length);
    }
    this.renderKDS();
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
