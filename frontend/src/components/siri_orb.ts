/**
 * Audio Reactive Zen Visualizer for The Lantern Voice Concierge.
 * Renders dynamic acoustic frequency rings and ambient harmonic energy
 * that vibrates and wobbles in real-time as the guest or AI speaks.
 */

export type OrbMode = "idle" | "listen" | "think" | "speak";

export class AppleOrb {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private mode: OrbMode = "idle";
  private analyser: AnalyserNode | null = null;
  private animFrameId: number | null = null;
  private level: number = 0;
  private dataArray: Uint8Array<ArrayBuffer>;
  private size: number = 260;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const context = this.canvas.getContext("2d");
    if (!context) throw new Error("Canvas 2D context not available");
    this.ctx = context;

    this.dataArray = new Uint8Array(new ArrayBuffer(128));
    this.updateSize();
    this.startLoop();
  }

  private updateSize(): void {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    this.size = Math.round(rect.width) || 260;
    this.canvas.width = Math.round(this.size * dpr);
    this.canvas.height = Math.round(this.size * dpr);
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.scale(dpr, dpr);
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
    const dpr = window.devicePixelRatio || 1;
    const expectedPx = Math.round(this.size * dpr);
    if (this.canvas.width !== expectedPx || this.canvas.height !== expectedPx) {
      this.updateSize();
    }

    const centerX = this.size / 2;
    const centerY = this.size / 2;

    this.ctx.clearRect(0, 0, this.size, this.size);

    // 1. Compute acoustic energy from analyser
    let energy = 0.05;
    if (this.analyser && (this.mode === "listen" || this.mode === "speak")) {
      this.analyser.getByteFrequencyData(this.dataArray);
      let sum = 0;
      const count = Math.min(this.dataArray.length, 48);
      for (let i = 0; i < count; i++) {
        sum += this.dataArray[i];
      }
      energy = Math.min(1.0, sum / (count * 150));
    } else if (this.mode === "think") {
      energy = 0.16 + 0.08 * Math.sin(performance.now() / 240);
    } else {
      energy = 0.04 + 0.02 * Math.sin(performance.now() / 900);
    }

    // Smooth lerp for buttery natural inertia
    this.level += (energy - this.level) * 0.22;

    // Mic button is 64px diameter, radius is 32px
    const micButtonRadius = 32;
    const baseRadius = 40; // Starts right outside the mic button
    const currentRadius = baseRadius * (1 + this.level * 0.5);

    // 2. Soft core radial glow perfectly centered behind mic button
    const gradient = this.ctx.createRadialGradient(
      centerX, centerY, micButtonRadius * 0.7,
      centerX, centerY, currentRadius * 1.5
    );

    if (this.mode === "listen") {
      gradient.addColorStop(0, `rgba(250, 250, 249, ${0.9 + this.level * 0.1})`);
      gradient.addColorStop(0.4, `rgba(168, 162, 158, ${0.4 + this.level * 0.4})`);
      gradient.addColorStop(1, "rgba(28, 25, 23, 0)");
    } else if (this.mode === "speak") {
      gradient.addColorStop(0, `rgba(240, 253, 244, ${0.9 + this.level * 0.1})`);
      gradient.addColorStop(0.4, `rgba(101, 163, 13, ${0.35 + this.level * 0.45})`);
      gradient.addColorStop(1, "rgba(28, 25, 23, 0)");
    } else if (this.mode === "think") {
      gradient.addColorStop(0, "rgba(231, 229, 228, 0.8)");
      gradient.addColorStop(0.5, "rgba(120, 113, 108, 0.4)");
      gradient.addColorStop(1, "rgba(28, 25, 23, 0)");
    } else {
      gradient.addColorStop(0, "rgba(214, 211, 209, 0.4)");
      gradient.addColorStop(0.5, "rgba(168, 162, 158, 0.15)");
      gradient.addColorStop(1, "rgba(28, 25, 23, 0)");
    }

    this.ctx.fillStyle = gradient;
    this.ctx.beginPath();
    this.ctx.arc(centerX, centerY, currentRadius * 1.4, 0, Math.PI * 2);
    this.ctx.fill();

    // 3. Vibrating Concentric Frequency Rings - perfectly centered and evenly spaced
    const rings = 4;
    for (let r = 0; r < rings; r++) {
      const t = performance.now() / (650 - r * 80);
      const wobble = 1 + this.level * (0.12 + r * 0.05) * Math.sin(t + r * 1.4);
      this.ctx.beginPath();
      const alpha = Math.max(0, 0.14 + this.level * 0.45 - r * 0.025);
      this.ctx.strokeStyle = this.mode === "speak"
        ? `rgba(101, 163, 13, ${alpha})`
        : `rgba(168, 162, 158, ${alpha})`;
      this.ctx.lineWidth = 1.5 + this.level * 2.5;
      const ringRadius = (baseRadius + r * 18) * wobble;
      this.ctx.arc(centerX, centerY, ringRadius, 0, Math.PI * 2);
      this.ctx.stroke();
    }
  }
}
