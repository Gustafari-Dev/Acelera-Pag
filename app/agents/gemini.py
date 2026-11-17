"""Adaptador do SDK oficial: geração estruturada e revalidação Pydantic local."""

import json
import logging
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import DomainError

logger = logging.getLogger(__name__)
Output = TypeVar("Output", bound=BaseModel)


class GeminiAgents:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.gemini_model
        try:
            self.client = genai.Client(
                api_key=settings.google_api_key.get_secret_value(),
                vertexai=False,
                http_options=types.HttpOptions(
                    timeout=settings.llm_timeout_seconds * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        except Exception as exc:
            raise DomainError(502, "Não foi possível iniciar o cliente Gemini.") from exc

    def generate(self, agent: str, prompt: str, context: dict, schema: type[Output]) -> Output:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=json.dumps(context, ensure_ascii=False, sort_keys=True),
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,
                    max_output_tokens=8192,
                ),
            )
            feedback = getattr(response, "prompt_feedback", None)
            if getattr(feedback, "block_reason", None):
                raise ValueError("Resposta bloqueada pelo provedor.")
            for candidate in getattr(response, "candidates", None) or []:
                reason = getattr(candidate, "finish_reason", None)
                if reason is not None and getattr(reason, "value", reason) != "STOP":
                    raise ValueError("Geração incompleta ou interrompida.")
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, BaseModel):
                # Revalida até um objeto Pydantic entregue pelo SDK, sem confiar no construtor.
                parsed = parsed.model_dump(mode="json")
            if parsed is not None:
                return schema.model_validate(parsed)
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Resposta sem JSON estruturado.")
            return schema.model_validate_json(text)
        except Exception as exc:
            # Não registrar resposta, relato, chave ou mensagem de erro bruta do SDK.
            logger.warning("Falha no agente %s: %s", agent, type(exc).__name__)
            raise DomainError(
                502, f"Falha ou resposta inválida do agente {agent}; execução não autorizada."
            ) from exc

    def close(self) -> None:
        self.client.close()
