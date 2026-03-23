"use client";

import { useState } from "react";

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("idle");

  const handleToggle = () => {
    if (isRecording) {
      setIsRecording(false);
      setStatus("stopped");
    } else {
      setIsRecording(true);
      setStatus("listening");
    }
  };

  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>Voice Agent</h1>
      <p>Status: <strong>{status}</strong></p>
      <button
        onClick={handleToggle}
      >
        {isRecording ? "Stop" : "Start"}
      </button>
    </div>
  );
}
