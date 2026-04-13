"use client";

import { useState, useRef } from "react";

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("idle");
  const [isReceiving, setIsReceiving] = useState(false);
  const [hasPlayback, setHasPlayback] = useState(false);

  // References for our AudioWorklet flow
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);

  // Accumulate the raw Float32Array chunks so we can test playback
  const recordedChunksRef = useRef<Float32Array[]>([]);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Clear previous recording 
      recordedChunksRef.current = [];
      setHasPlayback(false);

      // 1. Create AudioContext with 16kHz sample rate
      // as required by Deepgram/Silero VAD
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
        sampleRate: 16000,
      });
      audioContextRef.current = audioContext;

      // 2. Load the AudioWorklet processor script
      // It must be in public/ so Next.js doesn't bundle it
      await audioContext.audioWorklet.addModule("/audio-processor.js");

      // 3. Connect the media stream to the worklet node
      const source = audioContext.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioContext, "audio-processor");
      workletNodeRef.current = workletNode;

      // 4. Handle incoming raw PCM Float32Array chunks
      workletNode.port.onmessage = (event) => {
        // Here we receive 128-sample Float32Arrays (~2.7ms of audio at 48kHz, or ~8ms at 16kHz)
        setIsReceiving(true);

        // Save the chunk to verify it later
        // We clone it because transferring Float32Arrays from workers can sometimes reuse memory
        recordedChunksRef.current.push(new Float32Array(event.data));

        // Removed console log so we don't spam the console too much during long recordings
      };

      source.connect(workletNode);
      // Note: we do NOT connect workletNode to audioContext.destination
      // to avoid causing an echo feedback loop!

      setIsRecording(true);
      setStatus("listening via AudioWorklet (16kHz)");
    } catch (err) {
      console.error("Error accessing microphone:", err);
      setStatus("error: check console");
    }
  };

  const stopRecording = () => {
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }

    if (audioContextRef.current) {
      audioContextRef.current.close().catch(console.error);
      audioContextRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    setIsRecording(false);
    setStatus("stopped");
    setIsReceiving(false);

    // If we captured anything, enable the playback button
    if (recordedChunksRef.current.length > 0) {
      setHasPlayback(true);
    }
  };

  const playCapture = () => {
    if (recordedChunksRef.current.length === 0) return;

    // 1. Calculate the total size of all chunks
    const totalLength = recordedChunksRef.current.reduce((acc, chunk) => acc + chunk.length, 0);

    // 2. Combine all chunks into one massive Float32Array
    const mergedArray = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of recordedChunksRef.current) {
      mergedArray.set(chunk, offset);
      offset += chunk.length;
    }

    // 3. Play it back using a temporary AudioContext
    const playbackCtx = new (window.AudioContext || (window as any).webkitAudioContext)({
      sampleRate: 16000,
    });

    // Create an empty AudioBuffer at 16kHz
    const audioBuffer = playbackCtx.createBuffer(1, totalLength, 16000);
    // Fill the buffer's first (and only) channel with our raw audio data
    audioBuffer.getChannelData(0).set(mergedArray);

    // Connect and play
    const sourceNode = playbackCtx.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(playbackCtx.destination);
    sourceNode.start();

    setStatus("playing back captured audio...");
    sourceNode.onended = () => {
      setStatus("stopped");
    };
  };

  const handleToggle = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>Voice Agent</h1>
      <p>Status: <strong>{status}</strong></p>

      {isReceiving && <p style={{ color: "green", fontWeight: "bold" }}>Receiving audio chunks...</p>}

      <div style={{ display: "flex", gap: "10px", marginBottom: "1rem" }}>
        <button onClick={handleToggle}>
          {isRecording ? "Stop" : "Start"}
        </button>

        {hasPlayback && !isRecording && (
          <button onClick={playCapture} disabled={isRecording}>
            Verify Playback
          </button>
        )}
      </div>

    </div>
  );
}
