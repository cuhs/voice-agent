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
        "3. Do not use markdown formatting. "
        "4. Always verify the patient's identity before sharing medical details (ask for name and date of birth format YYYY-MM-DD). "
        "5. Never provide medical diagnoses or treatment advice — always direct clinical questions to a provider.\n"
        "6. You are a patient coordination assistant, not a medical professional. "
        "7. If the user asks something completely unrelated to medical assistance, ignore it.\n"
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

    try:
        async with websockets.connect(DEEPGRAM_URL, additional_headers=extra_headers) as dg_socket:
            print("Connected to Deepgram STT!")

            async def receiver():
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await dg_socket.send(data)
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
                                        print("\nBot: ", end="")
                                        stream = await llm_client.chat.completions.create(
                                            model="llama-3.1-8b-instant",
                                            messages=messages,
                                            stream=True
                                        )
                                        full_response = ""
                                        async for chunk in stream:
                                            # Validate the content packet safely
                                            content = chunk.choices[0].delta.content if chunk.choices else None
                                            if content:
                                                print(content, end="", flush=True)
                                                full_response += content
                                        print("\n")
                                        messages.append({"role": "assistant", "content": full_response})
                                        
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

                                    except Exception as e:
                                        print(f"[LLM Error]: {e}")
                                        
                                asyncio.create_task(process_llm())
                            continue

                        if msg_type == "Results":
                            is_final = res.get("is_final")
                            alternatives = res.get("channel", {}).get("alternatives", [])
                            if not alternatives:
                                continue
                                
                            transcript = alternatives[0].get("transcript", "").strip()
                            
                            if transcript:
                                if is_final:
                                    # Accumulate into our buffer
                                    accumulated_transcript += transcript + " "
                                    print(f">>> [FINAL]: {transcript}")
                                else:
                                    print(f"    [Interim]: {transcript}")
                                
                                await websocket.send_text(json.dumps({
                                    "type": "transcript",
                                    "text": transcript,
                                    "is_final": is_final
                                }))

                except websockets.exceptions.ConnectionClosed:
                    print("Deepgram connection closed.")
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

    except Exception as e:
        print(f"Failed to connect or maintain Deepgram via WebSockets: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass
        print("WebSocket endpoint cleaned up")
