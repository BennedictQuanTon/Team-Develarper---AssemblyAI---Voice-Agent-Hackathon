/**
 * Logs & Telemetry View Component
 * Adheres strictly to ThoughtStream Design System (DESIGN.md).
 * Displays real-time model routing, latency breakdowns, retrieved RAG chunks,
 * tool calls, and historical turns inspection.
 */

import { TurnTelemetry } from "../types/realtime";

export class LogsView {
  private container: HTMLElement;
  private activeTurn: TurnTelemetry | null = null;
  private turnsHistory: TurnTelemetry[] = [];
  private eventLogs: Array<{ time: string; type: string; detail: string; kind: string }> = [];

  // DOM sub-elements
  private turnQueryEl!: HTMLElement;
  private turnAnswerEl!: HTMLElement;
  private turnLatencyGridEl!: HTMLElement;
  private turnChunksListEl!: HTMLElement;
  private turnToolsListEl!: HTMLElement;

  private eventLogsStreamEl!: HTMLElement;
  private historyListEl!: HTMLElement;
  private historyCountEl!: HTMLElement;

  constructor(container: HTMLElement) {
    this.container = container;
    this.bindDomElements();
    this.bindActionButtons();
    this.fetchInitialHistory();
  }

  private bindDomElements(): void {
    this.turnQueryEl = this.container.querySelector("#logTurnQuery") as HTMLElement;
    this.turnAnswerEl = this.container.querySelector("#logTurnAnswer") as HTMLElement;
    this.turnLatencyGridEl = this.container.querySelector("#logTurnLatencyGrid") as HTMLElement;
    this.turnChunksListEl = this.container.querySelector("#logTurnChunksList") as HTMLElement;
    this.turnToolsListEl = this.container.querySelector("#logTurnToolsList") as HTMLElement;

    this.eventLogsStreamEl = this.container.querySelector("#logEventStream") as HTMLElement;
    this.historyListEl = this.container.querySelector("#logHistoryList") as HTMLElement;
    this.historyCountEl = this.container.querySelector("#logHistoryCount") as HTMLElement;
  }

  private bindActionButtons(): void {
    const refreshBtn = this.container.querySelector("#logRefreshBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        this.fetchInitialHistory();
        this.addEvent("history_refresh", "Fetched latest turns from /metrics/turns", "info");
      });
    }

    const clearStreamBtn = this.container.querySelector("#logClearStreamBtn");
    if (clearStreamBtn) {
      clearStreamBtn.addEventListener("click", () => {
        this.eventLogs = [];
        this.renderEventStream();
      });
    }
  }

  public async fetchInitialHistory(): Promise<void> {
    try {
      const res = await fetch("/metrics/turns?limit=40");
      if (!res.ok) return;
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        this.turnsHistory = data;
        this.renderHistoryList();
        if (!this.activeTurn && data[0]) {
          this.setActiveTurn(data[0]);
        }
      } else {
        if (this.historyListEl) {
          this.historyListEl.innerHTML = `
            <div class="log-empty-state">
              <span>No past turns recorded yet.</span>
            </div>
          `;
        }
      }
    } catch (err) {
      console.warn("Failed to fetch turns history:", err);
      if (this.historyListEl) {
        this.historyListEl.innerHTML = `
          <div class="log-empty-state">
            <span>Failed to load turn history.</span>
          </div>
        `;
      }
    }
  }

  public addEvent(type: string, detail: string, kind: "info" | "success" | "warn" | "accent" = "info"): void {
    const now = new Date();
    const timeStr = now.toTimeString().split(" ")[0] + "." + String(now.getMilliseconds()).padStart(3, "0");
    this.eventLogs.unshift({ time: timeStr, type, detail, kind });
    if (this.eventLogs.length > 60) this.eventLogs.pop();
    this.renderEventStream();
  }

  public handleTurnComplete(turn: TurnTelemetry): void {
    // Record event
    const e2e = turn.e2e_turn_ms ? `${turn.e2e_turn_ms}ms` : (turn.timings_ms?.e2e_turn_ms ? `${turn.timings_ms.e2e_turn_ms}ms` : "done");
    this.addEvent("turn_complete", `Turn finished · E2E: ${e2e} · Answer length: ${(turn.answer || "").length} chars`, "success");

    // Add to history
    this.turnsHistory.unshift(turn);
    if (this.turnsHistory.length > 100) this.turnsHistory.pop();

    this.setActiveTurn(turn);
    this.renderHistoryList();
  }

  public setActiveTurn(turn: TurnTelemetry): void {
    this.activeTurn = turn;

    // 1. Update Query & Answer
    if (this.turnQueryEl) {
      this.turnQueryEl.textContent = turn.query || turn.transcript || "—";
    }
    if (this.turnAnswerEl) {
      this.turnAnswerEl.textContent = turn.answer || "—";
    }

    // 2. Update Latency Breakdown Grid
    this.renderLatencyBreakdown(turn);

    // 3. Update Knowledge Chunks (sử dụng chunk gì gì gì)
    this.renderChunks(turn);

    // 4. Update Tool Calls (nếu là Waiter mode)
    this.renderToolCalls(turn);

    // Highlight active card in history list
    this.highlightActiveHistoryItem(turn.turn_id);
  }

  private renderLatencyBreakdown(turn: TurnTelemetry): void {
    if (!this.turnLatencyGridEl) return;
    const t = turn.timings_ms || {};
    const e2e = turn.e2e_turn_ms || t.e2e_turn_ms || 0;
    const ttfb = turn.ttfb_ms || t.ttfb_ms || 0;
    const rag = t.rag_ms || 0;
    const llm = t.llm_ttft_ms || 0;

    this.turnLatencyGridEl.innerHTML = `
      <div class="latency-pill-card ${e2e < 800 ? 'fast' : ''}">
        <span class="latency-label">E2E Latency</span>
        <span class="latency-val">${e2e > 0 ? `${Math.round(e2e)} ms` : '—'}</span>
      </div>
      <div class="latency-pill-card ${ttfb < 350 ? 'fast' : ''}">
        <span class="latency-label">TTFB (First Audio)</span>
        <span class="latency-val">${ttfb > 0 ? `${Math.round(ttfb)} ms` : '—'}</span>
      </div>
      <div class="latency-pill-card">
        <span class="latency-label">RAG Retrieval</span>
        <span class="latency-val">${rag > 0 ? `${Math.round(rag)} ms` : '0 ms'}</span>
      </div>
      <div class="latency-pill-card">
        <span class="latency-label">LLM / Tools</span>
        <span class="latency-val">${llm > 0 ? `${Math.round(llm)} ms` : (e2e > 0 ? `${Math.round(Math.max(0, e2e - ttfb))} ms` : '—')}</span>
      </div>
    `;
  }

  private renderChunks(turn: TurnTelemetry): void {
    if (!this.turnChunksListEl) return;

    const chunks = turn.chunks || [];
    const chunkIds = turn.chunk_ids || [];

    if (chunks.length === 0 && chunkIds.length === 0) {
      this.turnChunksListEl.innerHTML = `
        <div class="log-empty-state">
          <span>No RAG chunks used (Direct function call or standard conversational turn)</span>
        </div>
      `;
      return;
    }

    if (chunks.length > 0) {
      this.turnChunksListEl.innerHTML = chunks.map((c, i) => `
        <div class="chunk-item-card">
          <div class="chunk-header">
            <span class="chunk-badge">Chunk #${i + 1}</span>
            <span class="chunk-id mono">${this.escapeHtml(c.chunk_id)}</span>
            ${c.source ? `<span class="chunk-source">${this.escapeHtml(c.source)}</span>` : ''}
          </div>
          ${c.text ? `<p class="chunk-text">${this.escapeHtml(c.text)}</p>` : ''}
        </div>
      `).join("");
    } else {
      this.turnChunksListEl.innerHTML = chunkIds.map((id, i) => `
        <div class="chunk-item-card">
          <div class="chunk-header">
            <span class="chunk-badge">Chunk #${i + 1}</span>
            <span class="chunk-id mono">${this.escapeHtml(id)}</span>
          </div>
        </div>
      `).join("");
    }
  }

  private renderToolCalls(turn: TurnTelemetry): void {
    if (!this.turnToolsListEl) return;
    const tools = turn.tool_calls || [];

    if (tools.length === 0) {
      this.turnToolsListEl.innerHTML = `
        <div class="log-empty-state">
          <span>No tool calls in current turn.</span>
        </div>
      `;
      return;
    }

    this.turnToolsListEl.innerHTML = tools.map((t, i) => `
      <div class="tool-call-card">
        <div class="tool-header">
          <span class="tool-badge">Tool #${i + 1}</span>
          <span class="tool-name mono">${this.escapeHtml(t.tool)}</span>
        </div>
        <pre class="tool-args mono">${this.escapeHtml(JSON.stringify(t.args, null, 2))}</pre>
      </div>
    `).join("");
  }

  private renderEventStream(): void {
    if (!this.eventLogsStreamEl) return;
    if (this.eventLogs.length === 0) {
      this.eventLogsStreamEl.innerHTML = `
        <div class="log-empty-state">
          <span>No live events yet. Speak into the microphone to see real-time pipeline telemetry.</span>
        </div>
      `;
      return;
    }

    this.eventLogsStreamEl.innerHTML = this.eventLogs.map((e) => `
      <div class="log-event-row ${e.kind}">
        <span class="log-event-time mono">${e.time}</span>
        <span class="log-event-type mono">${e.type}</span>
        <span class="log-event-detail">${this.escapeHtml(e.detail)}</span>
      </div>
    `).join("");
  }

  private renderHistoryList(): void {
    if (!this.historyListEl) return;
    if (this.historyCountEl) {
      this.historyCountEl.textContent = `${this.turnsHistory.length} turns`;
    }

    if (this.turnsHistory.length === 0) {
      this.historyListEl.innerHTML = `
        <div class="log-empty-state">
          <span>No past turns recorded yet.</span>
        </div>
      `;
      return;
    }

    this.historyListEl.innerHTML = this.turnsHistory.map((t) => {
      const e2e = t.e2e_turn_ms || t.timings_ms?.e2e_turn_ms || 0;
      const turnId = t.turn_id || "turn";
      const isSelected = this.activeTurn?.turn_id === t.turn_id;
      const query = t.query || t.transcript || "(No query text)";
      const time = t.timestamp ? new Date(t.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : "Recent";
      const asr = t.provider?.asr || "Universal";
      const llm = t.provider?.llm || "Gemini";
      const chunkCount = (t.chunks || t.chunk_ids || []).length;
      const toolCount = (t.tool_calls || []).length;

      return `
        <div class="history-turn-card ${isSelected ? 'selected' : ''}" data-turn-id="${turnId}">
          <div class="ht-header">
            <span class="ht-query">${this.escapeHtml(query)}</span>
            <span class="ht-time mono">${time}</span>
          </div>
          <div class="ht-footer">
            <span class="ht-badge ${e2e > 0 && e2e < 700 ? 'badge-fast' : ''} mono">
              ${e2e > 0 ? `${Math.round(e2e)}ms` : '—'}
            </span>
            <span class="ht-badge mono">${asr.includes("assemblyai") ? "AAI" : "ASR"} + ${llm.includes("gemini") ? "Gemini" : "LLM"}</span>
            ${chunkCount > 0 ? `<span class="ht-badge mono">${chunkCount} chunks</span>` : ''}
            ${toolCount > 0 ? `<span class="ht-badge badge-accent mono">${toolCount} tools</span>` : ''}
          </div>
        </div>
      `;
    }).join("");

    // Add click event listeners
    this.historyListEl.querySelectorAll(".history-turn-card").forEach((card) => {
      card.addEventListener("click", () => {
        const id = card.getAttribute("data-turn-id");
        const found = this.turnsHistory.find((item) => item.turn_id === id);
        if (found) {
          this.setActiveTurn(found);
        }
      });
    });
  }

  private highlightActiveHistoryItem(turnId?: string): void {
    if (!this.historyListEl || !turnId) return;
    this.historyListEl.querySelectorAll(".history-turn-card").forEach((card) => {
      if (card.getAttribute("data-turn-id") === turnId) {
        card.classList.add("selected");
      } else {
        card.classList.remove("selected");
      }
    });
  }

  private escapeHtml(str: string): string {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}
