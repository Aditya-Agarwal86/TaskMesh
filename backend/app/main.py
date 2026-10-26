from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.api.health import router as health_router
from app.api.v1 import api_v1_router
from app.services.redis_listener import start_redis_pubsub_listener, stop_redis_pubsub_listener

setup_logging()
logger = get_logger("taskmesh.api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting TaskMesh API Server", extra={"environment": settings.ENVIRONMENT})
    start_redis_pubsub_listener()
    yield
    stop_redis_pubsub_listener()
    logger.info("Stopping TaskMesh API Server")



app = FastAPI(
    title="TaskMesh — Distributed Task Processing Platform",
    description="Scalable distributed task orchestration with priority scheduling, transactional outbox, and fault-tolerant leases.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Exception handlers for uniform error structures
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request parameters or payload",
                "details": exc.errors(),
            }
        },
    )


# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Route registration
app.include_router(health_router)
app.include_router(api_v1_router)

@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok", "service": "taskmesh-api", "environment": settings.ENVIRONMENT}



@app.get("/")
async def root():
    return {
        "service": "TaskMesh API",
        "version": "0.1.0",
        "docs_url": "/docs",
        "health_live": "/health/live",
        "health_ready": "/health/ready",
    }
