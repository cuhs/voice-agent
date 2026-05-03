"""
WebSocket handler for the Medi voice assistant.

This module is the transport layer only — it wires together:
  - Frontend WebSocket ↔ Deepgram STT (Speech-to-Text)
  - Deepgram transcript events → LLM orchestration (Phase 1, handles logic/tool calling)
  - LLM response → ElevenLabs TTS (Text-to-Speech) → Frontend audio (Phase 2)

All business logic (what the agent says, tools it uses, states) lives in 
orchestrator.py, tools.py, guardrails.py, and prompts.py. This file is purely 
concerned with moving data streams between the frontend and the AI services.
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

# ── Deepgram Configuration ───────────────────────────────────────────────────
# We use Deepgram's nova-2 model for fast and accurate streaming transcription.
# We enable vad_events (Voice Activity Detection) and interim_results so we 
# get partial words as the user speaks.
DG_PARAMS = (
    "model=nova-2&encoding=linear16&sample_rate=16000&channels=1"
    "&interim_results=true&utterance_end_ms=1000&vad_events=true"
)
DEEPGRAM_URL = f"wss://api.deepgram.com/v1/listen?{DG_PARAMS}"


# ── TTS Streaming (Phase 2) ──────────────────────────────────────────────────

async def stream_tts(text: str, websocket: WebSocket) -> None:
    """
    Stream text through ElevenLabs TTS and send audio chunks to the frontend.
    
    This function opens a WebSocket to ElevenLabs, sends the text payload, 
    and then listens for audio chunks returning. As soon as a chunk arrives, 
    it is forwarded as binary data directly to the frontend's WebSocket.
    """
    api_key = getattr(settings, "elevenlabs_api_key", None)
    if not api_key:
        return

    # A predefined voice ID from ElevenLabs.
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

        # Initialize the handshake. The chunk_length_schedule controls 
        # how frequently ElevenLabs sends us audio chunks (lower = faster time to first byte).
        await tts_socket.send(json.dumps({
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
            "xi_api_key": api_key,
            "chunk_length_schedule": [50],
        }))

        async def receive_audio():
            """Background task to continuously read TTS audio from ElevenLabs."""
            try:
                while True:
                    msg = await tts_socket.recv()
                    data = json.loads(msg)

                    if "error" in data:
                        print(f"ElevenLabs API Error: {data['error']}")
                    if data.get("audio"):
                        print(".", end="", flush=True)
                        # Decode the base64 audio and send as raw binary bytes over WebSocket
                        audio_bytes = base64.b64decode(data["audio"])
                        await websocket.send_bytes(audio_bytes)
                    if data.get("isFinal"):
                        # ElevenLabs signals when generation for this request is fully complete
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

        # Send text + flush. The empty string tells ElevenLabs we are done sending text.
        await tts_socket.send(json.dumps({"text": text, "try_trigger_generation": True}))
        await tts_socket.send(json.dumps({"text": ""}))

        try:
            # Await the completion of the receive task (or timeout if it gets stuck)
            await asyncio.wait_for(tts_task, timeout=10.0)
        except asyncio.TimeoutError:
            pass

        await tts_socket.close()
    except Exception as e:
        print(f"Failed to connect to ElevenLabs: {e}")


# ── Main WebSocket Endpoint ──────────────────────────────────────────────────

@router.websocket("/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    """
    Main entry point for a frontend connection. Each client session 
    creates one instance of this endpoint execution.
    """
    await websocket.accept()
    print("WebSocket client connected to /api/v1/ws/audio")

    # Validate that our third-party keys are present.
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
    # The session state tracks the state machine stage, verified patient info,
    # and the message history containing the LLM prompts and conversation log.
    current_state = "GREETING"
    verified_patient_id = None
    messages = [{"role": "system", "content": get_system_prompt(current_state, verified_patient_id)}]
    accumulated_transcript = ""
    current_llm_task = None
    
    # Event the receiver sets to signal the orchestrator to abort early
    # if the user interrupts mid-generation.
    cancel_event = asyncio.Event()

    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        # Open connection to Deepgram STT.
        async with websockets.connect(
            DEEPGRAM_URL, additional_headers=extra_headers, ssl=ssl_context
        ) as dg_socket:
            print("Connected to Deepgram STT!")

            # ── Receiver: frontend audio/commands → Deepgram ──────────
            async def receiver():
                """
                Listens to the frontend.
                If binary bytes are received (audio), forwards them to Deepgram.
                If text is received (JSON), parses it for commands like 'interrupt'.
                """
                nonlocal current_llm_task
                try:
                    while True:
                        data = await websocket.receive()
                        if "bytes" in data:
                            # Forward raw audio bytes to Deepgram for STT
                            await dg_socket.send(data["bytes"])
                        elif "text" in data:
                            try:
                                msg = json.loads(data["text"])
                                if msg.get("type") == "interrupt":
                                    print("\n--- Frontend sent interrupt. Cancelling processing. ---")
                                    # Signal the orchestrator to stop between steps
                                    cancel_event.set()
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
                """
                Listens to Deepgram.
                When words are recognized, it emits 'transcript' messages back to the frontend.
                When the user stops speaking ('UtteranceEnd'), it triggers the LLM Orchestrator.
                """
                nonlocal accumulated_transcript, current_state, verified_patient_id, current_llm_task

                try:
                    while True:
                        msg = await dg_socket.recv()
                        res = json.loads(msg)
                        msg_type = res.get("type")

                        if msg_type == "UtteranceEnd":
                            print("\n>>> [UtteranceEnd] User stopped speaking.")
                            text_to_process = accumulated_transcript.strip()
                            
                            # Only trigger LLM if the user actually said something
                            if text_to_process:
                                accumulated_transcript = ""
                                print(f"--- Triggering Brain (Groq) with: '{text_to_process}' ---")
                                await websocket.send_text(json.dumps({"type": "pipeline_stage", "stage": "stt", "detail": f"Transcribed: '{text_to_process}'"}))
                                await websocket.send_text(json.dumps({"type": "dev_log", "content": f"[STT] UtteranceEnd received. Transcribed: '{text_to_process}'"}))
                                messages.append({"role": "user", "content": text_to_process})

                                async def process_llm():
                                    """
                                    Inner task that runs the LLM logic. It can be cancelled
                                    by the receiver if the user interrupts.
                                    """
                                    nonlocal current_state, verified_patient_id
                                    response_sent = False
                                    full_response = ""

                                    # Reset cancel event for this new processing run
                                    cancel_event.clear()

                                    # Track the background filler TTS task so we can
                                    # wait for it to finish before starting the real
                                    # response TTS (avoids overlapping audio streams).
                                    filler_tts_task: asyncio.Task | None = None

                                    # ── Filler callback: fire-and-forget TTS ──────
                                    async def send_filler(phrase: str) -> None:
                                        """
                                        Kick off filler TTS in the background and
                                        return immediately so tool execution runs in
                                        parallel with the filler audio playback.
                                        """
                                        nonlocal filler_tts_task
                                        try:
                                            # Send text to frontend chat panel right away
                                            await websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": phrase,
                                                "is_filler": True,
                                            }))
                                            # Start TTS in background — do NOT await
                                            filler_tts_task = asyncio.create_task(
                                                stream_tts(phrase, websocket)
                                            )
                                        except Exception as e:
                                            print(f"[Filler TTS Error]: {e}")

                                    async def send_dev_log(content: str):
                                        await websocket.send_text(json.dumps({"type": "dev_log", "content": content}))
                                        
                                    async def send_state_update(state: str):
                                        await websocket.send_text(json.dumps({"type": "state_update", "state": state}))

                                    async def send_pipeline_stage(stage: str, detail: str = ""):
                                        await websocket.send_text(json.dumps({"type": "pipeline_stage", "stage": stage, "detail": detail}))

                                    try:
                                        await send_pipeline_stage("orchestration", "Running LLM orchestration loop")
                                        # Run the state machine and orchestrator logic
                                        full_response, current_state, verified_patient_id = (
                                            await run_orchestration(
                                                messages, current_state,
                                                verified_patient_id, text_to_process,
                                                filler_callback=send_filler,
                                                cancel_event=cancel_event,
                                                dev_log_callback=send_dev_log,
                                                state_update_callback=send_state_update,
                                                pipeline_callback=send_pipeline_stage,
                                            )
                                        )

                                        if not full_response.strip():
                                            return

                                        print(f"\nBot: {full_response}\n")

                                        # Wait for filler TTS to finish before
                                        # streaming the real response — prevents
                                        # two audio streams overlapping.
                                        if filler_tts_task and not filler_tts_task.done():
                                            print("[Waiting for filler TTS to finish...]")
                                            try:
                                                await asyncio.wait_for(filler_tts_task, timeout=5.0)
                                            except asyncio.TimeoutError:
                                                print("[Filler TTS timed out, proceeding]")

                                        # Send text to frontend first so it appears in chat UI
                                        await websocket.send_text(json.dumps({
                                            "type": "bot_response",
                                            "text": full_response,
                                        }))
                                        response_sent = True

                                        # Phase 2: Run Text-to-Speech to generate bot audio
                                        await send_pipeline_stage("tts", f"Streaming TTS: '{full_response[:40]}...'")
                                        await send_dev_log(f"[TTS] Generating audio via ElevenLabs for text: '{full_response[:50]}...'")
                                        await stream_tts(full_response, websocket)
                                        await send_pipeline_stage("playback", "Audio sent to frontend for playback")

                                    except asyncio.CancelledError:
                                        print("\n[LLM Task Cancelled by User Interrupt]")
                                        # Cancel any in-flight filler TTS too
                                        if filler_tts_task and not filler_tts_task.done():
                                            filler_tts_task.cancel()
                                        
                                        # Ensure we save any partial text generation to the history
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
                                        # Provide a graceful fallback on rate limits or failures
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

                                # Cancel old LLM tasks if a new utterance finishes before the old one is done
                                if current_llm_task and not current_llm_task.done():
                                    current_llm_task.cancel()
                                current_llm_task = asyncio.create_task(process_llm())
                            continue

                        # Deepgram sends partial/interim word transcripts as the user speaks
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
                                await websocket.send_text(json.dumps({"type": "pipeline_stage", "stage": "capture", "detail": f"Streaming audio to Deepgram..."}))
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
            # Required so Deepgram doesn't close the connection when the user
            # is silent for a long period of time.
            async def keep_alive():
                try:
                    while True:
                        await asyncio.sleep(8)
                        await dg_socket.send(json.dumps({"type": "KeepAlive"}))
                except BaseException:
                    pass

            # Run receiver, sender, and keep_alive concurrently
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
