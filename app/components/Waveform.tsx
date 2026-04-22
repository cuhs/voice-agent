/**
 * Waveform visualizer component.
 *
 * Renders a single animated waveform line on a canvas element,
 * switching between mic (green) and bot (blue) based on who is active.
 */

"use client";

import { useRef, useEffect, useCallback } from "react";
import { styles } from "@/app/styles/voiceAgent";

interface WaveformProps {
  micAnalyser: AnalyserNode | null;
  botAnalyser: AnalyserNode | null;
  isActive: boolean;
  isBotSpeaking: boolean;
}

export default function Waveform({ micAnalyser, botAnalyser, isActive, isBotSpeaking }: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationFrameRef = useRef<number>(0);

  const drawWaveform = useCallback(() => {
    if (!canvasRef.current) return;
    const canvas = canvasRef.current;
    const canvasCtx = canvas.getContext("2d");
    if (!canvasCtx) return;

    const bufferLength = micAnalyser ? micAnalyser.frequencyBinCount : 128;
    const micDataArray = new Uint8Array(bufferLength);
    if (micAnalyser) micAnalyser.getByteTimeDomainData(micDataArray);

    let botDataArray: Uint8Array<ArrayBuffer> | null = null;
    if (botAnalyser) {
      botDataArray = new Uint8Array(botAnalyser.frequencyBinCount);
      botAnalyser.getByteTimeDomainData(botDataArray);
    }

    canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
    canvasCtx.lineWidth = 2;

    const drawLine = (dataArray: Uint8Array<ArrayBuffer>, color: string) => {
      canvasCtx.beginPath();
      canvasCtx.strokeStyle = color;
      const sliceWidth = (canvas.width * 1.0) / bufferLength;
      let x = 0;
      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = (v * canvas.height) / 2;
        if (i === 0) {
          canvasCtx.moveTo(x, y);
        } else {
          canvasCtx.lineTo(x, y);
        }
        x += sliceWidth;
      }
      canvasCtx.lineTo(canvas.width, canvas.height / 2);
      canvasCtx.stroke();
    };

    let isBotActive = false;
    if (botDataArray) {
      for (let i = 0; i < botDataArray.length; i++) {
        if (Math.abs(128 - botDataArray[i]) > 2) isBotActive = true;
      }
    }

    if (isBotActive && botDataArray) {
      drawLine(botDataArray, "#60a5fa"); // Blue for bot
    } else if (micAnalyser) {
      drawLine(micDataArray, "#4ade80"); // Green for mic
    } else {
      drawLine(new Uint8Array(bufferLength).fill(128), "#475569");
    }

    animationFrameRef.current = requestAnimationFrame(drawWaveform);
  }, [micAnalyser, botAnalyser]);

  useEffect(() => {
    if (isActive && canvasRef.current) {
      drawWaveform();
    }
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = 0;
      }
    };
  }, [isActive, drawWaveform]);

  return (
    <div style={styles.speakingIndicator}>
      <canvas ref={canvasRef} width={160} height={32} style={styles.canvas}></canvas>
      <span>{isBotSpeaking ? "Medi is speaking..." : "Listening..."}</span>
    </div>
  );
}
