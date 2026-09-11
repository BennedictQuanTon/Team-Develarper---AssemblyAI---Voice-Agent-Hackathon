/**
 * Component managing the Luxury Guest Voice Ordering Concierge view.
 * Handles circular microphone toggle, dual speech streaming (Guest + AI Waiter),
 * elegant fine-dining welcome card, and live progressive basket.
 */

import { AppleOrb, type OrbMode } from "./siri_orb";
import type { BasketItem } from "../types/realtime";

export class GuestView {
  private container: HTMLElement;
  private orb: AppleOrb;
  private micBtn: HTMLButtonElement;
  private welcomeCard: HTMLElement | null;
  private conversationStream: HTMLElement | null;
  private guestTranscript: HTMLElement;
  private guestStatus: HTMLElement | null;
  private agentTranscript: HTMLElement;
  private agentStatus: HTMLElement | null;
  private basketSheet: HTMLElement;
  private basketList: HTMLElement;
  private basketTotal: HTMLElement;

  public onMicClick?: () => void;
  public onPromptSelect?: (promptText: string) => void;

  constructor(container: HTMLElement) {
    this.container = container;

    // Grab child elements
    const canvas = this.container.querySelector<HTMLCanvasElement>("#orbCanvas")!;
    this.orb = new AppleOrb(canvas);
    this.micBtn = this.container.querySelector<HTMLButtonElement>("#micBtn")!;

    this.welcomeCard = this.container.querySelector<HTMLElement>("#welcomeCard");
    this.conversationStream = this.container.querySelector<HTMLElement>("#conversationStream");
    this.guestTranscript = this.container.querySelector<HTMLElement>("#guestTranscript")!;
    this.guestStatus = this.container.querySelector<HTMLElement>("#guestLiveStatus");
    this.agentTranscript = this.container.querySelector<HTMLElement>("#agentTranscript")!;
    this.agentStatus = this.container.querySelector<HTMLElement>("#agentLiveStatus");

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

    // Quick suggestion prompt chips
    const chips = this.container.querySelectorAll<HTMLButtonElement>(".chip-prompt");
    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        const prompt = chip.getAttribute("data-prompt") || chip.textContent || "";
        if (this.onPromptSelect) {
          this.onPromptSelect(prompt);
        } else if (this.onMicClick) {
          // Trigger mic session if not already listening
          this.onMicClick();
        }
      });
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
      ? `<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>`
      : `<svg viewBox="0 0 24 24"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>`;

    if (active) {
      this.showConversation();
      if (this.guestStatus) this.guestStatus.textContent = "Listening…";
      if (!this.guestTranscript.textContent || this.guestTranscript.textContent.includes("Tap")) {
        this.guestTranscript.textContent = "Listening to your voice…";
        this.guestTranscript.className = "speech-text guest-text interim";
      }
    }
  }

  private showConversation(): void {
    if (this.welcomeCard) this.welcomeCard.style.display = "none";
    if (this.conversationStream) this.conversationStream.style.display = "flex";
  }

  public showInterimTranscript(text: string): void {
    this.showConversation();
    this.guestTranscript.className = "speech-text guest-text interim";
    this.guestTranscript.textContent = text;
    if (this.guestStatus) this.guestStatus.textContent = "Listening…";
  }

  public showFinalTranscript(text: string): void {
    this.showConversation();
    this.guestTranscript.className = "speech-text guest-text";
    this.guestTranscript.textContent = text;
    if (this.guestStatus) this.guestStatus.textContent = "Speech captured";
    if (this.agentStatus) this.agentStatus.textContent = "Consulting kitchen & menu…";
  }

  public showAgentAnswer(text: string): void {
    this.showConversation();
    this.agentTranscript.className = "speech-text agent-text";
    this.agentTranscript.textContent = text;
    if (this.agentStatus) this.agentStatus.textContent = "The Lantern";
    if (this.guestStatus) this.guestStatus.textContent = "";
  }

  public setIdlePrompt(): void {
    if (this.welcomeCard) this.welcomeCard.style.display = "flex";
    if (this.conversationStream) this.conversationStream.style.display = "none";
    if (this.guestStatus) this.guestStatus.textContent = "";
    if (this.agentStatus) this.agentStatus.textContent = "";
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
        <span class="basket-row-name">${item.quantity}× ${item.name}</span>
        <span class="basket-row-price">$${(item.price * item.quantity).toFixed(2)}</span>
      `;
      this.basketList.appendChild(row);
    }

    if (total != null) {
      this.basketTotal.textContent = `$${total.toFixed(2)}`;
    }
  }
}
