/**
 * Main application page.
 *
 * This serves as the UI container. It uses the `useVoiceSession` hook to 
 * drive the state (recording status, messages, etc.) and passes that state 
 * down to the UI components (ChatPanel and Waveform).
 */

"use client";

import { useState } from "react";
import { useVoiceSession } from "@/app/hooks/useVoiceSession";
import ChatPanel from "@/app/components/ChatPanel";
import Waveform from "@/app/components/Waveform";
import DeveloperPanel from "@/app/components/DeveloperPanel";
import { styles } from "@/app/styles/voiceAgent";

export default function Home() {
  // Extract all state and control functions from our custom hook
  const {
    isRecording,
    status,
    isBotSpeaking,
    chatHistory,
    interimTranscript,
    devLogs,
    backendState,
    pipelineStage,
    micAnalyser,
    botAnalyser,
    startRecording,
    stopRecording,
  } = useVoiceSession();

  const [isDevMode, setIsDevMode] = useState(false);

  // Toggle button handler
  const handleToggle = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  // Dynamic color coding for the status dot
  const dotColor =
    status === "listening"
      ? "#4ade80" // Green
      : status === "connected"
        ? "#fbbf24" // Yellow
        : status === "reconnecting"
          ? "#f97316" // Orange
          : "#f87171"; // Red

  return (
    <div style={styles.page}>
      {/* Header section with branding and controls */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <div style={styles.logoIcon}></div>
          <h1 style={styles.title}>Medi Assistant</h1>
        </div>
        <div style={styles.controlPanel}>
          {/* Status Indicator */}
          <div style={styles.statusIndicator}>
            <div style={{ ...styles.dot, backgroundColor: dotColor }}></div>
            <span style={styles.statusText}>{status.toUpperCase()}</span>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#64748b", cursor: "pointer", marginRight: "12px" }}>
            <input type="checkbox" checked={isDevMode} onChange={(e) => setIsDevMode(e.target.checked)} />
            Dev Mode
          </label>
          {/* Main Action Button */}
          <button
            onClick={handleToggle}
            style={isRecording ? styles.stopButton : styles.startButton}
          >
            {isRecording ? "End Session" : "Start Voice Assistant"}
          </button>
        </div>
      </header>

      {/* Main chat UI area and Developer Panel */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <main style={{ ...styles.main, flex: isDevMode ? 1 : 1, maxWidth: isDevMode ? "50%" : "100%" }}>
          <ChatPanel chatHistory={chatHistory} interimTranscript={interimTranscript} />

          {/* Only show waveform when the session is active */}
          {isRecording && (
            <Waveform
              micAnalyser={micAnalyser}
              botAnalyser={botAnalyser}
              isActive={isRecording}
              isBotSpeaking={isBotSpeaking}
            />
          )}
        </main>
        
        {isDevMode && (
          <DeveloperPanel backendState={backendState} devLogs={devLogs} pipelineStage={pipelineStage} />
        )}
      </div>
    </div>
  );
}
