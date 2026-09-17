/**
 * Microphone capture and linear downsampling to 16kHz 16-bit PCM.
 * Mutes audio loopback to prevent speaker echo.
 */

export class MicRecorder {
  private ctx: AudioContext;
  private stream: MediaStream | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private muteGain: GainNode | null = null;
  public analyser: AnalyserNode | null = null;
  public onAudioChunk?: (pcmData: Int16Array) => void;

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
  }

  public async start(): Promise<void> {
    if (this.stream) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });

    this.sourceNode = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.sourceNode.connect(this.analyser);

    // Use 2048 buffer (~42ms at 48kHz) for low latency
    this.processor = this.ctx.createScriptProcessor(2048, 1, 1);
    this.processor.onaudioprocess = (e) => {
      const inputFloat = e.inputBuffer.getChannelData(0);
      const pcm16 = this.downsampleTo16k(inputFloat, this.ctx.sampleRate);
      if (this.onAudioChunk) {
        this.onAudioChunk(pcm16);
      }
    };

    this.sourceNode.connect(this.processor);

    // Mute mic output to speakers so user doesn't hear themselves
    this.muteGain = this.ctx.createGain();
    this.muteGain.gain.value = 0;
    this.processor.connect(this.muteGain);
    this.muteGain.connect(this.ctx.destination);
  }

  public stop(): void {
    if (this.processor) {
      this.processor.disconnect();
      this.processor = null;
    }
    if (this.sourceNode) {
      this.sourceNode.disconnect();
      this.sourceNode = null;
    }
    if (this.muteGain) {
      this.muteGain.disconnect();
      this.muteGain = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }
  }

  private downsampleTo16k(input: Float32Array, inputRate: number): Int16Array {
    if (inputRate === 16000) {
      const out = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        const s = Math.max(-1, Math.min(1, input[i]));
        out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      return out;
    }

    const ratio = inputRate / 16000;
    const newLen = Math.round(input.length / ratio);
    const out = new Int16Array(newLen);

    for (let i = 0; i < newLen; i++) {
      const idx = Math.min(input.length - 1, Math.round(i * ratio));
      const s = Math.max(-1, Math.min(1, input[idx] || 0));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    return out;
  }
}
