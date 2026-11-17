"""Contratos estritos: dinheiro decimal, enumerações e campos extras proibidos."""

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Identifier = Annotated[str, Field(min_length=1, max_length=100)]
PedidoID = Annotated[str, Field(pattern=r"^PED-\d{3}$")]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)


class Motivo(StrEnum):
    ARREPENDIMENTO = "ARREPENDIMENTO"
    DEFEITO = "DEFEITO"
    OUTRO = "OUTRO"


class Recomendacao(StrEnum):
    APROVAR = "APROVAR"
    PEDIR_INFORMACOES = "PEDIR_INFORMACOES"
    REVISAO_HUMANA = "REVISAO_HUMANA"
    REJEITAR = "REJEITAR"


class StatusAnexo(StrEnum):
    OK = "OK"
    ILEGIVEL = "ILEGIVEL"
    INCONSISTENTE = "INCONSISTENTE"
    AUSENTE = "AUSENTE"


class CasoEntrada(Schema):
    case_id: UUID
    pedido_id: PedidoID
    cliente_id: Identifier | None = None
    cliente_nome: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    cpf: str | None = None
    email: Annotated[
        str, Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    ]
    valor_solicitado: Money
    motivo: Motivo
    relato: Annotated[str, Field(min_length=1, max_length=8000)]
    data_entrega_informada: date | None = None
    anexo_status: StatusAnexo = StatusAnexo.AUSENTE

    @field_validator("cpf")
    @classmethod
    def normalize_cpf(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[0-9.\-\s]+", value):
            raise ValueError("CPF deve conter somente dígitos e formatação usual.")
        digits = re.sub(r"\D", "", value)
        if len(digits) != 11:
            raise ValueError("CPF deve conter 11 dígitos.")
        return digits


class ExecucaoEntrada(Schema):
    pedido_id: PedidoID
    case_id: UUID
    valor: Money


class Reembolso(Schema):
    transaction_id: UUID
    case_id: UUID
    pedido_id: PedidoID
    valor: Money
    status: str = "CONCLUIDO"
    criado_em: datetime


class Pedido(Schema):
    pedido_id: PedidoID
    cliente_id: str
    cpf: str
    cliente_nome: str
    itens: list[str]
    valor_pago: Money
    data_entrega: date
    compra_online: StrictBool
    data_referencia: date
    historico_estornos: list[Reembolso] = Field(default_factory=list)


class Citacao(Schema):
    documento: str
    versao: str
    secao: str
    trecho: str
    sha256: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.documento, self.versao, self.secao


class ParecerEvidencias(Schema):
    coincidencias: list[str]
    divergencias: list[str]
    informacoes_ausentes: list[str]
    vinculo_valido: StrictBool
    anexo_legivel: StrictBool


class ParecerPoliticas(Schema):
    dias_desde_entrega: Annotated[int, Field(strict=True)]
    elegivel_automatico: StrictBool
    fundamentacao: str
    citacoes: list[Citacao]


class ParecerCoordenador(Schema):
    recomendacao: Recomendacao
    justificativa: str


class ParecerValidador(Schema):
    autorizado_execucao: StrictBool
    motivo_bloqueio: str | None
    verificacoes: dict[str, bool]


class ConsultaRAG(Schema):
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    top_k: Annotated[int, Field(strict=True, ge=1, le=20)] = 5


class CasoResultado(Schema):
    case_id: UUID
    pedido_id: PedidoID
    valor_solicitado: Money
    recebido_em: datetime
    modo: str
    evidencias: ParecerEvidencias
    politicas: ParecerPoliticas
    coordenador: ParecerCoordenador
    validador: ParecerValidador
    autorizado_execucao: StrictBool
    motivo_bloqueio: str | None


class ValidacaoEntrada(Schema):
    caso: CasoEntrada
    pedido_informado: Pedido | None = None
    evidencias: ParecerEvidencias
    politicas: ParecerPoliticas
    coordenador: ParecerCoordenador


class ResultadoRAG(Schema):
    trechos: list[Citacao]
    modo: str


class RastroEtapa(Schema):
    etapa: str
    duracao_ms: Annotated[float, Field(ge=0)]
    entrada_sha256: str
    saida_sha256: str


class MetadadosAnalise(Schema):
    analise_id: UUID
    modo: str
    motivo_modo_mock: str | None
    modelo_usado: str
    modo_embeddings: str
    iniciado_em: datetime
    concluido_em: datetime
    tempo_total_ms: Annotated[float, Field(ge=0)]
    versao_pipeline: str
    prompts_sha256: dict[str, str]
    rastreabilidade: list[RastroEtapa]


class ResultadoAnalise(Schema):
    caso: CasoEntrada
    pedido_oficial: Pedido
    parecer_evidencias: ParecerEvidencias
    parecer_politicas: ParecerPoliticas
    parecer_coordenador: ParecerCoordenador
    validacao: ParecerValidador
    autorizado_execucao: StrictBool
    motivo_bloqueio: str | None
    metadados: MetadadosAnalise
