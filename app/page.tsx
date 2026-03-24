"use client";

import { useState, useRef } from "react";

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("idle");
  const [isReceiving, setIsReceiving] = useState(false);
  const [audioURL, setAudioURL] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      setAudioURL(null);
      audioChunksRef.current = [];

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
          setIsReceiving(true);
        }
      };

      mediaRecorder.onstop = () => {
        // When stopped, combine chunks into a single audio file and create a URL
        const audioBlob = new Blob(audioChunksRef.current, { type: mediaRecorder.mimeType });
        const url = URL.createObjectURL(audioBlob);
        setAudioURL(url);
      };

      mediaRecorder.start(250);
      setIsRecording(true);
      setStatus("listening");
    } catch (err) {
      console.error("Error accessing microphone:", err);
      setStatus("error: check console");
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
    }

    mediaRecorderRef.current = null;
    streamRef.current = null;

    setIsRecording(false);
    setStatus("stopped");
    setIsReceiving(false);
  };

  const handleToggle = () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  return (
    <div style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>Voice Agent</h1>
      <p>Status: <strong>{status}</strong></p>

      {isReceiving && <p style={{ color: "green", fontWeight: "bold" }}>Receiving audio...</p>}

      <button onClick={handleToggle} style={{ marginBottom: "1rem" }}>
        {isRecording ? "Stop" : "Start"}
      </button>

      {audioURL && (
        <div>
          <p>Playback:</p>
          <audio src={audioURL} controls />
        </div>
      )}
    </div>
  );
}
