"""Ingestão idempotente em Chroma, preservando as seções numeradas inteiras."""

import hashlib
import re
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import Settings
from app.core.schemas import Citacao
from app.rag.embeddings import make_embeddings

POLICY_FILES = ("politica_interna_reembolsos_v1.md", "cdc_artigos_selecionados_v1.md")


def load_sections(docs_dir: Path) -> list[Citacao]:
    sections: list[Citacao] = []
    for filename in POLICY_FILES:
        content = (docs_dir / filename).read_text(encoding="utf-8")
        document = re.search(r"^Documento:\s*(\S+)\s*$", content, re.MULTILINE)
        version = re.search(r"^Versao:\s*(\S+)\s*$", content, re.MULTILINE)
        headings = list(re.finditer(r"^## Seção (\d+\.\d+)\s*-.*$", content, re.MULTILINE))
        if not document or not version or not headings:
            raise ValueError(f"Política sem metadados/seções numeradas: {filename}")
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            text = content[heading.start() : end].strip()
            sections.append(
                Citacao(
                    documento=document[1],
                    versao=version[1],
                    secao=f"Seção {heading[1]}",
                    trecho=text,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                )
            )
    if len({section.key for section in sections}) != len(sections):
        raise ValueError("Existem seções duplicadas na base normativa.")
    return sections


def section_id(section: Citacao) -> str:
    return "|".join(section.key)


def open_index(settings: Settings):
    sections = load_sections(settings.policy_docs_dir)
    provider = "mock-hash-v1" if settings.mock_embeddings else settings.gemini_embedding_model
    fingerprint = hashlib.sha256(
        (provider + "".join(s.model_dump_json() for s in sections)).encode()
    ).hexdigest()[:24]
    # Coleções distintas impedem misturar dimensões, modelos e versões de documentos.
    client = chromadb.PersistentClient(
        path=str(settings.chroma_persist_dir.resolve()),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=f"reembolsos-{fingerprint}",
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )
    embeddings = make_embeddings(settings)
    if collection.count() != len(sections):
        collection.upsert(
            ids=[section_id(section) for section in sections],
            documents=[section.trecho for section in sections],
            metadatas=[section.model_dump(exclude={"trecho"}) for section in sections],
            embeddings=embeddings.embed_documents([section.trecho for section in sections]),
        )
    return collection, embeddings, sections


def main() -> None:
    collection, _, sections = open_index(Settings())
    print(f"Ingestão concluída: {len(sections)} seções; coleção {collection.name}.")


if __name__ == "__main__":
    main()
