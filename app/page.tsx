"use client";

import { useVoiceSession } from "@/app/hooks/useVoiceSession";
import ChatPanel from "@/app/components/ChatPanel";
import Waveform from "@/app/components/Waveform";
import { styles } from "@/app/styles/voiceAgent";

export default function Home() {
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

  const handleToggle = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  const dotColor =
    status === "listening"
      ? "#4ade80"
      : status === "connected"
        ? "#fbbf24"
        : status === "reconnecting"
          ? "#f97316"
          : "#f87171";

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <div style={styles.logo}>
          <div style={styles.logoIcon}></div>
          <h1 style={styles.title}>Medi Assistant</h1>
        </div>
        <div style={styles.controlPanel}>
          <div style={styles.statusIndicator}>
            <div style={{ ...styles.dot, backgroundColor: dotColor }}></div>
            <span style={styles.statusText}>{status.toUpperCase()}</span>
          </div>
          <button
            onClick={handleToggle}
            style={isRecording ? styles.stopButton : styles.startButton}
          >
            {isRecording ? "End Session" : "Start Voice Assistant"}
          </button>
        </div>
      </header>

      <main style={styles.main}>
        <ChatPanel chatHistory={chatHistory} interimTranscript={interimTranscript} />

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
