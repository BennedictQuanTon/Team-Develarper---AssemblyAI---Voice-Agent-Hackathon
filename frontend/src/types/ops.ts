/**
 * Type-safe contract for the /ws/ops restaurant floor & inventory endpoint.
 */

export type TableStatus = "free" | "seated" | "reserved";

export interface Table {
  id: string;
  name: string;
  seats: number;
  status: TableStatus;
}

export interface FloorCounts {
  free: number;
  seated: number;
  reserved?: number;
}

export interface FloorData {
  tables: Table[];
  counts: FloorCounts;
}

export interface MenuItem {
  sku: string;
  name: string;
  price: number;
  available: boolean;
  category: string;
  fits?: string[];
}

export interface MenuData {
  items: MenuItem[];
}

export interface OpsMetrics {
  turns?: number;
  waterfall?: {
    e2e_turn_ms?: { count: number; mean: number };
  };
}

export interface OpsSnapshot {
  type: "ops_snapshot";
  floor: FloorData;
  menu: MenuData;
  metrics?: OpsMetrics;
  ts: string;
}

export type OpsClientCommand =
  | { command: "set-available"; sku: string; available: boolean }
  | { command: "seat"; table_id: string; party_size?: number }
  | { command: "clear"; table_id: string };
