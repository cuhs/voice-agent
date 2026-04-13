from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import api_router_v1
from app.core.config import settings

app = FastAPI(
    title=settings.project_name,
    openapi_url=f"{settings.api_v1_str}/openapi.json",
    docs_url=f"{settings.api_v1_str}/docs",
    redoc_url=f"{settings.api_v1_str}/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router_v1, prefix=settings.api_v1_str)

@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.project_name}"}

@app.websocket("/ws/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket client connected to /ws/audio")
    try:
        while True:
             # Wait for data from the client
            data = await websocket.receive_bytes()
            # Echo the exact same data back
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        print("WebSocket client disconnected")
