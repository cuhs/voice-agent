from fastapi import APIRouter
from app.api.endpoints import router as system_router
from app.api.websocket import router as websocket_router

api_router_v1 = APIRouter()
api_router_v1.include_router(system_router, prefix="/system", tags=["System"])
api_router_v1.include_router(websocket_router, prefix="/ws", tags=["WebSocket"])
