"""Factory FastAPI; recursos locais inicializados apenas no lifespan."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import Settings
from app.core.errors import DomainError
from app.db.database import Database
from app.rag.retriever import PolicyRetriever


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings or Settings()
        database = Database(configuration)
        database.initialize()
        application.state.settings = configuration
        application.state.database = database
        application.state.retriever = PolicyRetriever(configuration)
        yield

    application = FastAPI(
        title="Central de Reembolsos",
        version="0.1.0",
        description="Agentes Python/Gemini, RAG e validador; n8n integra Webhook e Data Lake.",
        lifespan=lifespan,
    )

    @application.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
        headers = {"Retry-After": "2"} if error.status_code == 503 else None
        return JSONResponse(
            status_code=error.status_code, content={"detail": error.detail}, headers=headers
        )

    application.include_router(router)
    return application


app = create_app()
