/**
 * Low-latency Web Audio API PCM Stream Player.
 * Schedules 16kHz 16-bit linear PCM chunks back-to-back with instant cancellation on barge-in.
 */

export class PCMStreamPlayer {
  private ctx: AudioContext;
  private analyser: AnalyserNode | null;
  private nextTime: number = 0;
  private sources: AudioBufferSourceNode[] = [];
  public isPlaying: boolean = false;
  public onPlaybackEnd?: () => void;

  constructor(ctx: AudioContext, analyser: AnalyserNode | null = null) {
    this.ctx = ctx;
    this.analyser = analyser;
  }

  public playChunk(pcmBytes: Int16Array, sampleRate: number = 16000): void {
    if (!pcmBytes || pcmBytes.length === 0) return;

    // Convert Int16 [-32768, 32767] to Float32 [-1.0, 1.0]
    const float32 = new Float32Array(pcmBytes.length);
    for (let i = 0; i < pcmBytes.length; i++) {
      float32[i] = pcmBytes[i] / 32768.0;
    }

    const audioBuf = this.ctx.createBuffer(1, float32.length, sampleRate);
    audioBuf.getChannelData(0).set(float32);

    const source = this.ctx.createBufferSource();
    source.buffer = audioBuf;

    if (this.analyser) {
      source.connect(this.analyser);
      this.analyser.connect(this.ctx.destination);
    } else {
      source.connect(this.ctx.destination);
    }

    const now = this.ctx.currentTime;
    if (this.nextTime < now) {
      this.nextTime = now;
    }

    source.start(this.nextTime);
    this.nextTime += audioBuf.duration;
    this.sources.push(source);
    this.isPlaying = true;

    source.onended = () => {
      const idx = this.sources.indexOf(source);
      if (idx !== -1) {
        this.sources.splice(idx, 1);
      }
      if (this.sources.length === 0) {
        this.isPlaying = false;
        if (this.onPlaybackEnd) {
          this.onPlaybackEnd();
        }
      }
    };
  }

  /**
   * Instantly stops all playing audio. Crucial for sub-second barge-in response.
   */
  public stop(): void {
    for (const src of this.sources) {
      try {
        src.stop();
        src.disconnect();
      } catch (_) {
        // Source might already have ended
      }
    }
    this.sources = [];
    this.nextTime = 0;
    this.isPlaying = false;
  }
}
