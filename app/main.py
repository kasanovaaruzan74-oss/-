import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.database.db import engine, init_db
from app.routers.auth import router as auth_router
from app.routers.documents import router as documents_router


def create_app(database_engine: Engine = engine, *, auth_required: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(database_engine)
        yield

    application = FastAPI(
        title="DeanFlow оқу API",
        description="Деканат құжаттарын басқарудың зертханалық REST API үлгісі.",
        version="1.1.0",
        lifespan=lifespan,
    )
    application.state.database_engine = database_engine
    application.state.auth_required = (
        os.getenv("AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes"}
        if auth_required is None
        else auth_required
    )
    application.include_router(documents_router)
    application.include_router(auth_router)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _error: RequestValidationError) -> JSONResponse:
        secured = request.app.state.auth_required or request.url.path.startswith("/auth/")
        return JSONResponse(
            status_code=400 if secured else 422,
            content={"detail": "Сұрау деректері жарамсыз. Міндетті өрістер мен пішімді тексеріңіз."},
        )

    @application.get("/", tags=["Жүйе"])
    def root() -> dict[str, str | bool]:
        return {
            "жүйе": "DeanFlow оқу API",
            "құжаттама": "/docs",
            "auth_required": application.state.auth_required,
        }

    @application.get("/health", tags=["Жүйе"])
    def health() -> dict[str, str]:
        return {"күйі": "жұмыс істеп тұр"}

    return application


app = create_app()
