"""Comparação de nomes informados, preservando o CPF/ID como vínculo obrigatório."""

import unicodedata


def normalize_customer_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in normalized if not unicodedata.combining(c)).split())
