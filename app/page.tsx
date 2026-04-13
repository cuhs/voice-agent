"use client";

import { useState, useRef } from "react";

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("idle");
  const [wsStatus, setWsStatus] = useState("disconnected");

  // References for our AudioWorklet flow
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const connectWebSocket = () => {
    const ws = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/audio");
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setWsStatus("connected");
    };

    ws.onmessage = (event) => {
      // Receive the echoed ArrayBuffer containing Int16 PCM data
      const int16 = new Int16Array(event.data);

      // Decode Int16 PCM bytes back to Float32Array
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768.0;
      }

      console.log("Echoed audio chunk received. Float32Array length:", float32.length);
    };

    ws.onclose = () => {
      setWsStatus("disconnected");
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      setWsStatus("error: check console");
    };
  };

  const startRecording = async () => {
    try {
      setWsStatus("connecting...");
      connectWebSocket();

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
        sampleRate: 16000,
      });
      audioContextRef.current = audioContext;

      await audioContext.audioWorklet.addModule("/audio-processor.js");

      const source = audioContext.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioContext, "audio-processor");
      workletNodeRef.current = workletNode;

      // When the AudioWorklet posts a message with Float32Array (128 samples typically)
      workletNode.port.onmessage = (event) => {
        const float32Data = event.data as Float32Array;

        // Convert Float32Array to Int16Array (16-bit PCM buffer) for transport
        const int16Data = new Int16Array(float32Data.length);
        for (let i = 0; i < float32Data.length; i++) {
          // Clamp the values to -1 to 1 before converting to 16 bit PCM
          const s = Math.max(-1, Math.min(1, float32Data[i]));
          int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        // Send binary buffer to backend
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(int16Data.buffer);
        }
      };

      source.connect(workletNode);

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

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setIsRecording(false);
    setStatus("stopped");
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
      <p>Mic Status: <strong>{status}</strong></p>
      <p>WebSocket: <strong>{wsStatus}</strong></p>

      <div style={{ display: "flex", gap: "10px", marginBottom: "1rem" }}>
        <button onClick={handleToggle}>
          {isRecording ? "Stop" : "Start"}
        </button>
      </div>
    </div>
  );
}
