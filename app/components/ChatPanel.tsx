/**
 * Chat panel component.
 *
 * Renders the scrollable message list. It displays user bubbles, bot bubbles,
 * and the interim (typing) transcript from Deepgram.
 * It also handles automatically scrolling to the bottom when new messages arrive.
 */

"use client";

import { useRef, useEffect } from "react";
import { styles } from "@/app/styles/voiceAgent";

export type Message = {
  role: "user" | "bot";
  text: string;
};

interface ChatPanelProps {
  chatHistory: Message[];
  interimTranscript: string;
}

export default function ChatPanel({ chatHistory, interimTranscript }: ChatPanelProps) {
  // Reference to an empty div at the bottom of the list for auto-scrolling
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever history or interim transcript updates
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory, interimTranscript]);

  return (
    <div style={styles.chatContainer}>
      {/* Empty state: Displayed before the user says anything */}
      {chatHistory.length === 0 && !interimTranscript && (
        <div style={styles.emptyState}>
          <h2>Welcome to Greenfield Medical Group</h2>
          <p>Click start and say &quot;Hello&quot; to begin.</p>
        </div>
      )}

      {/* Render finalized messages */}
      {chatHistory.map((msg, idx) => (
        <div
          key={idx}
          style={{
            ...styles.messageWrapper,
            justifyContent: msg.role === "user" ? "flex-end" : "flex-start",
          }}
        >
          <div style={msg.role === "user" ? styles.userMessage : styles.botMessage}>
            {msg.text}
          </div>
        </div>
      ))}

      {/* Render the interim transcript as a typing bubble */}
      {interimTranscript && (
        <div style={{ ...styles.messageWrapper, justifyContent: "flex-end" }}>
          <div style={{ ...styles.userMessage, ...styles.interimMessage }}>
            {interimTranscript}...
          </div>
        </div>
      )}
      
      {/* Invisible anchor for scrolling */}
      <div ref={messagesEndRef} />
    </div>
  );
}
