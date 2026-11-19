"""Adaptador do SDK oficial: geração estruturada e revalidação Pydantic local."""

import json
import logging
import time
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.schemas import (
    ParecerCoordenador,
    ParecerEvidencias,
    Recomendacao,
    RespostaPoliticas,
)

logger = logging.getLogger(__name__)
Output = TypeVar("Output", bound=BaseModel)

# Sobrecarga momentânea do provedor: uma única nova tentativa curta. Persistindo, 502.
STATUS_TRANSITORIOS = frozenset({503, 504})
NOVAS_TENTATIVAS = 1
ESPERA_RETRY_SEGUNDOS = 1.0


# Contratos enviados como response_schema. Só usam tipos que a API REST do Gemini aceita
# (str, int, bool, list, objetos simples e enum de strings). Os modelos de domínio usam
# extra="forbid", que o SDK serializa como "additional_properties": a Gemini Developer API
# rejeita esse campo com 400 INVALID_ARGUMENT. A rigidez do domínio continua garantida
# porque a resposta é revalidada contra o modelo de domínio em generate().
class _SaidaLLM(BaseModel):
    pass


class ReferenciaLLM(_SaidaLLM):
    # Só a chave da seção: o trecho integral e o sha256 são anexados pelo backend a partir
    # dos chunks recuperados (pipeline.hidratar_citacoes), sem o modelo reescrevê-los.
    documento: str
    versao: str
    secao: str
    relevancia: str


class EvidenciasLLM(_SaidaLLM):
    coincidencias: list[str]
    divergencias: list[str]
    informacoes_ausentes: list[str]
    vinculo_valido: bool
    anexo_legivel: bool


class PoliticasLLM(_SaidaLLM):
    dias_desde_entrega: int
    elegivel_automatico: bool
    fundamentacao: str
    citacoes: list[ReferenciaLLM]


class CoordenadorLLM(_SaidaLLM):
    recomendacao: Recomendacao
    justificativa: str


SCHEMAS_LLM: dict[type[BaseModel], type[_SaidaLLM]] = {
    ParecerEvidencias: EvidenciasLLM,
    RespostaPoliticas: PoliticasLLM,
    ParecerCoordenador: CoordenadorLLM,
}


def _resumo_erro(exc: Exception, secret: SecretStr) -> str:
    """Resumo seguro para log: código/status da API, nunca chave, prompt ou resposta."""
    if not isinstance(exc, errors.APIError):
        # Erros de validação carregam valores da resposta do modelo; registrar só o tipo.
        return type(exc).__name__
    message = " ".join(str(exc.message or "").split())
    key = secret.get_secret_value()
    if key:
        message = message.replace(key, "<redacted>")
    return f"{type(exc).__name__} {exc.code} {exc.status or 'UNKNOWN'}: {message[:200]}"


class GeminiAgents:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.gemini_model
        self._secret = settings.google_api_key
        self._thinking = (
            types.ThinkingConfig(
                thinking_level=types.ThinkingLevel(settings.gemini_thinking_level.upper())
            )
            if settings.gemini_thinking_level
            else None
        )
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

    def _generate_with_retry(self, agent: str, **request) -> types.GenerateContentResponse:
        for attempt in range(NOVAS_TENTATIVAS + 1):
            try:
                return self.client.models.generate_content(**request)
            except errors.APIError as exc:
                if exc.code not in STATUS_TRANSITORIOS or attempt == NOVAS_TENTATIVAS:
                    raise
                logger.warning(
                    "Agente %s: %s %s transitório; nova tentativa em %.1fs",
                    agent,
                    exc.code,
                    exc.status or "UNKNOWN",
                    ESPERA_RETRY_SEGUNDOS,
                )
                time.sleep(ESPERA_RETRY_SEGUNDOS)
        raise AssertionError("inalcançável")

    def generate(self, agent: str, prompt: str, context: dict, schema: type[Output]) -> Output:
        try:
            response = self._generate_with_retry(
                agent,
                model=self.model,
                contents=json.dumps(context, ensure_ascii=False, sort_keys=True),
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    response_schema=SCHEMAS_LLM[schema],
                    temperature=0,
                    max_output_tokens=8192,
                    thinking_config=self._thinking,
                ),
            )
            feedback = getattr(response, "prompt_feedback", None)
            if getattr(feedback, "block_reason", None):
                raise ValueError("Resposta bloqueada pelo provedor.")
            for candidate in getattr(response, "candidates", None) or []:
                reason = getattr(candidate, "finish_reason", None)
                if reason is not None and getattr(reason, "value", reason) != "STOP":
                    raise ValueError("Geração incompleta ou interrompida.")
            # O JSON bruto é validado contra o modelo de domínio (extra="forbid", tipos
            # estritos): campos extras ou tipos errados falham mesmo que o SDK os tolere.
            text = getattr(response, "text", None)
            if isinstance(text, str) and text.strip():
                return schema.model_validate_json(text)
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, BaseModel):
                parsed = parsed.model_dump(mode="json")
            if parsed is None:
                raise ValueError("Resposta sem JSON estruturado.")
            return schema.model_validate(parsed)
        except Exception as exc:
            # Registra só código/status da API; nunca a chave, o prompt ou a resposta.
            logger.warning("Falha no agente %s: %s", agent, _resumo_erro(exc, self._secret))
            raise DomainError(
                502, f"Falha ou resposta inválida do agente {agent}; execução não autorizada."
            ) from exc

    def close(self) -> None:
        self.client.close()
