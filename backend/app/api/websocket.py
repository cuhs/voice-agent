"""
WebSocket handler for the Medi voice assistant.

This module is the transport layer only — it wires together:
  - Frontend WebSocket ↔ Deepgram STT
  - Deepgram transcript events → LLM orchestration (Phase 1)
  - LLM response → ElevenLabs TTS → Frontend audio (Phase 2)

All business logic lives in orchestrator.py, tools.py, guardrails.py, and prompts.py.
"""

import asyncio
import base64
import json
import ssl

import certifi
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.orchestrator import run_orchestration
from app.api.prompts import get_system_prompt
from app.core.config import settings

router = APIRouter()

DG_PARAMS = (
    "model=nova-2&encoding=linear16&sample_rate=16000&channels=1"
    "&interim_results=true&utterance_end_ms=1000&vad_events=true"
)
DEEPGRAM_URL = f"wss://api.deepgram.com/v1/listen?{DG_PARAMS}"


# ── TTS Streaming (Phase 2) ──────────────────────────────────────────────────

async def stream_tts(text: str, websocket: WebSocket) -> None:
    """Stream text through ElevenLabs TTS and send audio chunks to the frontend."""
    api_key = getattr(settings, "elevenlabs_api_key", None)
    if not api_key:
        return

    voice_id = "pNInz6obpgDQGcFmaJgB"
    url = (
        f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        f"/stream-input?model_id=eleven_flash_v2_5&output_format=pcm_16000"
    )

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    tts_socket = None

    try:
        tts_socket = await websockets.connect(url, ssl=ssl_ctx)
        print("Connected to ElevenLabs TTS!")

        # Init handshake
        await tts_socket.send(json.dumps({
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
            "xi_api_key": api_key,
            "chunk_length_schedule": [50],
        }))

        async def receive_audio():
            try:
                while True:
                    msg = await tts_socket.recv()
                    data = json.loads(msg)

                    if "error" in data:
                        print(f"ElevenLabs API Error: {data['error']}")
                    if data.get("audio"):
                        print(".", end="", flush=True)
                        audio_bytes = base64.b64decode(data["audio"])
                        await websocket.send_bytes(audio_bytes)
                    if data.get("isFinal"):
                        print("\nElevenLabs reports isFinal: True")
                        break
            except websockets.exceptions.ConnectionClosed as e:
                print(f"\nElevenLabs websocket closed. Code: {e.code}, Reason: {e.reason}")
            except RuntimeError as e:
                if "Unexpected ASGI message" in str(e) or "websocket.send" in str(e):
                    pass  # Benign: hot-reload closed the socket while streaming audio
                else:
                    print(f"\nTTS Receive runtime error: {e}")
            except Exception as e:
                print(f"\nTTS Receive error: {e}")

        tts_task = asyncio.create_task(receive_audio())

        # Send text + flush
        await tts_socket.send(json.dumps({"text": text, "try_trigger_generation": True}))
        await tts_socket.send(json.dumps({"text": ""}))

        try:
            await asyncio.wait_for(tts_task, timeout=10.0)
        except asyncio.TimeoutError:
            pass

        await tts_socket.close()
    except Exception as e:
        print(f"Failed to connect to ElevenLabs: {e}")


# ── Main WebSocket Endpoint ──────────────────────────────────────────────────

@router.websocket("/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket client connected to /api/v1/ws/audio")

    if not settings.deepgram_api_key:
        print("ERROR: deepgram_api_key not found in settings!")
        await websocket.close()
        return
    if not settings.groq_api_key:
        print("ERROR: groq_api_key not found in settings!")
        await websocket.close()
        return
    print("Deepgram and Groq keys found")

    extra_headers = {"Authorization": f"Token {settings.deepgram_api_key}"}

    # ── Session state ─────────────────────────────────────────────────────
    current_state = "GREETING"
    verified_patient_id = None
    messages = [{"role": "system", "content": get_system_prompt(current_state, verified_patient_id)}]
    accumulated_transcript = ""
    current_llm_task = None

    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(
            DEEPGRAM_URL, additional_headers=extra_headers, ssl=ssl_context
        ) as dg_socket:
            print("Connected to Deepgram STT!")

            # ── Receiver: frontend audio/commands → Deepgram ──────────
            async def receiver():
                nonlocal current_llm_task
                try:
                    while True:
                        data = await websocket.receive()
                        if "bytes" in data:
                            await dg_socket.send(data["bytes"])
                        elif "text" in data:
                            try:
                                msg = json.loads(data["text"])
                                if msg.get("type") == "interrupt":
                                    print("\n--- Frontend sent interrupt. Cancelling processing. ---")
                                    if current_llm_task and not current_llm_task.done():
                                        current_llm_task.cancel()
                                    await websocket.send_text(json.dumps({"type": "interrupt_ack"}))
                            except json.JSONDecodeError:
                                pass
                except (WebSocketDisconnect, RuntimeError):
                    print("Frontend client disconnected.")
                except Exception as e:
                    if "disconnect" in str(e).lower() or "receive" in str(e).lower():
                        pass  # Benign: hot-reload or page refresh killed the socket
                    else:
                        print(f"Receiver error: {e}")

            # ── Sender: Deepgram transcripts → LLM → TTS → frontend ──
            async def sender():
                nonlocal accumulated_transcript, current_state, verified_patient_id, current_llm_task

                try:
                    while True:
                        msg = await dg_socket.recv()
                        res = json.loads(msg)
                        msg_type = res.get("type")

                        if msg_type == "UtteranceEnd":
                            print("\n>>> [UtteranceEnd] User stopped speaking.")
                            text_to_process = accumulated_transcript.strip()
                            if text_to_process:
                                accumulated_transcript = ""
                                print(f"--- Triggering Brain (Groq) with: '{text_to_process}' ---")
                                messages.append({"role": "user", "content": text_to_process})

                                async def process_llm():
                                    nonlocal current_state, verified_patient_id
                                    response_sent = False
                                    full_response = ""
                                    try:
                                        full_response, current_state, verified_patient_id = (
                                            await run_orchestration(
                                                messages, current_state,
                                                verified_patient_id, text_to_process,
                                            )
                                        )

                                        if not full_response.strip():
                                            return

                                        print(f"\nBot: {full_response}\n")

                                        # Send text to frontend first
                                        await websocket.send_text(json.dumps({
                                            "type": "bot_response",
                                            "text": full_response,
                                        }))
                                        response_sent = True

                                        # Phase 2: TTS
                                        await stream_tts(full_response, websocket)

                                    except asyncio.CancelledError:
                                        print("\n[LLM Task Cancelled by User Interrupt]")
                                        if not response_sent and full_response.strip():
                                            messages.append({"role": "assistant", "content": full_response})
                                            asyncio.create_task(websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": full_response,
                                            })))
                                        raise
                                    except Exception as e:
                                        print(f"[LLM Error]: {e}")
                                        error_str = str(e).lower()
                                        if "429" in error_str or "rate limit" in error_str:
                                            err_msg = "I'm currently receiving too many requests. Please try again in a moment."
                                        else:
                                            err_msg = "I'm sorry, I'm having trouble processing that right now."
                                        try:
                                            await websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": err_msg,
                                            }))
                                        except Exception:
                                            pass

                                if current_llm_task and not current_llm_task.done():
                                    current_llm_task.cancel()
                                current_llm_task = asyncio.create_task(process_llm())
                            continue

                        if msg_type == "Results":
                            transcript = (
                                res.get("channel", {})
                                .get("alternatives", [{}])[0]
                                .get("transcript", "")
                            )
                            if not transcript:
                                continue

                            is_final = res.get("is_final", False)
                            if is_final:
                                print(f">>> [FINAL]: {transcript}")
                                accumulated_transcript += transcript + " "
                                await websocket.send_text(json.dumps({
                                    "type": "transcript", "is_final": True, "text": transcript,
                                }))
                            else:
                                print(f"    [Interim]: {transcript}")
                                await websocket.send_text(json.dumps({
                                    "type": "transcript", "is_final": False, "text": transcript,
                                }))

                except Exception as e:
                    print(f"Sender error: {e}")

            # ── Keep-alive ping for Deepgram ──────────────────────────
            async def keep_alive():
                try:
                    while True:
                        await asyncio.sleep(8)
                        await dg_socket.send(json.dumps({"type": "KeepAlive"}))
                except BaseException:
                    pass

            await asyncio.gather(receiver(), sender(), keep_alive())

    except WebSocketDisconnect:
        print("WebSocket client disconnected normally")
    except Exception as e:
        print(f"Failed to connect or maintain Deepgram via WebSockets: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        print("WebSocket endpoint cleaned up")
