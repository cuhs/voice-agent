"use client";

import { useEffect, useRef, useState } from "react";

interface DeveloperPanelProps {
  backendState: string;
  devLogs: string[];
  pipelineStage: { stage: string; detail: string };
}

const STATES = [
  "GREETING",
  "VERIFICATION",
  "AUTHENTICATED",
  "SERVICING",
  "SCHEDULING",
  "CLOSING",
];

// Pipeline stages mapped to the walkthrough.md data flow
const PIPELINE_NODES = [
  { id: "capture", label: "Audio Capture", desc: "Mic → AudioWorklet → PCM16 → WebSocket" },
  { id: "stt", label: "Speech-to-Text", desc: "Deepgram Nova-2 streaming transcription" },
  { id: "safety", label: "Safety Classifier", desc: "Pre-LLM keyword + regex guardrails" },
  { id: "orchestration", label: "Orchestration", desc: "State machine + prompt assembly" },
  { id: "llm", label: "LLM Generation", desc: "Groq (Llama 3.1 8B) inference" },
  { id: "tools", label: "Tool Execution", desc: "Patient lookup / appointments / labs" },
  { id: "guardrails", label: "Response Validation", desc: "Post-LLM hallucination check" },
  { id: "tts", label: "Text-to-Speech", desc: "ElevenLabs streaming synthesis" },
  { id: "playback", label: "Audio Playback", desc: "Frontend gapless PCM scheduling" },
];

export default function DeveloperPanel({ backendState, devLogs, pipelineStage }: DeveloperPanelProps) {
  const logEndRef = useRef<HTMLDivElement>(null);
  const [activeTab, setActiveTab] = useState<"pipeline" | "logs">("pipeline");

  useEffect(() => {
    if (activeTab === "logs") {
      logEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [devLogs, activeTab]);

  const tabStyle = (tab: string): React.CSSProperties => ({
    padding: "8px 16px",
    fontSize: "12px",
    fontWeight: 600,
    cursor: "pointer",
    backgroundColor: "transparent",
    border: "none",
    borderBottomWidth: "2px",
    borderBottomStyle: "solid",
    borderBottomColor: activeTab === tab ? "#3b82f6" : "transparent",
    color: activeTab === tab ? "#3b82f6" : "#64748b",
    transition: "all 0.15s ease",
  });

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%", borderLeft: "1px solid #1e293b", backgroundColor: "#0f172a", color: "#e2e8f0" }}>
      {/* Tab Bar */}
      <div style={{ display: "flex", borderBottom: "1px solid #1e293b", backgroundColor: "#0f172a" }}>
        <button style={tabStyle("pipeline")} onClick={() => setActiveTab("pipeline")}>Pipeline</button>
        <button style={tabStyle("logs")} onClick={() => setActiveTab("logs")}>Logs</button>
        {/* State Machine - always visible in header */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "6px", paddingRight: "12px" }}>
          {STATES.map((state) => {
            const isActive = state === backendState;
            return (
              <div
                key={state}
                style={{
                  padding: "3px 8px",
                  borderRadius: "4px",
                  fontSize: "9px",
                  fontWeight: isActive ? 700 : 500,
                  backgroundColor: isActive ? "rgba(34, 197, 94, 0.15)" : "rgba(100, 116, 139, 0.1)",
                  color: isActive ? "#4ade80" : "#475569",
                  border: isActive ? "1px solid #22c55e" : "1px solid #1e293b",
                  transition: "all 0.2s ease",
                  boxShadow: isActive ? "0 0 6px rgba(34, 197, 94, 0.3)" : "none",
                }}
              >
                {state}
              </div>
            );
          })}
        </div>
      </div>

      {/* Pipeline Tab */}
      {activeTab === "pipeline" && (
        <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            {PIPELINE_NODES.map((node, i) => {
              const isActive = pipelineStage.stage === node.id;
              const isPast = (() => {
                const activeIdx = PIPELINE_NODES.findIndex(n => n.id === pipelineStage.stage);
                return activeIdx > i;
              })();

              return (
                <div key={node.id}>
                  {/* Node */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      padding: "10px 14px",
                      borderRadius: "8px",
                      backgroundColor: isActive ? "rgba(59, 130, 246, 0.12)" : isPast ? "rgba(34, 197, 94, 0.06)" : "rgba(30, 41, 59, 0.5)",
                      border: isActive ? "1px solid #3b82f6" : isPast ? "1px solid rgba(34, 197, 94, 0.2)" : "1px solid #1e293b",
                      transition: "all 0.3s ease",
                      boxShadow: isActive ? "0 0 12px rgba(59, 130, 246, 0.25)" : "none",
                    }}
                  >
                    {/* Status indicator */}
                    <div style={{
                      width: "8px",
                      height: "8px",
                      borderRadius: "50%",
                      marginRight: "12px",
                      backgroundColor: isActive ? "#3b82f6" : isPast ? "#22c55e" : "#334155",
                      boxShadow: isActive ? "0 0 6px #3b82f6" : "none",
                      transition: "all 0.3s ease",
                      animation: isActive ? "pulse 1.5s ease-in-out infinite" : "none",
                    }} />

                    <div style={{ flex: 1 }}>
                      <div style={{
                        fontSize: "13px",
                        fontWeight: isActive ? 600 : 500,
                        color: isActive ? "#93c5fd" : isPast ? "#86efac" : "#94a3b8",
                        transition: "color 0.3s ease",
                      }}>
                        {node.label}
                      </div>
                      <div style={{
                        fontSize: "10px",
                        color: isActive ? "#60a5fa" : "#475569",
                        marginTop: "2px",
                      }}>
                        {isActive && pipelineStage.detail ? pipelineStage.detail : node.desc}
                      </div>
                    </div>

                    {/* Data format badge */}
                    {isActive && (
                      <div style={{
                        fontSize: "9px",
                        padding: "2px 6px",
                        borderRadius: "3px",
                        backgroundColor: "rgba(59, 130, 246, 0.2)",
                        color: "#93c5fd",
                        fontFamily: "monospace",
                      }}>
                        ACTIVE
                      </div>
                    )}
                    {isPast && (
                      <div style={{
                        fontSize: "9px",
                        padding: "2px 6px",
                        borderRadius: "3px",
                        backgroundColor: "rgba(34, 197, 94, 0.15)",
                        color: "#86efac",
                        fontFamily: "monospace",
                      }}>
                        ✓ DONE
                      </div>
                    )}
                  </div>

                  {/* Connector line */}
                  {i < PIPELINE_NODES.length - 1 && (
                    <div style={{
                      width: "2px",
                      height: "8px",
                      backgroundColor: isPast ? "#22c55e" : "#1e293b",
                      marginLeft: "17px",
                      transition: "background-color 0.3s ease",
                    }} />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Logs Tab */}
      {activeTab === "logs" && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "16px", overflow: "hidden" }}>
          <div style={{ flex: 1, overflowY: "auto", fontFamily: "monospace", fontSize: "12px", color: "#4ade80", paddingRight: "8px" }}>
            {devLogs.length === 0 && (
              <div style={{ color: "#475569", fontStyle: "italic" }}>Waiting for events...</div>
            )}
            {devLogs.map((log, i) => (
              <div key={i} style={{ marginBottom: "6px", wordBreak: "break-all", borderBottom: "1px solid #1e293b", paddingBottom: "4px" }}>
                <span style={{ color: "#64748b", marginRight: "8px", fontSize: "10px" }}>{String(i + 1).padStart(3, "0")}</span>
                <span style={{
                  color: log.startsWith("[STT]") ? "#38bdf8"
                    : log.startsWith("[Phase") ? "#a78bfa"
                      : log.startsWith("[TOOL]") ? "#fbbf24"
                        : log.startsWith("[TTS]") ? "#f472b6"
                          : "#4ade80",
                }}>
                  {log}
                </span>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* Pulse animation */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
