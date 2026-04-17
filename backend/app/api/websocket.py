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
    
    # State machine initialization
    current_state = "GREETING"

    def get_system_prompt():
        base = (
            "You are Medi, an AI voice receptionist for Greenfield Medical Group. "
            "1. Keep responses concise (voice responses should be 1-2 sentences). "
            "2. Be warm and reassuring. You are a VOICE agent, speak naturally. "
            "3. Do not invent data. Use tools to look up reality. "
            "4. Only answer medical administrative requests. "
            f"\nCURRENT CONVERSATION STATE: {current_state}\n"
        )
        if current_state == "GREETING":
            return base + "Goal: Greet the patient warmly. If they have a request, use transition_state to move to VERIFICATION."
        elif current_state == "VERIFICATION":
            return base + "Goal: Ask their name and DOB. Once provided, use lookup_patient. If successful, use transition_state to move to AUTHENTICATED."
        elif current_state == "AUTHENTICATED":
            return base + "Goal: Acknowledge you found them. Ask what info they need. Use transition_state to move to SERVICING."
        elif current_state == "SERVICING":
            return base + "Goal: Use tools to fetch their labs/prescriptions/appointments. Use transition_state to move to SCHEDULING if they want to book, or CLOSING if done."
        elif current_state == "SCHEDULING":
            return base + "Goal: Use get_available_slots to find slots. Help them schedule. Use transition_state to move to CLOSING."
        else: # CLOSING
            return base + "Goal: Ask if they need anything else. If not, say goodbye politely."

    messages = [{"role": "system", "content": get_system_prompt()}]
    accumulated_transcript = ""

    TOOLS = [
        {"type": "function", "function": {"name": "transition_state", "description": "Move the conversation to a new state.", "parameters": {"type": "object", "properties": {"new_state": {"type": "string", "enum": ["GREETING", "VERIFICATION", "AUTHENTICATED", "SERVICING", "SCHEDULING", "CLOSING"]}}, "required": ["new_state"]}}},
        {"type": "function", "function": {"name": "lookup_patient", "description": "Look up patient by name and DOB.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "dob": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["name", "dob"]}}},
        {"type": "function", "function": {"name": "get_appointments", "description": "Get appointments for a patient.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}}},
        {"type": "function", "function": {"name": "get_prescriptions", "description": "Get prescriptions for a patient.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}}},
        {"type": "function", "function": {"name": "get_labs", "description": "Get lab results for a patient.", "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}}},
        {"type": "function", "function": {"name": "get_available_slots", "description": "Find available openings to book an appointment.", "parameters": {"type": "object", "properties": {}}}}
    ]
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
                                messages[0]["content"] = get_system_prompt()
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
                                            stream=True,
                                            tools=TOOLS,
                                            tool_choice="auto"
                                        )
                                        full_response = ""
                                        is_action = False
                                        response_sent = False
                                        
                                        tool_calls_buffer = {}

                                        async for chunk in stream:
                                            delta = chunk.choices[0].delta
                                            if delta.tool_calls:
                                                is_action = True
                                                for tc in delta.tool_calls:
                                                    if tc.id:
                                                        tool_calls_buffer[tc.index] = {"id": tc.id, "function": {"name": tc.function.name, "arguments": ""}}
                                                    if tc.function.arguments:
                                                        tool_calls_buffer[tc.index]["function"]["arguments"] += tc.function.arguments
                                            
                                            content = delta.content
                                            if content:
                                                print(content, end="", flush=True)
                                                full_response += content
                                                if tts_socket:
                                                    await tts_socket.send(json.dumps({"text": content, "try_trigger_generation": True}))

                                        print("\n")
                                        if full_response.strip():
                                            messages.append({"role": "assistant", "content": full_response})
                                        
                                        if not is_action and full_response.strip():
                                            await websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": full_response
                                            }))
                                            response_sent = True
                                        
                                        if tts_socket:
                                            if not is_action:
                                                await tts_socket.send(json.dumps({"text": ""}))
                                                try:
                                                    await asyncio.wait_for(tts_receive_task, timeout=5.0)
                                                except asyncio.TimeoutError:
                                                    pass
                                            await tts_socket.close()

                                        # ---- NATIVE TOOL EXECUTIONS ----
                                        if is_action:
                                            from app.api.endpoints import (
                                                internal_lookup_patient, MOCK_APPOINTMENTS, 
                                                MOCK_PRESCRIPTIONS, MOCK_LABS, MOCK_AVAILABLE_SLOTS
                                            )
                                            # Append tool calls to message history
                                            tcs = []
                                            for idx, tc in tool_calls_buffer.items():
                                                tcs.append({"id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}})
                                            
                                            messages.append({"role": "assistant", "tool_calls": tcs})
                                            
                                            for idx, tc in tool_calls_buffer.items():
                                                tool_name = tc["function"]["name"]
                                                try:
                                                    args = json.loads(tc["function"]["arguments"])
                                                except:
                                                    args = {}
                                                
                                                print(f"[TOOL EXECUTED]: {tool_name} with {args}")
                                                res_data = "Unknown Tool"
                                                
                                                if tool_name == "transition_state":
                                                    nonlocal current_state
                                                    current_state = args.get("new_state", current_state)
                                                    res_data = f"State transitioned to {current_state}. Now follow {current_state} instructions."
                                                elif tool_name == "lookup_patient":
                                                    p = internal_lookup_patient(args.get("name", ""), args.get("dob", ""))
                                                    res_data = json.dumps(p) if p else "Patient Not Found."
                                                elif tool_name == "get_appointments":
                                                    res_data = json.dumps(MOCK_APPOINTMENTS.get(args.get("patient_id"), ["No appointments found."]))
                                                elif tool_name == "get_prescriptions":
                                                    res_data = json.dumps(MOCK_PRESCRIPTIONS.get(args.get("patient_id"), ["No prescriptions found."]))
                                                elif tool_name == "get_labs":
                                                    res_data = json.dumps(MOCK_LABS.get(args.get("patient_id"), ["No labs found."]))
                                                elif tool_name == "get_available_slots":
                                                    res_data = json.dumps(MOCK_AVAILABLE_SLOTS)
                                                    
                                                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tool_name, "content": res_data})
                                                
                                            await process_llm() # Automatically follow-up!

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
