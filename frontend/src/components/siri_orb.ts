/**
 * Apple Siri / Intelligence-inspired Minimalist Audio Orb.
 * Renders fluid, elegant reactive waveforms for Idle, Listen, Think, and Speak modes.
 */

export type OrbMode = "idle" | "listen" | "think" | "speak";

export class AppleOrb {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private mode: OrbMode = "idle";
  private analyser: AnalyserNode | null = null;
  private animFrameId: number | null = null;
  private phase: number = 0;
  private dataArray: Uint8Array<ArrayBuffer>;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const context = this.canvas.getContext("2d");
    if (!context) throw new Error("Canvas 2D context not available");
    this.ctx = context;

    // Retina display resolution
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    const size = rect.width || 140;
    this.canvas.width = size * dpr;
    this.canvas.height = size * dpr;
    this.ctx.scale(dpr, dpr);

    this.dataArray = new Uint8Array(new ArrayBuffer(128));
    this.startLoop();
  }

  public setMode(mode: OrbMode): void {
    this.mode = mode;
  }

  public setAnalyser(analyser: AnalyserNode | null): void {
    this.analyser = analyser;
    if (analyser) {
      this.dataArray = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
    }
  }

  private startLoop(): void {
    const render = () => {
      this.draw();
      this.animFrameId = requestAnimationFrame(render);
    };
    this.animFrameId = requestAnimationFrame(render);
  }

  public stop(): void {
    if (this.animFrameId !== null) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
  }

  private draw(): void {
    const width = 140;
    const height = 140;
    const centerX = width / 2;
    const centerY = height / 2;

    this.ctx.clearRect(0, 0, width, height);

    // Compute audio amplitude
    let energy = 0;
    if (this.analyser && (this.mode === "listen" || this.mode === "speak")) {
      this.analyser.getByteFrequencyData(this.dataArray);
      let sum = 0;
      const count = Math.min(this.dataArray.length, 32);
      for (let i = 0; i < count; i++) {
        sum += this.dataArray[i];
      }
      energy = sum / (count * 255);
    }

    this.phase += this.mode === "think" ? 0.08 : 0.03;

    // Radius dynamics
    const baseRadius = 52;
    const expansion = energy * 16;
    const radius = baseRadius + expansion;

    // Draw Apple glow layer
    const gradient = this.ctx.createRadialGradient(
      centerX, centerY, radius * 0.4,
      centerX, centerY, radius * 1.2
    );

    if (this.mode === "idle") {
      gradient.addColorStop(0, "rgba(120, 120, 128, 0.2)");
      gradient.addColorStop(0.8, "rgba(120, 120, 128, 0.04)");
      gradient.addColorStop(1, "rgba(120, 120, 128, 0)");
    } else if (this.mode === "listen") {
      gradient.addColorStop(0, "rgba(0, 113, 227, 0.4)");
      gradient.addColorStop(0.7, "rgba(52, 199, 89, 0.2)");
      gradient.addColorStop(1, "rgba(0, 113, 227, 0)");
    } else if (this.mode === "think") {
      gradient.addColorStop(0, "rgba(175, 82, 222, 0.45)");
      gradient.addColorStop(0.6, "rgba(0, 113, 227, 0.25)");
      gradient.addColorStop(1, "rgba(175, 82, 222, 0)");
    } else {
      // speak
      gradient.addColorStop(0, "rgba(52, 199, 89, 0.5)");
      gradient.addColorStop(0.6, "rgba(0, 113, 227, 0.3)");
      gradient.addColorStop(1, "rgba(52, 199, 89, 0)");
    }

    this.ctx.fillStyle = gradient;
    this.ctx.beginPath();
    this.ctx.arc(centerX, centerY, radius * 1.2, 0, Math.PI * 2);
    this.ctx.fill();

    // Subtle breathing perimeter ring
    this.ctx.strokeStyle = this.mode === "idle"
      ? "rgba(120, 120, 128, 0.25)"
      : this.mode === "listen"
      ? "rgba(0, 113, 227, 0.6)"
      : this.mode === "think"
      ? "rgba(175, 82, 222, 0.6)"
      : "rgba(52, 199, 89, 0.7)";

    this.ctx.lineWidth = 1.5;
    this.ctx.beginPath();
    this.ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
    this.ctx.stroke();
  }
}
