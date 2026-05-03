/**
 * Main application page.
 *
 * This serves as the UI container. It uses the `useVoiceSession` hook to 
 * drive the state (recording status, messages, etc.) and passes that state 
 * down to the UI components (ChatPanel and Waveform).
 */

"use client";

import { useVoiceSession } from "@/app/hooks/useVoiceSession";
import ChatPanel from "@/app/components/ChatPanel";
import Waveform from "@/app/components/Waveform";
import { styles } from "@/app/styles/voiceAgent";

export default function Home() {
  // Extract all state and control functions from our custom hook
  const {
    isRecording,
    status,
    isBotSpeaking,
    chatHistory,
    interimTranscript,
    micAnalyser,
    botAnalyser,
    startRecording,
    stopRecording,
  } = useVoiceSession();

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
          {/* Main Action Button */}
          <button
            onClick={handleToggle}
            style={isRecording ? styles.stopButton : styles.startButton}
          >
            {isRecording ? "End Session" : "Start Voice Assistant"}
          </button>
        </div>
      </header>

      {/* Main chat UI area */}
      <main style={styles.main}>
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
    </div>
  );
}
