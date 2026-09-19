export class MicRecorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;

  async start(onPcm16: (pcm: ArrayBuffer) => void): Promise<void> {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.context = new AudioContext();
    await this.context.resume();
    this.source = this.context.createMediaStreamSource(this.stream);
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    const inputRate = this.context.sampleRate;
    this.processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const ratio = inputRate / 16000;
      const size = Math.floor(input.length / ratio);
      const output = new Int16Array(size);
      for (let index = 0; index < size; index += 1) {
        const start = Math.floor(index * ratio);
        const end = Math.max(start + 1, Math.floor((index + 1) * ratio));
        let total = 0;
        for (let sourceIndex = start; sourceIndex < end && sourceIndex < input.length; sourceIndex += 1) total += input[sourceIndex];
        const sample = Math.max(-1, Math.min(1, total / (end - start)));
        output[index] = sample < 0 ? sample * 32768 : sample * 32767;
      }
      onPcm16(output.buffer);
    };
    this.source.connect(this.processor);
    this.processor.connect(this.context.destination);
  }

  async stop(): Promise<void> {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    await this.context?.close();
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.context = null;
  }
}
