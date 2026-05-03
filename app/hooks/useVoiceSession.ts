/**
 * Custom hook for the voice session lifecycle.
 *
 * This is the frontend brain of the voice agent. It manages:
 *  - WebSocket connection to the backend (with reconnect backoff)
 *  - Microphone capture via Web Audio API + AudioWorklet (for low latency)
 *  - VAD (Voice Activity Detection) for speech start/end logic
 *  - Bot audio playback via AudioPlaybackManager
 *  - Chat history and transcript state to drive the UI
 */

"use client";

import { useState, useRef, useCallback } from "react";
import { AudioPlaybackManager } from "@/app/lib/audioManager";
import type { Message } from "@/app/components/ChatPanel";

export function useVoiceSession() {
  // ── State variables driving the UI ───────────────────────────────────────
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("disconnected"); // disconnected, connecting, connected, listening, reconnecting, error
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);
  const [chatHistory, setChatHistory] = useState<Message[]>([]);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [devLogs, setDevLogs] = useState<string[]>([]);
  const [backendState, setBackendState] = useState<string>("GREETING");
  const [pipelineStage, setPipelineStage] = useState<{stage: string; detail: string}>({stage: "", detail: ""});

  // ── Mutable Refs (don't trigger re-renders) ────────────────────────────
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const vadRef = useRef<any>(null);
  const playbackManagerRef = useRef<AudioPlaybackManager | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  
  // Connection retry state
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  // ignoreAudioRef is set to true when the user is speaking, so we 
  // drop any incoming bot audio chunks that might still be arriving.
  const ignoreAudioRef = useRef(false);

  // ── WebSocket Logic ────────────────────────────────────────────────────

  const connectWebSocket = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    const ws = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/audio");
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      reconnectAttemptsRef.current = 0; // Reset backoff on success
    };

    ws.onmessage = async (event) => {
      // 1. Binary Data: This is a PCM16 audio chunk from ElevenLabs via the backend
      if (event.data instanceof Blob) {
        if (ignoreAudioRef.current) return; // Discard audio if user is speaking
        setIsBotSpeaking(true);
        const arrayBuffer = await event.data.arrayBuffer();
        playbackManagerRef.current?.scheduleChunk(arrayBuffer);
      } 
      // 2. Text Data: JSON control messages (transcripts, chat history updates)
      else if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data);

          if (msg.type === "transcript") {
            // Update UI with what the user is saying
            if (msg.is_final) {
              setChatHistory((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === "user") {
                  return [...prev.slice(0, -1), { role: "user", text: last.text + " " + msg.text }];
                }
                return [...prev, { role: "user", text: msg.text }];
              });
              setInterimTranscript(""); // Clear interim once we have final text
            } else {
              setInterimTranscript(msg.text); // Show typing/streaming effect
            }
          } else if (msg.type === "bot_response") {
            // Once the bot actually starts responding, we clear the ignore flag
            // so we can hear its audio.
            ignoreAudioRef.current = false;
            setChatHistory((prev) => [...prev, { role: "bot", text: msg.text }]);
          } else if (msg.type === "dev_log") {
            setDevLogs((prev) => [...prev, msg.content]);
          } else if (msg.type === "state_update") {
            setBackendState(msg.state);
          } else if (msg.type === "pipeline_stage") {
            setPipelineStage({stage: msg.stage, detail: msg.detail || ""});
          }
          // interrupt_ack — no action needed on frontend, just an acknowledgement
        } catch (e) {
          console.error("Failed to parse websocket message", e);
        }
      }
    };

    ws.onclose = () => {
      // If the close was unexpected (not requested by stopRecording), try to reconnect
      if (wsRef.current === ws) {
        setStatus("reconnecting");
        // Exponential backoff: 1s, 2s, 4s, 8s, up to max 10s.
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

  // ── Start Recording (Initialization Phase) ──────────────────────────────

  const startRecording = useCallback(async () => {
    try {
      setStatus("connecting");
      setChatHistory([]);
      setInterimTranscript("");
      setDevLogs([]);
      setBackendState("GREETING");
      setPipelineStage({stage: "", detail: ""});
      
      // 1. Open the WebSocket to the backend
      connectWebSocket();

      // 2. Request microphone access
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // 3. Initialize ONNX and VAD (Voice Activity Detection) model
      const { MicVAD } = await import("@ricky0123/vad-web");
      const ort = await import("onnxruntime-web");

      ort.env.wasm.wasmPaths = "/";
      ort.env.wasm.numThreads = 1; // Limit threads to avoid heavy CPU usage

      const myvad = await MicVAD.new({
        getStream: async () => stream,
        baseAssetPath: "/",
        onnxWASMBasePath: "/",
        positiveSpeechThreshold: 0.6, // Sensitivity
        redemptionMs: 600, // How long to wait after silence to call it 'speech end'
        minSpeechMs: 100,
        onSpeechStart: () => {
          console.log("VAD: Speech Start");
          
          // User started talking! Immediately cut off the bot.
          ignoreAudioRef.current = true;
          playbackManagerRef.current?.interrupt();
          setIsBotSpeaking(false);

          // Tell the backend to stop generating text/audio
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: "interrupt" }));
          }
        },
        onSpeechEnd: () => {
          console.log("VAD: Speech End");
          // Action on speech end is handled by Deepgram's UtteranceEnd event 
          // on the backend, rather than purely local VAD.
        },
      });
      vadRef.current = myvad;
      myvad.start();

      // 4. Setup AudioContext and AudioWorklet for raw PCM extraction
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)({
        sampleRate: 16000, // Required for Deepgram and ElevenLabs
      });
      audioContextRef.current = audioContext;

      // Create playback manager (handles bot audio)
      playbackManagerRef.current = new AudioPlaybackManager(audioContext, () => {
        setIsBotSpeaking(false);
      });

      // Load custom processor to convert microphone input to PCM16 binary chunks
      await audioContext.audioWorklet.addModule("/audio-processor.js");

      const source = audioContext.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioContext, "audio-processor");
      workletNodeRef.current = workletNode;

      workletNode.port.onmessage = (event) => {
        // Convert Float32Array from mic to Int16Array for backend
        const float32Data = event.data as Float32Array;
        const int16Data = new Int16Array(float32Data.length);
        for (let i = 0; i < float32Data.length; i++) {
          const s = Math.max(-1, Math.min(1, float32Data[i]));
          int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }

        // Send binary audio chunks over WebSocket
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(int16Data.buffer);
        }
      };

      // Create analyser for visual waveform (user mic)
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

  // ── Stop Recording (Teardown Phase) ────────────────────────────────────

  const stopRecording = useCallback(() => {
    // 1. Stop VAD
    if (vadRef.current) {
      vadRef.current.pause();
      vadRef.current = null;
    }

    // 2. Stop Audio Worklet
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }

    // 3. Stop Audio Context
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(console.error);
      audioContextRef.current = null;
    }

    // 4. Stop hardware microphone stream
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    // 5. Close WebSocket connection cleanly (prevents reconnect loop)
    if (wsRef.current) {
      wsRef.current.onclose = null; 
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
    devLogs,
    backendState,
    pipelineStage,
    micAnalyser: micAnalyserRef.current,
    botAnalyser: playbackManagerRef.current?.botAnalyser ?? null,
    startRecording,
    stopRecording,
  };
}
