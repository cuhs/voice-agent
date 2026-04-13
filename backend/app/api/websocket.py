import asyncio
import json
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.config import settings

router = APIRouter()

DG_PARAMS = "model=nova-2&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&utterance_end_ms=1000&vad_events=true"
DEEPGRAM_URL = f"wss://api.deepgram.com/v1/listen?{DG_PARAMS}"

@router.websocket("/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket client connected to /api/v1/ws/audio")

    if not settings.deepgram_api_key:
        print("ERROR: deepgram_api_key not found in settings!")
        await websocket.close()
        return
    else:
        print("Deepgram key found")

    extra_headers = {
        "Authorization": f"Token {settings.deepgram_api_key}"
    }

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
                try:
                    while True:
                        msg = await dg_socket.recv()
                        res = json.loads(msg)

                        msg_type = res.get("type")

                        if msg_type == "UtteranceEnd":
                            print(">>> [UtteranceEnd] User stopped speaking.")
                            continue

                        if msg_type == "Results":
                            is_final = res.get("is_final")
                            alternatives = res.get("channel", {}).get("alternatives", [])
                            if not alternatives:
                                continue
                                
                            transcript = alternatives[0].get("transcript", "").strip()
                            
                            if transcript:
                                if is_final:
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
