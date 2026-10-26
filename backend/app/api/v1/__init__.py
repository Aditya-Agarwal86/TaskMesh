from fastapi import APIRouter
from app.api.v1.jobs import router as jobs_router
from app.api.v1.events import router as events_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(jobs_router)
api_v1_router.include_router(events_router)

