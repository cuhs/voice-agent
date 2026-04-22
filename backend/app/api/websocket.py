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
    verified_patient_id = None  # Set after successful lookup_patient

    # SAFETY CLASSIFIER KEYWORDS
    EMERGENCY_KEYWORDS = ["suicide", "chest pain", "heart attack", "can't breathe",
                          "emergency", "911", "bleeding out", "stroke", "overdose",
                          "killing myself", "want to die"]
    CLINICAL_KEYWORDS = ["dosage", "should i take", "side effects", "diagnosis",
                         "does this mean i have", "am i sick", "what is this pill",
                         "is this cancer", "prescribe me"]

    def get_system_prompt():
        base = (
            "You are Medi, an AI voice receptionist for Greenfield Medical Group. "
            "CRITICAL: The patient is SPEAKING to you by voice. They cannot type. "
            "NEVER mention formats like 'YYYY-MM-DD' or ask them to type anything. "
            "When they say a date aloud (e.g. 'august fourteen two thousand one'), YOU silently convert it to 2001-08-14 and use it in tool calls. "
            "Rules: "
            "1. Keep responses to 1-2 short sentences. "
            "2. Be warm and natural. "
            "3. Do not invent data. Only reference data returned by tools. "
            "4. Only answer medical administrative requests. "
            "5. NEVER provide clinical advice. "
            f"\nCURRENT CONVERSATION STATE: {current_state}\n"
        )
        if verified_patient_id:
            base += f"VERIFIED PATIENT ID: {verified_patient_id}. Use this ID for all data lookups.\n"
        if current_state == "GREETING":
            return base + "Goal: Greet the patient warmly and ask how you can help today. Do NOT call any data tools yet. Use transition_state to VERIFICATION once they state a need."
        elif current_state == "VERIFICATION":
            return base + "Goal: Verify identity. Ask for their full name and date of birth. When they say it aloud, silently convert it to YYYY-MM-DD and call lookup_patient. NEVER tell the user what format you need. If found, call transition_state to AUTHENTICATED."
        elif current_state == "AUTHENTICATED":
            return base + "Goal: Confirm their identity. Ask what information they need (appointments, prescriptions, lab results, or scheduling). Then call transition_state to SERVICING."
        elif current_state == "SERVICING":
            return base + "Goal: Use the appropriate data tool (get_appointments, get_prescriptions, get_labs) to answer their question. If they want to book, call transition_state to SCHEDULING. If they're done, transition to CLOSING."
        elif current_state == "SCHEDULING":
            return base + "Goal: Use get_available_slots to show openings. Help them pick a slot. Then transition to CLOSING."
        else:  # CLOSING
            return base + "Goal: Ask if they need anything else. If not, say a warm goodbye."

    messages = [{"role": "system", "content": get_system_prompt()}]
    accumulated_transcript = ""

    TOOLS = [
        {"type": "function", "function": {
            "name": "transition_state",
            "description": "Move the conversation to a new phase. Call this to advance the workflow.",
            "parameters": {"type": "object", "properties": {
                "new_state": {"type": "string", "enum": ["GREETING", "VERIFICATION", "AUTHENTICATED", "SERVICING", "SCHEDULING", "CLOSING"]}
            }, "required": ["new_state"]}
        }},
        {"type": "function", "function": {
            "name": "lookup_patient",
            "description": "Verify patient identity. The user speaks their DOB aloud — you must convert spoken dates (e.g. 'march fifth nineteen eighty') to YYYY-MM-DD yourself before calling this tool. Never ask the user for a specific format.",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "Patient full name"},
                "dob": {"type": "string", "description": "DOB you converted to YYYY-MM-DD from their spoken date"}
            }, "required": ["name", "dob"]}
        }},
        {"type": "function", "function": {
            "name": "get_appointments",
            "description": "Retrieve appointments for a verified patient. Only call after identity is verified.",
            "parameters": {"type": "object", "properties": {
                "patient_id": {"type": "string", "description": "The verified patient ID"},
                "time_range": {"type": "string", "enum": ["upcoming", "past_30_days", "all"], "description": "Filter by time range. Defaults to upcoming."}
            }, "required": ["patient_id"]}
        }},
        {"type": "function", "function": {
            "name": "get_prescriptions",
            "description": "Retrieve active prescriptions and refill status for a verified patient.",
            "parameters": {"type": "object", "properties": {
                "patient_id": {"type": "string", "description": "The verified patient ID"}
            }, "required": ["patient_id"]}
        }},
        {"type": "function", "function": {
            "name": "get_labs",
            "description": "Retrieve recent lab results with reference ranges for a verified patient.",
            "parameters": {"type": "object", "properties": {
                "patient_id": {"type": "string", "description": "The verified patient ID"},
                "test_name": {"type": "string", "description": "Optional: filter by specific test name"}
            }, "required": ["patient_id"]}
        }},
        {"type": "function", "function": {
            "name": "get_available_slots",
            "description": "Find available appointment openings for scheduling.",
            "parameters": {"type": "object", "properties": {}}
        }}
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
                except (WebSocketDisconnect, RuntimeError):
                    print("Frontend client disconnected.")
                except Exception as e:
                    if "disconnect" in str(e).lower() or "receive" in str(e).lower():
                        pass  # Benign: hot-reload or page refresh killed the socket
                    else:
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
                                    nonlocal current_state, verified_patient_id
                                    response_sent = False
                                    tts_socket = None
                                    tts_receive_task = None
                                    full_response = ""
                                    last_tool_results = {}  # tool_name -> result string
                                    try:
                                        from app.api.endpoints import (
                                            internal_lookup_patient, MOCK_APPOINTMENTS, 
                                            MOCK_PRESCRIPTIONS, MOCK_LABS, MOCK_AVAILABLE_SLOTS
                                        )

                                        # SAFETY CLASSIFIER (runs before LLM) ----
                                        text_lower = text_to_process.lower()
                                        if any(kw in text_lower for kw in EMERGENCY_KEYWORDS):
                                            full_response = "I'm a virtual assistant and cannot help with medical emergencies. If you are experiencing an emergency, please hang up and call 911 immediately."
                                            messages.append({"role": "assistant", "content": full_response})
                                            # Skip straight to Phase 2 (TTS)
                                        elif any(kw in text_lower for kw in CLINICAL_KEYWORDS):
                                            full_response = "I'm not able to provide medical advice. I can help you schedule an appointment with your provider to discuss this. Would you like me to look up available times?"
                                            messages.append({"role": "assistant", "content": full_response})
                                        else:
                                            # ---- PHASE 1: Non-streaming tool resolution loop ----
                                            messages[0]["content"] = get_system_prompt()
                                            max_tool_rounds = 5
                                            import re
                                            import uuid
                                            def fix_malformed_json(raw: str) -> str:
                                                """Fix unquoted JSON keys/values like {new_state: VERIFICATION}."""
                                                fixed = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', raw)
                                                fixed = re.sub(r':\s*(?!")([A-Za-z_][A-Za-z_0-9]*)\s*(?=[,}])', r': "\1"', fixed)
                                                return fixed

                                            # Patterns to catch hallucinated tool tags (with or without closing tags)
                                            pattern1 = r'<([a-zA-Z_]+)>(\s*\{.*?\}\s*)(?:</[^>]*>)?'
                                            pattern2 = r'<function=([a-zA-Z_]+)>(\s*\{.*?\}\s*)(?:</function[^>]*>)?'

                                            for _ in range(max_tool_rounds):
                                                print(f"\n[Phase 1] Calling LLM (state={current_state}, with tools)...")
                                                extracted_tools = []
                                                content = ""
                                                clean_content = ""

                                                # Dynamic temperature: low for data states, warmer for chat
                                                temp = 0.4 if current_state in ("GREETING", "CLOSING") else 0.1

                                                try:
                                                    completion = await llm_client.chat.completions.create(
                                                        model="llama-3.1-8b-instant",
                                                        messages=messages,
                                                        tools=TOOLS,
                                                        tool_choice="auto",
                                                        temperature=temp
                                                    )
                                                    choice = completion.choices[0]

                                                    if choice.message.tool_calls:
                                                        for tc in choice.message.tool_calls:
                                                            extracted_tools.append({"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments})

                                                    content = choice.message.content or ""
                                                    clean_content = content
                                                except Exception as api_err:
                                                    # Try multiple ways to detect tool_use_failed from Groq
                                                    error_body = getattr(api_err, 'body', None)
                                                    failed_gen = None

                                                    if isinstance(error_body, dict):
                                                        inner = error_body.get('error', error_body)
                                                        if inner.get('code') == 'tool_use_failed':
                                                            failed_gen = inner.get('failed_generation', '')

                                                    if failed_gen is None:
                                                        err_str = str(api_err)
                                                        if 'tool_use_failed' in err_str:
                                                            fg_match = re.search(r"'failed_generation':\s*'(.*?)'(?:\s*})", err_str, re.DOTALL)
                                                            if fg_match:
                                                                failed_gen = fg_match.group(1)
                                                            else:
                                                                failed_gen = ''

                                                    if failed_gen is not None:
                                                        print(f"[Recovering from tool_use_failed]: parsing failed generation")
                                                        content = failed_gen
                                                        clean_content = content
                                                    else:
                                                        raise

                                                for match in re.finditer(pattern1, content, re.DOTALL):
                                                    clean_content = clean_content.replace(match.group(0), "")
                                                    fixed_args = fix_malformed_json(match.group(2))
                                                    extracted_tools.append({"id": f"call_{uuid.uuid4().hex[:8]}", "name": match.group(1), "arguments": fixed_args})
                                                for match in re.finditer(pattern2, content, re.DOTALL):
                                                    clean_content = clean_content.replace(match.group(0), "")
                                                    fixed_args = fix_malformed_json(match.group(2))
                                                    extracted_tools.append({"id": f"call_{uuid.uuid4().hex[:8]}", "name": match.group(1), "arguments": fixed_args})
                                                
                                                if clean_content.strip():
                                                    full_response = clean_content.strip() + " "
                                                
                                                if extracted_tools:
                                                    tcs_for_history = []
                                                    for tc in extracted_tools:
                                                        tcs_for_history.append({"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}})
                                                        
                                                    assist_msg = {"role": "assistant"}
                                                    if clean_content.strip():
                                                        assist_msg["content"] = clean_content.strip()
                                                    assist_msg["tool_calls"] = tcs_for_history
                                                    messages.append(assist_msg)
                                                    
                                                    for tc in extracted_tools:
                                                        tool_name = tc["name"]
                                                        try:
                                                            args = json.loads(tc["arguments"])
                                                        except:
                                                            args = {}
                                                        
                                                        print(f"[TOOL EXECUTED]: {tool_name}({args})")
                                                        res_data = "Unknown tool."
                                                        
                                                        # STATE GUARDS 
                                                        data_tools = ("get_appointments", "get_prescriptions", "get_labs")
                                                        if tool_name in data_tools and not verified_patient_id:
                                                            res_data = "ACCESS DENIED: Patient identity not yet verified. You must call lookup_patient first."
                                                            print(f"[STATE GUARD]: Blocked {tool_name} — patient not verified")
                                                        elif tool_name == "transition_state":
                                                            current_state = args.get("new_state", current_state)
                                                            messages[0]["content"] = get_system_prompt()
                                                            res_data = f"State transitioned to {current_state}."
                                                        elif tool_name == "lookup_patient":
                                                            if not args.get("dob"):
                                                                res_data = "DOB not provided. Ask the patient for their date of birth."
                                                            else:
                                                                p = internal_lookup_patient(args.get("name", ""), args.get("dob", ""))
                                                                if p:
                                                                    verified_patient_id = p["id"]
                                                                    res_data = json.dumps(p)
                                                                    print(f"[SESSION]: verified_patient_id = {verified_patient_id}")
                                                                else:
                                                                    res_data = "Patient Not Found."
                                                        elif tool_name == "get_appointments":
                                                            res_data = json.dumps(MOCK_APPOINTMENTS.get(args.get("patient_id"), ["No appointments found."]))
                                                        elif tool_name == "get_prescriptions":
                                                            res_data = json.dumps(MOCK_PRESCRIPTIONS.get(args.get("patient_id"), ["No prescriptions found."]))
                                                        elif tool_name == "get_labs":
                                                            res_data = json.dumps(MOCK_LABS.get(args.get("patient_id"), ["No labs found."]))
                                                        elif tool_name == "get_available_slots":
                                                            res_data = json.dumps(MOCK_AVAILABLE_SLOTS)
                                                        
                                                        last_tool_results[tool_name] = res_data
                                                        messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tool_name, "content": res_data})
                                                    
                                                    continue
                                                else:
                                                    if clean_content.strip():
                                                        messages.append({"role": "assistant", "content": clean_content.strip()})
                                                    break

                                        if not full_response.strip():
                                            print("[Phase 1] No final text response from LLM.")
                                            return

                                        # RESPONSE VALIDATION LAYER
                                        if last_tool_results:
                                            combined_tool_data = " ".join(last_tool_results.values()).lower()
                                            response_lower = full_response.lower()
                                            hallucination_found = False
                                            
                                            # Check medications
                                            all_meds = set()
                                            for lst in MOCK_PRESCRIPTIONS.values():
                                                for rx in lst:
                                                    all_meds.add(rx["medication"].lower())
                                            for med in all_meds:
                                                if med in response_lower and med not in combined_tool_data:
                                                    print(f"[GUARDRAIL]: Hallucinated medication '{med}' not in tool results")
                                                    hallucination_found = True
                                                    break
                                            
                                            # Check lab test names
                                            if not hallucination_found:
                                                all_tests = set()
                                                for lst in MOCK_LABS.values():
                                                    for lab in lst:
                                                        all_tests.add(lab["test"].lower())
                                                for test in all_tests:
                                                    if test in response_lower and test not in combined_tool_data:
                                                        print(f"[GUARDRAIL]: Hallucinated lab test '{test}' not in tool results")
                                                        hallucination_found = True
                                                        break
                                            
                                            if hallucination_found:
                                                full_response = "I found your records, but let me make sure I give you accurate information. Could you please specify what you'd like to know? I can look up your appointments, prescriptions, or lab results."
                                                if messages and messages[-1].get("role") == "assistant":
                                                    messages[-1]["content"] = full_response
                                                print("[GUARDRAIL]: Response replaced with safe fallback")

                                        print(f"\nBot: {full_response}\n")

                                        # Send text to frontend FIRST (clears ignoreAudio flag before audio arrives)
                                        await websocket.send_text(json.dumps({
                                            "type": "bot_response",
                                            "text": full_response
                                        }))
                                        response_sent = True

                                        # ---- PHASE 2: Stream final response to TTS ----
                                        api_key = getattr(settings, "elevenlabs_api_key", None)
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
                                                    except RuntimeError as e:
                                                        if "Unexpected ASGI message" in str(e) or "websocket.send" in str(e):
                                                            pass # Benign: hot-reload closed the socket while we were sending audio
                                                        else:
                                                            print(f"\nTTS Receive runtime error: {e}")
                                                    except Exception as e:
                                                        print(f"\nTTS Receive error: {e}")

                                                tts_receive_task = asyncio.create_task(receive_audio())
                                                
                                                # Send the full response text to TTS
                                                await tts_socket.send(json.dumps({"text": full_response, "try_trigger_generation": True}))
                                                await tts_socket.send(json.dumps({"text": ""}))
                                                
                                                try:
                                                    await asyncio.wait_for(tts_receive_task, timeout=10.0)
                                                except asyncio.TimeoutError:
                                                    pass
                                                await tts_socket.close()
                                                tts_socket = None
                                            except Exception as e:
                                                print(f"Failed to connect to ElevenLabs: {e}")

                                    except asyncio.CancelledError:
                                        print("\n[LLM Task Cancelled by User Interrupt]")
                                        
                                        if not response_sent and full_response.strip():
                                            messages.append({"role": "assistant", "content": full_response})
                                            asyncio.create_task(websocket.send_text(json.dumps({
                                                "type": "bot_response",
                                                "text": full_response
                                            })))
                                                
                                        try:
                                            if tts_socket:
                                                asyncio.create_task(tts_socket.close())
                                        except Exception:
                                            pass
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
                                                "text": err_msg
                                            }))
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
