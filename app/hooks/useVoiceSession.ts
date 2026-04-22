/**
 * Custom hook for the voice session lifecycle.
 *
 * Manages:
 *  - WebSocket connection to the backend (with reconnect backoff)
 *  - Microphone capture via Web Audio API + AudioWorklet
 *  - VAD (Voice Activity Detection) for speech start/end
 *  - Bot audio playback via AudioPlaybackManager
 *  - Chat history and transcript state
 */

"use client";

import { useState, useRef, useCallback } from "react";
import { AudioPlaybackManager } from "@/app/lib/audioManager";
import type { Message } from "@/app/components/ChatPanel";

export function useVoiceSession() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("disconnected");
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);
  const [chatHistory, setChatHistory] = useState<Message[]>([]);
  const [interimTranscript, setInterimTranscript] = useState("");

  // Refs
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const vadRef = useRef<any>(null);
  const playbackManagerRef = useRef<AudioPlaybackManager | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const ignoreAudioRef = useRef(false);

  // ── WebSocket ──────────────────────────────────────────────────────────

  const connectWebSocket = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    const ws = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/audio");
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      reconnectAttemptsRef.current = 0;
    };

    ws.onmessage = async (event) => {
      if (event.data instanceof Blob) {
        if (ignoreAudioRef.current) return;
        setIsBotSpeaking(true);
        const arrayBuffer = await event.data.arrayBuffer();
        playbackManagerRef.current?.scheduleChunk(arrayBuffer);
      } else if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data);

          if (msg.type === "transcript") {
            if (msg.is_final) {
              setChatHistory((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === "user") {
                  return [...prev.slice(0, -1), { role: "user", text: last.text + " " + msg.text }];
                }
                return [...prev, { role: "user", text: msg.text }];
              });
              setInterimTranscript("");
            } else {
              setInterimTranscript(msg.text);
            }
          } else if (msg.type === "bot_response") {
            ignoreAudioRef.current = false;
            setChatHistory((prev) => [...prev, { role: "bot", text: msg.text }]);
          }
          // interrupt_ack — no action needed
        } catch (e) {
          console.error("Failed to parse websocket message", e);
        }
      }
    };

    ws.onclose = () => {
      if (wsRef.current === ws) {
        setStatus("reconnecting");
        const backoff = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 10000);
        console.log(`WebSocket dropped. Reconnecting in ${backoff}ms...`);
        reconnectAttemptsRef.current += 1;

        reconnectTimeoutRef.current = setTimeout(() => {
          if (wsRef.current === ws) connectWebSocket();
        }, backoff);
      } else {
        setStatus("disconnected");
      }
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      if (wsRef.current === ws) setStatus("error");
    };
  }, []);

  // ── Start Recording ────────────────────────────────────────────────────

  const startRecording = useCallback(async () => {
    try {
      setStatus("connecting");
      setChatHistory([]);
      setInterimTranscript("");
      connectWebSocket();

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const { MicVAD } = await import("@ricky0123/vad-web");
      const ort = await import("onnxruntime-web");

      ort.env.wasm.wasmPaths = "https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/";
      ort.env.wasm.numThreads = 1;

      const myvad = await MicVAD.new({
        getStream: async () => stream,
        baseAssetPath: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web/dist/",
        onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/",
        positiveSpeechThreshold: 0.6,
        redemptionMs: 600,
        minSpeechMs: 100,
        onSpeechStart: () => {
          console.log("VAD: Speech Start");
          ignoreAudioRef.current = true;
          playbackManagerRef.current?.interrupt();
          setIsBotSpeaking(false);

          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: "interrupt" }));
          }
        },
        onSpeechEnd: () => {
          console.log("VAD: Speech End");
        },
      });
      vadRef.current = myvad;
      myvad.start();

      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
        sampleRate: 16000,
      });
      audioContextRef.current = audioContext;

      // Create playback manager
      playbackManagerRef.current = new AudioPlaybackManager(audioContext, () => {
        setIsBotSpeaking(false);
      });

      await audioContext.audioWorklet.addModule("/audio-processor.js");

      const source = audioContext.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioContext, "audio-processor");
      workletNodeRef.current = workletNode;

      workletNode.port.onmessage = (event) => {
        const float32Data = event.data as Float32Array;
        const int16Data = new Int16Array(float32Data.length);
        for (let i = 0; i < float32Data.length; i++) {
          const s = Math.max(-1, Math.min(1, float32Data[i]));
          int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }

        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(int16Data.buffer);
        }
      };

      const micAnalyser = audioContext.createAnalyser();
      micAnalyser.fftSize = 256;
      source.connect(micAnalyser);
      micAnalyserRef.current = micAnalyser;

      source.connect(workletNode);

      setIsRecording(true);
      setStatus("listening");
    } catch (err) {
      console.error("Error accessing microphone:", err);
      setStatus("error");
    }
  }, [connectWebSocket]);

  // ── Stop Recording ─────────────────────────────────────────────────────

  const stopRecording = useCallback(() => {
    if (vadRef.current) {
      vadRef.current.pause();
      vadRef.current = null;
    }

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
      wsRef.current.onclose = null; // Prevent reconnect loop
      wsRef.current.close();
      wsRef.current = null;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    playbackManagerRef.current?.reset();
    playbackManagerRef.current = null;
    micAnalyserRef.current = null;

    setIsRecording(false);
    setIsBotSpeaking(false);
    setStatus("disconnected");
  }, []);

  return {
    isRecording,
    status,
    isBotSpeaking,
    chatHistory,
    interimTranscript,
    micAnalyser: micAnalyserRef.current,
    botAnalyser: playbackManagerRef.current?.botAnalyser ?? null,
    startRecording,
    stopRecording,
  };
}
