from fastapi import APIRouter
from app.api.endpoints import router as system_router

api_router_v1 = APIRouter()
api_router_v1.include_router(system_router, prefix="/system", tags=["System"])
