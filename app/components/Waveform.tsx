/**
 * Waveform visualizer component.
 *
 * Renders a single animated waveform line on a canvas element.
 * It constantly pulls frequency data from the AnalyserNodes, and 
 * switches between the microphone (green) and bot audio (blue) 
 * dynamically based on who is currently making sound.
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

    // 1. Fetch Mic Data
    const bufferLength = micAnalyser ? micAnalyser.frequencyBinCount : 128;
    const micDataArray = new Uint8Array(bufferLength);
    if (micAnalyser) micAnalyser.getByteTimeDomainData(micDataArray);

    // 2. Fetch Bot Data
    let botDataArray: Uint8Array<ArrayBuffer> | null = null;
    if (botAnalyser) {
      botDataArray = new Uint8Array(botAnalyser.frequencyBinCount);
      botAnalyser.getByteTimeDomainData(botDataArray);
    }

    // Clear previous frame
    canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
    canvasCtx.lineWidth = 2;

    // Helper to draw a single line across the canvas based on an array of bytes
    const drawLine = (dataArray: Uint8Array<ArrayBuffer>, color: string) => {
      canvasCtx.beginPath();
      canvasCtx.strokeStyle = color;
      const sliceWidth = (canvas.width * 1.0) / bufferLength;
      let x = 0;
      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0; // Normalize 0-255 around center 128
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

    // 3. Determine who is speaking
    // We check if the bot array has any values significantly deviating from the 
    // baseline (128). If yes, the bot is making sound.
    let isBotActive = false;
    if (botDataArray) {
      for (let i = 0; i < botDataArray.length; i++) {
        if (Math.abs(128 - botDataArray[i]) > 2) isBotActive = true;
      }
    }

    // 4. Render
    if (isBotActive && botDataArray) {
      drawLine(botDataArray, "#60a5fa"); // Blue for bot
    } else if (micAnalyser) {
      drawLine(micDataArray, "#4ade80"); // Green for mic
    } else {
      drawLine(new Uint8Array(bufferLength).fill(128), "#475569"); // Gray baseline
    }

    // Loop animation
    animationFrameRef.current = requestAnimationFrame(drawWaveform);
  }, [micAnalyser, botAnalyser]);

  // Start/Stop animation loop when component mounts/unmounts
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
