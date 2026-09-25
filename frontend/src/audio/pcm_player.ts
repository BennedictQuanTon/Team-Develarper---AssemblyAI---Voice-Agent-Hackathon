export class PcmPlayer {
  private context: AudioContext | null = null;
  private nextStart = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private clips = new Map<string, Promise<AudioBuffer>>();

  async resume(): Promise<void> {
    if (!this.context) this.context = new AudioContext();
    if (this.context.state === "suspended") await this.context.resume();
  }

  /** Fetch and decode a prerecorded clip once; later plays start without a network round trip. */
  preload(url: string): Promise<AudioBuffer> {
    let clip = this.clips.get(url);
    if (!clip) {
      clip = this.resume()
        .then(() => fetch(url))
        .then((response) => response.arrayBuffer())
        .then((bytes) => this.context!.decodeAudioData(bytes));
      clip.catch(() => this.clips.delete(url));
      this.clips.set(url, clip);
    }
    return clip;
  }

  /** Queue a prerecorded clip; streamed reply chunks that arrive later play after it. */
  async enqueueClip(url: string): Promise<void> {
    this.schedule(await this.preload(url));
  }

  async enqueue(pcmBase64: string, sampleRate: number): Promise<void> {
    await this.resume();
    const context = this.context!;
    const bytes = Uint8Array.from(atob(pcmBase64), (value) => value.charCodeAt(0));
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const samples = new Float32Array(Math.floor(bytes.byteLength / 2));
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 32768;
    }
    const buffer = context.createBuffer(1, samples.length, sampleRate);
    buffer.copyToChannel(samples, 0);
    this.schedule(buffer);
  }

  private schedule(buffer: AudioBuffer): void {
    const context = this.context!;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.02, this.nextStart);
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  stop(): void {
    for (const source of this.sources) {
      try { source.stop(); } catch { /* source already stopped */ }
    }
    this.sources.clear();
    this.nextStart = this.context?.currentTime ?? 0;
  }
}
