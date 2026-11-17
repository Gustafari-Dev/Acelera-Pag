"""Busca vetorial com citações conferidas contra os documentos de origem."""

from app.core.config import Settings
from app.core.schemas import Citacao
from app.rag.ingest import open_index


class PolicyRetriever:
    def __init__(self, settings: Settings) -> None:
        self.collection, self.embeddings, sections = open_index(settings)
        self.catalog = {section.key: section for section in sections}

    def valid(self, citation: Citacao) -> bool:
        return self.catalog.get(citation.key) == citation

    def search(self, query: str, k: int = 5) -> list[Citacao]:
        result = self.collection.query(
            query_embeddings=[self.embeddings.embed_query(query)],
            n_results=min(max(k, 1), self.collection.count()),
            include=["documents", "metadatas"],
        )
        return [
            Citacao(trecho=document, **metadata)
            for document, metadata in zip(
                result["documents"][0], result["metadatas"][0], strict=True
            )
        ]

    def section(self, document: str, section: str) -> Citacao:
        result = self.collection.get(
            ids=["|".join((document, "v1", section))], include=["documents", "metadatas"]
        )
        if not result["ids"]:
            raise ValueError(f"Seção normativa obrigatória ausente: {document}/{section}")
        return Citacao(trecho=result["documents"][0], **result["metadatas"][0])
