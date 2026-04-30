from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import create_tables, get_db


load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_tables()
    yield


app = FastAPI(title="NewsLens Backend", lifespan=lifespan)
router = APIRouter()


class HealthResponse(BaseModel):
    success: bool
    data: dict
    error: Optional[str]


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return {
        "success": True,
        "data": {
            "service": "newslens-backend",
            "status": "ok",
            "debug": os.getenv("DEBUG", "False"),
        },
        "error": None,
    }


app.include_router(router)
