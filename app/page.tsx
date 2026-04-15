"use client";

import { useState, useRef } from "react";

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("idle");
  const [wsStatus, setWsStatus] = useState("disconnected");
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);

  const [finalTranscript, setFinalTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");

  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const nextStartTimeRef = useRef<number>(0);

  const connectWebSocket = () => {
    const ws = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/audio");
    wsRef.current = ws;

    ws.onopen = () => {
      setWsStatus("connected");
    };

    ws.onmessage = async (event) => {
      if (event.data instanceof Blob) {
        setIsBotSpeaking(true);
        const arrayBuffer = await event.data.arrayBuffer();

        // Convert PCM Int16 to Float32
        const int16Data = new Int16Array(arrayBuffer);
        const float32Data = new Float32Array(int16Data.length);
        for (let i = 0; i < int16Data.length; i++) {
          float32Data[i] = int16Data[i] / 32768.0;
        }

        if (audioContextRef.current) {
          const audioBuffer = audioContextRef.current.createBuffer(1, float32Data.length, 16000);
          audioBuffer.copyToChannel(float32Data, 0);

          const source = audioContextRef.current.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(audioContextRef.current.destination);

          const currentTime = audioContextRef.current.currentTime;
          if (nextStartTimeRef.current < currentTime) {
            nextStartTimeRef.current = currentTime + 0.1; // 100ms buffering
          }

          source.start(nextStartTimeRef.current);
          nextStartTimeRef.current += audioBuffer.duration;

          source.onended = () => {
            if (audioContextRef.current && audioContextRef.current.currentTime >= nextStartTimeRef.current - 0.05) {
              setIsBotSpeaking(false);
            }
          };
        }
      } else if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data);

          if (msg.type === "transcript") {
            if (msg.is_final) {
              setFinalTranscript(prev => prev + (prev ? " " : "") + msg.text);
              setInterimTranscript("");
            } else {
              setInterimTranscript(msg.text);
            }
          }
        } catch (e) {
          console.error("Failed to parse websocket message", e);
        }
      }
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
      setFinalTranscript("");
      setInterimTranscript("");
      nextStartTimeRef.current = 0;
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

      workletNode.port.onmessage = (event) => {
        const float32Data = event.data as Float32Array;

        // Downsample and convert to 16-bit PCM buffer
        const int16Data = new Int16Array(float32Data.length);
        for (let i = 0; i < float32Data.length; i++) {
          const s = Math.max(-1, Math.min(1, float32Data[i]));
          int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

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
      <h1>Vocale AI</h1>

      <div>
        <p>Mic Status: {status}</p>
        <p>WebSocket: {wsStatus}</p>

        <button onClick={handleToggle}>
          {isRecording ? "Stop Recording" : "Start Recording"}
        </button>
      </div>

      <div style={{ marginTop: "2rem" }}>
        <h3>TTS Playback Status</h3>
        {isBotSpeaking ? (
          <p style={{ color: "blue", fontWeight: "bold" }}>Bot is speaking... </p>
        ) : (
          <p style={{ color: "gray" }}>Bot is silent.</p>
        )}
      </div>

      <div style={{ marginTop: "2rem" }}>
        <h3>Live Transcript</h3>
        <p>
          <span>{finalTranscript}</span>
          <span style={{ color: "gray", fontStyle: "italic", marginLeft: finalTranscript && interimTranscript ? "0.3rem" : "0" }}>
            {interimTranscript}
          </span>
        </p>
      </div>
    </div>
  );
}
