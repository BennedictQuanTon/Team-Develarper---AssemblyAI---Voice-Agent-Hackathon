/**
 * Component managing the Guest Voice Ordering view.
 * Handles microphone toggle, live subtitles, and progressive basket disclosure.
 */

import { AppleOrb, type OrbMode } from "./siri_orb";
import type { BasketItem } from "../types/realtime";

export class GuestView {
  private container: HTMLElement;
  private orb: AppleOrb;
  private micBtn: HTMLButtonElement;
  private roleTag: HTMLElement;
  private transcriptEl: HTMLElement;
  private basketSheet: HTMLElement;
  private basketList: HTMLElement;
  private basketTotal: HTMLElement;

  public onMicClick?: () => void;

  constructor(container: HTMLElement) {
    this.container = container;

    // Grab child elements
    const canvas = this.container.querySelector<HTMLCanvasElement>("#orbCanvas")!;
    this.orb = new AppleOrb(canvas);
    this.micBtn = this.container.querySelector<HTMLButtonElement>("#micBtn")!;
    this.roleTag = this.container.querySelector<HTMLElement>("#stageRole")!;
    this.transcriptEl = this.container.querySelector<HTMLElement>("#stageTranscript")!;
    this.basketSheet = this.container.querySelector<HTMLElement>("#basketSheet")!;
    this.basketList = this.container.querySelector<HTMLElement>("#basketList")!;
    this.basketTotal = this.container.querySelector<HTMLElement>("#basketTotal")!;

    this.initEvents();
  }

  private initEvents(): void {
    this.micBtn.addEventListener("click", () => {
      if (this.onMicClick) {
        this.onMicClick();
      }
    });
  }

  public setOrbMode(mode: OrbMode): void {
    this.orb.setMode(mode);
  }

  public setAnalyser(analyser: AnalyserNode | null): void {
    this.orb.setAnalyser(analyser);
  }

  public setMicActive(active: boolean): void {
    this.micBtn.classList.toggle("active", active);
    this.micBtn.setAttribute("aria-label", active ? "Stop listening" : "Start speaking");
    this.micBtn.innerHTML = active
      ? `<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`
      : `<svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>`;
  }

  public showInterimTranscript(text: string): void {
    this.roleTag.textContent = "You";
    this.transcriptEl.className = "stage-transcript interim";
    this.transcriptEl.textContent = text;
  }

  public showFinalTranscript(text: string): void {
    this.roleTag.textContent = "You";
    this.transcriptEl.className = "stage-transcript";
    this.transcriptEl.textContent = text;
  }

  public showAgentAnswer(text: string): void {
    this.roleTag.textContent = "The Lantern";
    this.transcriptEl.className = "stage-transcript";
    this.transcriptEl.textContent = text;
  }

  public setIdlePrompt(): void {
    this.roleTag.textContent = "";
    this.transcriptEl.className = "stage-transcript empty";
    this.transcriptEl.textContent = "Chạm vào biểu tượng micro để bắt đầu gọi món.";
  }

  public updateBasket(items?: BasketItem[], total?: number): void {
    if (!items || items.length === 0) {
      this.basketSheet.classList.remove("visible");
      this.basketList.innerHTML = "";
      return;
    }

    this.basketSheet.classList.add("visible");
    this.basketList.innerHTML = "";

    for (const item of items) {
      const row = document.createElement("div");
      row.className = "basket-row";
      row.innerHTML = `
        <span class="basket-row-name">${item.quantity}x ${item.name}</span>
        <span class="basket-row-price">$${(item.price * item.quantity).toFixed(2)}</span>
      `;
      this.basketList.appendChild(row);
    }

    if (total != null) {
      this.basketTotal.textContent = `$${total.toFixed(2)}`;
    }
  }
}
