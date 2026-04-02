"""
整车测试助手飞书机器人 - FastAPI Application Entry Point

Usage:
    python main.py          # Run with uvicorn
    python main.py --reload # Development mode with auto-reload
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.api.webhook import router as webhook_router
from app.api.bug_webhook import router as bug_webhook_router
from app.api.bug_automation import router as bug_automation_router
from app.api.change_awareness import router as change_awareness_router

# Initialize logging
setup_logging("feishurobot")
logger = setup_logging("feishurobot.__main__")

# Load settings
settings = get_settings()

# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="整车测试助手飞书机器人 - 模块1：飞书机器人基础",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# CORS middleware (for local testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(webhook_router)
app.include_router(bug_webhook_router)
app.include_router(bug_automation_router)
app.include_router(change_awareness_router)

# Root endpoint
@app.get("/", tags=["root"])
async def root():
    return {
        "service": settings.app_name,
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs" if settings.debug else "disabled",
    }


@app.get("/health", tags=["health"])
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting {settings.app_name}...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
