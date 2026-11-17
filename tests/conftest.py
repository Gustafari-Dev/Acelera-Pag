"""Cada teste usa SQLite novo; embeddings e acesso externo são sempre mock."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Os testes não podem acessar serviços externos.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    from google import genai

    monkeypatch.setattr(genai, "Client", forbidden)


@pytest.fixture(scope="session")
def chroma_directory(tmp_path_factory):
    return tmp_path_factory.mktemp("chroma-offline")


@pytest.fixture
def configuration(tmp_path, chroma_directory):
    return Settings(
        _env_file=None,
        use_mock_llm=True,
        use_mock_embeddings=True,
        google_api_key="",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        chroma_persist_dir=chroma_directory,
        limite_alcada_automatica="1000.00",
        seed_demo_data=True,
        permitir_validar_externo=True,
    )


@pytest.fixture
def client(configuration):
    with TestClient(create_app(configuration)) as session:
        yield session
