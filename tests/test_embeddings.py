"""Contrato do SDK real conferido com um cliente duplo, sem chave ou rede reais."""

from types import SimpleNamespace

from google import genai

from app.core.config import Settings
from app.rag.embeddings import GeminiEmbeddings, make_embeddings


def test_gemini_embedding_adapter_uses_official_sdk(monkeypatch):
    constructor, calls = {}, []

    class ClientDouble:
        def __init__(self, **kwargs):
            constructor.update(kwargs)
            self.models = self

        def embed_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.25] * 768) for _ in kwargs["contents"]]
            )

    monkeypatch.setattr(genai, "Client", ClientDouble)
    settings = Settings(
        _env_file=None, use_mock_embeddings=False, google_api_key="chave-ficticia-do-teste"
    )
    adapter = make_embeddings(settings)
    assert isinstance(adapter, GeminiEmbeddings)
    assert len(adapter.embed_documents(["política A", "política B"])) == 2
    assert len(adapter.embed_query("consulta")) == 768
    assert constructor["vertexai"] is False
    assert constructor["http_options"].timeout == 30000
    assert calls[0]["model"] == "gemini-embedding-001"
    assert calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert calls[1]["config"].task_type == "RETRIEVAL_QUERY"
    assert calls[0]["config"].output_dimensionality == 768
