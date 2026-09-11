/**
 * Component managing the Restaurant Floor & Menu Operations view.
 * Displays live table layout and 86-inventory availability toggles.
 */

import type { FloorData, MenuData, OpsMetrics } from "../types/ops";

export class OpsView {
  private container: HTMLElement;
  private mFreeEl: HTMLElement;
  private mSeatedEl: HTMLElement;
  private m86El: HTMLElement;
  private floorGrid: HTMLElement;
  private menuList: HTMLElement;

  public onToggleAvailable?: (sku: string, available: boolean) => void;
  public onTableAction?: (tableId: string, action: "seat" | "clear") => void;

  constructor(container: HTMLElement) {
    this.container = container;

    this.mFreeEl = this.container.querySelector<HTMLElement>("#mFree")!;
    this.mSeatedEl = this.container.querySelector<HTMLElement>("#mSeated")!;
    this.m86El = this.container.querySelector<HTMLElement>("#m86")!;
    this.floorGrid = this.container.querySelector<HTMLElement>("#floorGrid")!;
    this.menuList = this.container.querySelector<HTMLElement>("#menuList")!;
  }

  public update(floor: FloorData, menu: MenuData, _metrics?: OpsMetrics): void {
    // 1. Metric Counters
    this.mFreeEl.textContent = String(floor.counts.free ?? 0);
    this.mSeatedEl.textContent = String(floor.counts.seated ?? 0);

    const offCount = (menu.items || []).filter((i) => !i.available).length;
    this.m86El.textContent = String(offCount);

    // 2. Render Floor Tables
    this.renderFloor(floor);

    // 3. Render Menu Inventory
    this.renderMenu(menu);
  }

  private renderFloor(floor: FloorData): void {
    const tables = floor.tables || [];
    this.floorGrid.innerHTML = "";

    if (tables.length === 0) {
      this.floorGrid.innerHTML = '<p style="color: var(--content-tertiary)">No tables found.</p>';
      return;
    }

    for (const table of tables) {
      const card = document.createElement("div");
      card.className = `table-card ${table.status}`;
      const statusText = table.status === "free" ? "Available" : "Occupied";
      card.innerHTML = `
        <div class="table-number">Table ${table.name}</div>
        <div class="table-seats">${table.seats} Seats</div>
        <span class="table-status-pill ${table.status}">${statusText}</span>
      `;

      card.addEventListener("click", () => {
        if (this.onTableAction) {
          const action = table.status === "free" ? "seat" : "clear";
          this.onTableAction(table.id, action);
        }
      });

      this.floorGrid.appendChild(card);
    }
  }

  private renderMenu(menu: MenuData): void {
    const items = (menu.items || []).filter((item) => item.category);
    this.menuList.innerHTML = "";

    if (items.length === 0) {
      this.menuList.innerHTML = '<p style="padding: 16px; color: var(--content-tertiary)">No menu items available.</p>';
      return;
    }

    for (const item of items) {
      const row = document.createElement("div");
      row.className = "menu-item-row";

      const left = document.createElement("div");
      left.className = "menu-item-info";
      left.innerHTML = `
        <span class="menu-item-name">${item.name}</span>
        <span class="menu-item-meta">${item.category}</span>
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
}
