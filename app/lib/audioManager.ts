/**
 * Audio playback manager for bot speech.
 *
 * This class ensures that audio chunks received from the backend (originally 
 * from ElevenLabs) are played smoothly without popping or gaps. 
 * It schedules PCM audio chunks into Web Audio API buffer sources, 
 * and provides a robust interrupt mechanism to instantly stop playback
 * when the user starts speaking.
 */

export class AudioPlaybackManager {
  private audioContext: AudioContext;
  
  // Tracks the absolute timeline within the AudioContext of when the NEXT 
  // chunk should start playing. This guarantees gapless playback.
  private nextStartTime = 0;
  
  // Tracks all audio chunks currently scheduled or playing, so we can 
  // stop them immediately on interrupt.
  private scheduledSources: AudioBufferSourceNode[] = [];
  
  // Analyser node to extract waveform data for the UI.
  private _botAnalyser: AnalyserNode | null = null;
  
  // Callback fired when the queue is completely empty (bot has finished talking).
  private onPlaybackEnd: () => void;

  constructor(audioContext: AudioContext, onPlaybackEnd: () => void) {
    this.audioContext = audioContext;
    this.onPlaybackEnd = onPlaybackEnd;
  }

  get botAnalyser(): AnalyserNode | null {
    return this._botAnalyser;
  }

  /** 
   * Schedule a PCM16 audio chunk for gapless playback. 
   * @param arrayBuffer Raw 16-bit PCM data from the WebSocket.
   */
  scheduleChunk(arrayBuffer: ArrayBuffer): void {
    // 1. Convert Int16 (received format) to Float32 (Web Audio API required format)
    const int16Data = new Int16Array(arrayBuffer);
    const float32Data = new Float32Array(int16Data.length);
    for (let i = 0; i < int16Data.length; i++) {
      float32Data[i] = int16Data[i] / 32768.0;
    }

    // 2. Create an AudioBuffer and copy the data in
    const audioBuffer = this.audioContext.createBuffer(1, float32Data.length, 16000);
    audioBuffer.copyToChannel(float32Data, 0);

    // 3. Create a source node to play the buffer
    const source = this.audioContext.createBufferSource();
    source.buffer = audioBuffer;

    // 4. Lazy-create bot analyser (for waveform UI) and connect the graph
    if (!this._botAnalyser) {
      this._botAnalyser = this.audioContext.createAnalyser();
      this._botAnalyser.fftSize = 256;
      this._botAnalyser.connect(this.audioContext.destination);
    }
    source.connect(this._botAnalyser);

    this.scheduledSources.push(source);

    // 5. Gapless Scheduling Logic
    // Compare the audioContext's current time with our theoretical next start time.
    // If nextStartTime is in the past (e.g. network latency caused the buffer to underrun),
    // we jump ahead and schedule it slightly in the future (current time + 0.1s).
    const currentTime = this.audioContext.currentTime;
    if (this.nextStartTime < currentTime) {
      this.nextStartTime = currentTime + 0.1;
    }

    // Tell the Web Audio API exactly when to start this chunk
    source.start(this.nextStartTime);
    
    // Advance the pointer for the next chunk
    this.nextStartTime += audioBuffer.duration;

    // 6. Cleanup when chunk finishes playing
    source.onended = () => {
      this.scheduledSources = this.scheduledSources.filter((s) => s !== source);
      
      // If we are near the end of our scheduled time and have no more chunks queued,
      // the bot has finished speaking.
      if (
        this.audioContext.currentTime >= this.nextStartTime - 0.05 &&
        this.scheduledSources.length === 0
      ) {
        this.onPlaybackEnd();
      }
    };
  }

  /** 
   * Immediately stop all scheduled audio (user interrupt).
   * Called by the VAD when the user begins speaking.
   */
  interrupt(): void {
    // Force stop all nodes in the queue
    this.scheduledSources.forEach((s) => {
      try { s.stop(); } catch (_) { /* already stopped */ }
      try { s.disconnect(); } catch (_) { /* already disconnected */ }
    });
    
    // Clear the queue
    this.scheduledSources = [];
    this.nextStartTime = 0;
    
    // Fire callback to update UI
    this.onPlaybackEnd();
  }

  /** Reset state for a new session entirely. */
  reset(): void {
    this.nextStartTime = 0;
    this.scheduledSources = [];
    this._botAnalyser = null;
  }
}
