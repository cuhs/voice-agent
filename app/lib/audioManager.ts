/**
 * Audio playback manager for bot speech.
 *
 * Handles scheduling PCM audio chunks from ElevenLabs into Web Audio API
 * buffer sources, and provides an interrupt mechanism to stop playback
 * when the user starts speaking.
 */

export class AudioPlaybackManager {
  private audioContext: AudioContext;
  private nextStartTime = 0;
  private scheduledSources: AudioBufferSourceNode[] = [];
  private _botAnalyser: AnalyserNode | null = null;
  private onPlaybackEnd: () => void;

  constructor(audioContext: AudioContext, onPlaybackEnd: () => void) {
    this.audioContext = audioContext;
    this.onPlaybackEnd = onPlaybackEnd;
  }

  get botAnalyser(): AnalyserNode | null {
    return this._botAnalyser;
  }

  /** Schedule a PCM16 audio chunk for gapless playback. */
  scheduleChunk(arrayBuffer: ArrayBuffer): void {
    const int16Data = new Int16Array(arrayBuffer);
    const float32Data = new Float32Array(int16Data.length);
    for (let i = 0; i < int16Data.length; i++) {
      float32Data[i] = int16Data[i] / 32768.0;
    }

    const audioBuffer = this.audioContext.createBuffer(1, float32Data.length, 16000);
    audioBuffer.copyToChannel(float32Data, 0);

    const source = this.audioContext.createBufferSource();
    source.buffer = audioBuffer;

    // Lazy-create bot analyser
    if (!this._botAnalyser) {
      this._botAnalyser = this.audioContext.createAnalyser();
      this._botAnalyser.fftSize = 256;
      this._botAnalyser.connect(this.audioContext.destination);
    }
    source.connect(this._botAnalyser);

    this.scheduledSources.push(source);

    const currentTime = this.audioContext.currentTime;
    if (this.nextStartTime < currentTime) {
      this.nextStartTime = currentTime + 0.1;
    }

    source.start(this.nextStartTime);
    this.nextStartTime += audioBuffer.duration;

    source.onended = () => {
      this.scheduledSources = this.scheduledSources.filter((s) => s !== source);
      if (
        this.audioContext.currentTime >= this.nextStartTime - 0.05 &&
        this.scheduledSources.length === 0
      ) {
        this.onPlaybackEnd();
      }
    };
  }

  /** Immediately stop all scheduled audio (user interrupt). */
  interrupt(): void {
    this.scheduledSources.forEach((s) => {
      try { s.stop(); } catch (_) { /* already stopped */ }
      try { s.disconnect(); } catch (_) { /* already disconnected */ }
    });
    this.scheduledSources = [];
    this.nextStartTime = 0;
    this.onPlaybackEnd();
  }

  /** Reset state for a new session. */
  reset(): void {
    this.nextStartTime = 0;
    this.scheduledSources = [];
    this._botAnalyser = null;
  }
}
