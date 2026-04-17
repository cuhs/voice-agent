import asyncio
import json
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI
from app.core.config import settings

router = APIRouter()

DG_PARAMS = "model=nova-2&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&utterance_end_ms=1000&vad_events=true"
DEEPGRAM_URL = f"wss://api.deepgram.com/v1/listen?{DG_PARAMS}"

# Initialize Groq client using OpenAI SDK
llm_client = AsyncOpenAI(
    api_key=settings.groq_api_key,
    base_url="https://api.groq.com/openai/v1"
)

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

    extra_headers = {
        "Authorization": f"Token {settings.deepgram_api_key}"
    }
    
    # State mechanism for conversation memory and transcript accumulation
    system_prompt = (
        "You are Medi, a voice assistant for Greenfield Medical Group. "
        "You help patients check upcoming appointments, request prescription refills, "
        "retrieve lab results, and answer general questions about the practice. "
        "Rules: "
        "1. Keep responses concise (voice responses should be 1-3 sentences). "
        "2. Be warm and reassuring. "
        "3. You are a VOICE agent. So don't say stuff as if you are a text chat bot"
        "4. Do not use markdown formatting. "
        "5. Always verify the patient's identity before sharing medical details (ask for name and date of birth format YYYY-MM-DD). "
        "6. Never provide medical diagnoses or treatment advice — always direct clinical questions to a provider.\n"
        "7. You are a patient coordination assistant, not a medical professional. "
        "8. If the user asks something completely unrelated to medical assistance, ignore it.\n"
        "\nAVAILABLE INTERNAL TOOLS (NEVER ask the user to say these. YOU must generate these exact strings yourself to fetch data):\n"
        "- To look up a patient ID, respond EXACTLY with: `LOOKUP_PATIENT: Name, YYYY-MM-DD`\n"
        "- To get appointments, respond EXACTLY with: `GET_APPOINTMENTS: patient_id`\n"
        "- To get prescriptions, respond EXACTLY with: `GET_PRESCRIPTIONS: patient_id`\n"
        "- To get lab results, respond EXACTLY with: `GET_LABS: patient_id`\n"
        "- To find availability, respond EXACTLY with: `GET_AVAILABLE_SLOTS: {}`\n"
        "When using a tool, output ONLY the exact tool string and nothing else. Wait for the SYSTEM RESULT, then speak naturally to the user."
    )
    messages = [{"role": "system", "content": system_prompt}]
    accumulated_transcript = ""
    current_llm_task = None

    try:
        import ssl
        import certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(DEEPGRAM_URL, additional_headers=extra_headers, ssl=ssl_context) as dg_socket:
            print("Connected to Deepgram STT!")

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
                except WebSocketDisconnect:
                    print("Frontend client disconnected.")
                except Exception as e:
                    print(f"Receiver error: {e}")

            async def sender():
                nonlocal accumulated_transcript
                try:
                    while True:
                        msg = await dg_socket.recv()
                        res = json.loads(msg)

                        msg_type = res.get("type")

                        if msg_type == "UtteranceEnd":
                            print("\n>>> [UtteranceEnd] User stopped speaking.")
                            text_to_process = accumulated_transcript.strip()
                            if text_to_process:
                                # Reset buffer
                                accumulated_transcript = ""
                                print(f"--- Triggering Brain (Groq) with: '{text_to_process}' ---")
                                messages.append({"role": "user", "content": text_to_process})
                                
                                # Spawn async LLM process so we don't block STT parsing
                                async def process_llm():
                                    try:
                                        # --- ElevenLabs TTS Setup ---
                                        tts_socket = None
                                        tts_receive_task = None
                                        api_key = getattr(settings, "elevenlabs_api_key", None)
                                        print(f"ElevenLabs Key Present: {bool(api_key)}")
                                        
                                        if api_key:
                                            try:
                                                voice_id = "pNInz6obpgDQGcFmaJgB" 
                                                elevenlabs_url = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_flash_v2_5&output_format=pcm_16000"
                                                
                                                import ssl
                                                import certifi
                                                ssl_context = ssl.create_default_context(cafile=certifi.where())
                                                
                                                tts_socket = await websockets.connect(elevenlabs_url, ssl=ssl_context)
                                                print("Connected to ElevenLabs TTS!")
                                                
                                                init_msg = {
                                                    "text": " ",
                                                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                                                    "xi_api_key": api_key,
                                                    "chunk_length_schedule": [50]
                                                }
                                                await tts_socket.send(json.dumps(init_msg))

                                                async def receive_audio():
                                                    try:
                                                        while True:
                                                            msg = await tts_socket.recv()
                                                            data = json.loads(msg)
                                                            keys = list(data.keys())
                                                            
                                                            if "error" in data:
                                                                print(f"ElevenLabs API Error: {data['error']}")
                                                            if data.get("audio"):
                                                                print(".", end="", flush=True)
                                                                import base64
                                                                audio_bytes = base64.b64decode(data["audio"])
                                                                await websocket.send_bytes(audio_bytes)
                                                            if data.get("isFinal"):
                                                                print("\nElevenLabs reports isFinal: True")
                                                                break
                                                    except websockets.exceptions.ConnectionClosed as e:
                                                        print(f"\nElevenLabs websocket closed. Code: {e.code}, Reason: {e.reason}")
                                                    except Exception as e:
                                                        print(f"\nTTS Receive error: {e}")

                                                tts_receive_task = asyncio.create_task(receive_audio())
                                            except Exception as e:
                                                print(f"Failed to connect to ElevenLabs: {e}")
                                            
                                        # --- LLM Stream ---
                                        print("\nBot: ", end="")
                                        stream = await llm_client.chat.completions.create(
                                            model="llama-3.1-8b-instant",
                                            messages=messages,
                                            stream=True
                                        )
                                        full_response = ""
                                        is_action_determined = False
                                        is_action = False
                                        response_sent = False
                                        
                                        async for chunk in stream:
                                            content = chunk.choices[0].delta.content if chunk.choices else None
                                            if content:
                                                print(content, end="", flush=True)
                                                full_response += content
                                                
                                                if tts_socket:
                                                    if not is_action_determined:
                                                        action_keywords = ["LOOKUP_PATIENT:", "GET_APPOINTMENTS:", "GET_PRESCRIPTIONS:", "GET_LABS:", "GET_AVAILABLE_SLOTS:"]
                                                        match_possible = False
                                                        for kw in action_keywords:
                                                            if kw.startswith(full_response) or full_response.startswith(kw):
                                                                match_possible = True
                                                                break
                                                        
                                                        if not match_possible:
                                                            is_action_determined = True
                                                            is_action = False
                                                            await tts_socket.send(json.dumps({"text": full_response, "try_trigger_generation": True}))
                                                    else:
                                                        if not is_action:
                                                            await tts_socket.send(json.dumps({"text": content, "try_trigger_generation": True}))

                                        print("\n")
                                        messages.append({"role": "assistant", "content": full_response})
                                        
                                        action_keywords = ["LOOKUP_PATIENT:", "GET_APPOINTMENTS:", "GET_PRESCRIPTIONS:", "GET_LABS:", "GET_AVAILABLE_SLOTS:"]
                                        if any(full_response.startswith(kw) for kw in action_keywords):
                                            is_action = True
                                            
                                        if not is_action:
                                            await websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": full_response
                                            }))
                                            response_sent = True
                                        
                                        if tts_socket:
                                            # Close the stream
                                            if not is_action:
                                                await tts_socket.send(json.dumps({"text": ""}))
                                                try:
                                                    await asyncio.wait_for(tts_receive_task, timeout=5.0)
                                                except asyncio.TimeoutError:
                                                    pass
                                            await tts_socket.close()

                                        # ---- ACTION INTERCEPTION (Pseudo Tool Calling) ----
                                        from app.api.endpoints import (
                                            internal_lookup_patient, MOCK_APPOINTMENTS, 
                                            MOCK_PRESCRIPTIONS, MOCK_LABS, MOCK_AVAILABLE_SLOTS
                                        )
                                        
                                        resp_text = full_response.strip()
                                        
                                        if resp_text.startswith("LOOKUP_PATIENT:"):
                                            try:
                                                parts = resp_text.split("LOOKUP_PATIENT:")[1].strip().split(",")
                                                if len(parts) >= 2:
                                                    p = internal_lookup_patient(parts[0].strip(), parts[1].strip())
                                                    res_text = f"[INTERNAL DATABASE RESPONSE (Do not read this text verbatim. Speak this naturally to the user)]: {json.dumps(p) if p else 'Patient Not Found.'}"
                                                    print(f"[ACTION FIRED]: {res_text}")
                                                    messages.append({"role": "user", "content": res_text})
                                                    await process_llm() # Automatically follow-up!
                                            except Exception as e:
                                                pass
                                                
                                        elif resp_text.startswith("GET_APPOINTMENTS:"):
                                            pid = resp_text.split("GET_APPOINTMENTS:")[1].strip()
                                            res_text = f"[INTERNAL DATABASE RESPONSE (Do not read this text verbatim. Speak this naturally to the user)]: {json.dumps(MOCK_APPOINTMENTS.get(pid, [str('No appointments found.')]))}"
                                            print(f"[ACTION FIRED]: {res_text}")
                                            messages.append({"role": "user", "content": res_text})
                                            await process_llm()
                                            
                                        elif resp_text.startswith("GET_PRESCRIPTIONS:"):
                                            pid = resp_text.split("GET_PRESCRIPTIONS:")[1].strip()
                                            res_text = f"[INTERNAL DATABASE RESPONSE (Do not read this text verbatim. Speak this naturally to the user)]: {json.dumps(MOCK_PRESCRIPTIONS.get(pid, [str('No prescriptions found.')]))}"
                                            print(f"[ACTION FIRED]: {res_text}")
                                            messages.append({"role": "user", "content": res_text})
                                            await process_llm()
                                            
                                        elif resp_text.startswith("GET_LABS:"):
                                            pid = resp_text.split("GET_LABS:")[1].strip()
                                            res_text = f"[INTERNAL DATABASE RESPONSE (Do not read this text verbatim. Speak this naturally to the user)]: {json.dumps(MOCK_LABS.get(pid, [str('No labs found.')]))}"
                                            print(f"[ACTION FIRED]: {res_text}")
                                            messages.append({"role": "user", "content": res_text})
                                            await process_llm()
                                            
                                        elif resp_text.startswith("GET_AVAILABLE_SLOTS:"):
                                            res_text = f"[INTERNAL DATABASE RESPONSE (Do not read this text verbatim. Speak this naturally to the user)]: {json.dumps(MOCK_AVAILABLE_SLOTS)}"
                                            print(f"[ACTION FIRED]: {res_text}")
                                            messages.append({"role": "user", "content": res_text})
                                            await process_llm()

                                    except asyncio.CancelledError:
                                        print("\n[LLM Task Cancelled by User Interrupt]")
                                        
                                        # Send whatever text the bot generated (only if not already sent)
                                        if not response_sent and 'full_response' in locals() and full_response.strip():
                                            messages.append({"role": "assistant", "content": full_response})
                                            asyncio.create_task(websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": full_response
                                            })))
                                                
                                        try:
                                            if 'tts_socket' in locals() and tts_socket:
                                                asyncio.create_task(tts_socket.close())
                                        except Exception:
                                            pass
                                        raise
                                    except Exception as e:
                                        print(f"[LLM Error]: {e}")
                                        error_str = str(e).lower()
                                        if "429" in error_str or "rate limit" in error_str:
                                            err_msg = "I'm currently receiving too many requests. Please try again in an hour."
                                        else:
                                            err_msg = "I'm sorry, I'm having trouble processing that right now."
                                            
                                        try:
                                            await websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": err_msg
                                            }))
                                            if 'tts_socket' in locals() and tts_socket:
                                                await tts_socket.send(json.dumps({"text": err_msg, "try_trigger_generation": True}))
                                                await tts_socket.send(json.dumps({"text": ""}))
                                                await tts_socket.close()
                                        except Exception:
                                            pass
                                        
                                nonlocal current_llm_task
                                if current_llm_task and not current_llm_task.done():
                                    current_llm_task.cancel()
                                current_llm_task = asyncio.create_task(process_llm())
                            continue

                        if msg_type == "Results":
                            is_final = res.get("is_final")
                            alternatives = res.get("channel", {}).get("alternatives", [])
                            if not alternatives:
                                continue
                                
                            is_final = res.get("is_final", False)
                            transcript = res.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                            if transcript:
                                if is_final:
                                    print(f">>> [FINAL]: {transcript}")
                                    accumulated_transcript += transcript + " "
                                    await websocket.send_text(json.dumps({"type": "transcript", "is_final": True, "text": transcript}))
                                else:
                                    print(f"    [Interim]: {transcript}")
                                    await websocket.send_text(json.dumps({"type": "transcript", "is_final": False, "text": transcript}))
                                    
                except Exception as e:
                    print(f"Sender error: {e}")

            async def keep_alive():
                try:
                    while True:
                        await asyncio.sleep(8)
                        await dg_socket.send(json.dumps({"type": "KeepAlive"}))
                except BaseException:
                    pass

            await asyncio.gather(
                receiver(),
                sender(),
                keep_alive()
            )

    except WebSocketDisconnect:
        print("WebSocket client disconnected normally")
    except Exception as e:
        print(f"Failed to connect or maintain Deepgram via WebSockets: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass
        print("WebSocket endpoint cleaned up")
