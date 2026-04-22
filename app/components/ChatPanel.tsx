/**
 * Chat panel component.
 *
 * Renders the scrollable message list (user bubbles, bot bubbles,
 * interim transcript) and the empty-state welcome message.
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
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory, interimTranscript]);

  return (
    <div style={styles.chatContainer}>
      {chatHistory.length === 0 && !interimTranscript && (
        <div style={styles.emptyState}>
          <h2>Welcome to Greenfield Medical Group</h2>
          <p>Click start and say &quot;Hello&quot; to begin.</p>
        </div>
      )}

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

      {interimTranscript && (
        <div style={{ ...styles.messageWrapper, justifyContent: "flex-end" }}>
          <div style={{ ...styles.userMessage, ...styles.interimMessage }}>
            {interimTranscript}...
          </div>
        </div>
      )}
      <div ref={messagesEndRef} />
    </div>
  );
}
