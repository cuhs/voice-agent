from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

@router.websocket("/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket client connected to /api/v1/ws/audio")
    try:
        while True:
            # Wait for data from the client
            data = await websocket.receive_bytes()
            # Echo the exact same data back
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        print("WebSocket client disconnected from /api/v1/ws/audio")
