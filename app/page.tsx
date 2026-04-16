"use client";

import { useState, useRef, useEffect } from "react";

type Message = {
  role: "user" | "bot";
  text: string;
};

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("disconnected");
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);

  const [chatHistory, setChatHistory] = useState<Message[]>([]);
  const [interimTranscript, setInterimTranscript] = useState("");

  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const vadRef = useRef<any>(null);
  const nextStartTimeRef = useRef<number>(0);
  const scheduledSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [chatHistory, interimTranscript]);

  const connectWebSocket = () => {
    const ws = new WebSocket("ws://127.0.0.1:8000/api/v1/ws/audio");
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
    };

    ws.onmessage = async (event) => {
      if (event.data instanceof Blob) {
        setIsBotSpeaking(true);
        const arrayBuffer = await event.data.arrayBuffer();

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

          scheduledSourcesRef.current.push(source);

          const currentTime = audioContextRef.current.currentTime;
          if (nextStartTimeRef.current < currentTime) {
            nextStartTimeRef.current = currentTime + 0.1;
          }

          source.start(nextStartTimeRef.current);
          nextStartTimeRef.current += audioBuffer.duration;

          source.onended = () => {
            scheduledSourcesRef.current = scheduledSourcesRef.current.filter((s) => s !== source);
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
              setChatHistory(prev => {
                const last = prev[prev.length - 1];
                if (last && last.role === "user") {
                  return [...prev.slice(0, -1), { role: "user", text: last.text + " " + msg.text }];
                } else {
                  return [...prev, { role: "user", text: msg.text }];
                }
              });
              setInterimTranscript("");
            } else {
              setInterimTranscript(msg.text);
            }
          } else if (msg.type === "bot_response") {
            setChatHistory(prev => [...prev, { role: "bot", text: msg.text }]);
          }
        } catch (e) {
          console.error("Failed to parse websocket message", e);
        }
      }
    };

    ws.onclose = () => {
      setStatus("disconnected");
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      setStatus("error");
    };
  };

  const startRecording = async () => {
    try {
      setStatus("connecting");
      setChatHistory([]);
      setInterimTranscript("");
      nextStartTimeRef.current = 0;
      scheduledSourcesRef.current = [];
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
        positiveSpeechThreshold: 0.5,
        redemptionMs: 600,
        onSpeechStart: () => {
          console.log("VAD: Speech Start");
          if (scheduledSourcesRef.current.length > 0) {
            console.log("Interrupting bot playback...");
            scheduledSourcesRef.current.forEach((s) => {
              try { s.stop(); } catch (e) { }
              try { s.disconnect(); } catch (e) { }
            });
            scheduledSourcesRef.current = [];
            nextStartTimeRef.current = 0;
            setIsBotSpeaking(false);

            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
              wsRef.current.send(JSON.stringify({ type: "interrupt" }));
            }
          }
        },
        onSpeechEnd: (audio: Float32Array) => {
          console.log("VAD: Speech End");
        }
      });
      vadRef.current = myvad;
      myvad.start();

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
      setStatus("listening");
    } catch (err) {
      console.error("Error accessing microphone:", err);
      setStatus("error");
    }
  };

  const stopRecording = () => {
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
      wsRef.current.close();
      wsRef.current = null;
    }

    setIsRecording(false);
    setStatus("disconnected");
  };

  const handleToggle = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <div style={styles.logo}>
          <div style={styles.logoIcon}></div>
          <h1 style={styles.title}>Medi Assistant</h1>
        </div>
        <div style={styles.controlPanel}>
          <div style={styles.statusIndicator}>
            <div style={{ ...styles.dot, backgroundColor: status === "listening" ? "#4ade80" : status === "connected" ? "#fbbf24" : "#f87171" }}></div>
            <span style={styles.statusText}>{status.toUpperCase()}</span>
          </div>
          <button onClick={handleToggle} style={isRecording ? styles.stopButton : styles.startButton}>
            {isRecording ? "End Session" : "Start Voice Assistant"}
          </button>
        </div>
      </header>

      <main style={styles.main}>
        <div style={styles.chatContainer}>
          {chatHistory.length === 0 && !interimTranscript && (
            <div style={styles.emptyState}>
              <h2>Welcome to Greenfield Medical Group</h2>
              <p>Click start and say "Hello" to begin.</p>
            </div>
          )}

          {chatHistory.map((msg, idx) => (
            <div key={idx} style={{ ...styles.messageWrapper, justifyContent: msg.role === "user" ? "flex-end" : "flex-start" }}>
              <div style={msg.role === "user" ? styles.userMessage : styles.botMessage}>
                {msg.text}
              </div>
            </div>
          ))}

          {interimTranscript && (
            <div style={{ ...styles.messageWrapper, justifyContent: "flex-end" }}>
              <div style={{ ...styles.userMessage, ...styles.interimMessage }}>
                {interimTranscript}...
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {isBotSpeaking && (
          <div style={styles.speakingIndicator}>
            <div style={styles.waveform}>
              <div className="bar"></div>
              <div className="bar"></div>
              <div className="bar"></div>
              <div className="bar"></div>
            </div>
            <span>Medi is speaking...</span>
          </div>
        )}
      </main>

      <style>{`
        @keyframes pulse {
          0% { height: 4px; }
          50% { height: 16px; }
          100% { height: 4px; }
        }
        .bar {
            width: 4px;
            background: #3b82f6;
            margin: 0 2px;
            border-radius: 4px;
            animation: pulse 1s infinite ease-in-out;
        }
        .bar:nth-child(1) { animation-delay: 0.0s; }
        .bar:nth-child(2) { animation-delay: 0.2s; }
        .bar:nth-child(3) { animation-delay: 0.4s; }
        .bar:nth-child(4) { animation-delay: 0.1s; }
      `}</style>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    backgroundColor: "#0f172a",
    fontFamily: "system-ui, -apple-system, sans-serif",
    color: "#e2e8f0"
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "1.5rem 2rem",
    backgroundColor: "rgba(15, 23, 42, 0.8)",
    backdropFilter: "blur(12px)",
    borderBottom: "1px solid #1e293b",
    zIndex: 10
  },
  logo: {
    display: "flex",
    alignItems: "center",
    gap: "0.75rem"
  },
  logoIcon: {
    width: "24px",
    height: "24px",
    borderRadius: "8px",
    background: "linear-gradient(135deg, #3b82f6, #8b5cf6)"
  },
  title: {
    fontSize: "1.25rem",
    fontWeight: 600,
    margin: 0,
    background: "linear-gradient(135deg, #e2e8f0, #94a3b8)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent"
  },
  controlPanel: {
    display: "flex",
    alignItems: "center",
    gap: "1.5rem"
  },
  statusIndicator: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem"
  },
  dot: {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    transition: "background-color 0.3s ease"
  },
  statusText: {
    fontSize: "0.875rem",
    fontWeight: 600,
    color: "#94a3b8",
    letterSpacing: "0.05em"
  },
  startButton: {
    padding: "0.75rem 1.5rem",
    borderRadius: "999px",
    border: "none",
    background: "linear-gradient(135deg, #3b82f6, #2563eb)",
    color: "white",
    fontSize: "0.875rem",
    fontWeight: 600,
    cursor: "pointer",
    boxShadow: "0 4px 14px 0 rgba(37, 99, 235, 0.39)",
    transition: "transform 0.2s, box-shadow 0.2s"
  },
  stopButton: {
    padding: "0.75rem 1.5rem",
    borderRadius: "999px",
    border: "none",
    background: "linear-gradient(135deg, #ef4444, #dc2626)",
    color: "white",
    fontSize: "0.875rem",
    fontWeight: 600,
    cursor: "pointer",
    boxShadow: "0 4px 14px 0 rgba(239, 68, 68, 0.39)",
    transition: "transform 0.2s, box-shadow 0.2s"
  },
  main: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    padding: "2rem",
    overflow: "hidden",
    position: "relative"
  },
  chatContainer: {
    flex: 1,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: "1.5rem",
    paddingBottom: "4rem",
    maxWidth: "800px",
    margin: "0 auto",
    width: "100%",
    scrollbarWidth: "none"
  },
  emptyState: {
    margin: "auto",
    textAlign: "center",
    color: "#64748b"
  },
  messageWrapper: {
    display: "flex",
    width: "100%",
    animation: "fadeIn 0.3s ease"
  },
  userMessage: {
    maxWidth: "80%",
    padding: "1rem 1.25rem",
    borderRadius: "1.5rem",
    borderBottomRightRadius: "0.5rem",
    backgroundColor: "#1e293b",
    color: "#f8fafc",
    fontSize: "1rem",
    lineHeight: 1.5,
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)"
  },
  botMessage: {
    maxWidth: "80%",
    padding: "1rem 1.25rem",
    borderRadius: "1.5rem",
    borderBottomLeftRadius: "0.5rem",
    background: "linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(139, 92, 246, 0.1))",
    border: "1px solid rgba(59, 130, 246, 0.2)",
    color: "#f8fafc",
    fontSize: "1rem",
    lineHeight: 1.5,
    backdropFilter: "blur(8px)"
  },
  interimMessage: {
    opacity: 0.6,
    borderStyle: "dashed",
    borderWidth: "1px",
    borderColor: "#475569"
  },
  speakingIndicator: {
    position: "absolute",
    bottom: "2rem",
    left: "50%",
    transform: "translateX(-50%)",
    display: "flex",
    alignItems: "center",
    gap: "0.75rem",
    padding: "0.75rem 1.5rem",
    backgroundColor: "rgba(30, 41, 59, 0.9)",
    backdropFilter: "blur(12px)",
    borderRadius: "999px",
    border: "1px solid rgba(59, 130, 246, 0.3)",
    color: "#93c5fd",
    fontSize: "0.875rem",
    fontWeight: 500,
    boxShadow: "0 10px 15px -3px rgba(0, 0, 0, 0.2), 0 0 20px rgba(59, 130, 246, 0.15)",
    zIndex: 20
  },
  waveform: {
    display: "flex",
    alignItems: "center",
    height: "16px"
  }
};
