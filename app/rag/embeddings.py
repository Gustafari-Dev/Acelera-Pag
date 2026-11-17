"""Embeddings Gemini no modo real; hashing local reproduzível no mock."""

import hashlib
import math
import re
import unicodedata

from app.core.config import Settings


class MockEmbeddings:
    """Vetores lexicais para testes; não simulam qualidade semântica de produção."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        normalized = unicodedata.normalize("NFKD", text.lower())
        tokens = re.findall(r"[a-z0-9]+", normalized.encode("ascii", "ignore").decode())
        vector = [0.0] * 256
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % len(vector)] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class GeminiEmbeddings:
    def __init__(self, settings: Settings) -> None:
        from google import genai
        from google.genai import types

        self.client = genai.Client(
            api_key=settings.google_api_key.get_secret_value(),
            vertexai=False,
            http_options=types.HttpOptions(timeout=settings.llm_timeout_seconds * 1000),
        )
        self.model = settings.gemini_embedding_model

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        from google.genai import types

        response = self.client.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=768),
        )
        vectors = [item.values for item in response.embeddings or []]
        if len(vectors) != len(texts) or any(not values for values in vectors):
            raise RuntimeError("Gemini retornou embeddings incompletos.")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


def make_embeddings(settings: Settings) -> MockEmbeddings | GeminiEmbeddings:
    return MockEmbeddings() if settings.mock_embeddings else GeminiEmbeddings(settings)
