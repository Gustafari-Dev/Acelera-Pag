"""Barreira financeira em Python puro; nenhuma afirmação do LLM é confiada sozinha."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from app.core.identity import normalize_customer_name
from app.core.schemas import (
    Citacao,
    Motivo,
    ParecerValidador,
    Pedido,
    Recomendacao,
    StatusAnexo,
    ValidacaoEntrada,
)


def validar(
    payload: ValidacaoEntrada,
    pedido: Pedido,
    recebido_em: datetime,
    limite: Decimal,
    citacao_valida: Callable[[Citacao], bool],
) -> ParecerValidador:
    """Recalcula as condições com o pedido oficial lido do SQLite pelo chamador."""
    caso = payload.caso
    provided = []
    if caso.cliente_id is not None:
        provided.append(caso.cliente_id == pedido.cliente_id)
    if caso.cpf is not None:
        provided.append(caso.cpf == pedido.cpf)
    dias = (recebido_em.date() - pedido.data_entrega).days
    citations = payload.politicas.citacoes
    checked = [citation for citation in citations if citacao_valida(citation)]
    # A aprovação automática fica restrita ao caso de arrependimento coberto pela política.
    applicable = any(
        citation.key == ("cdc_artigos_selecionados", "v1", "Seção 2.1") for citation in checked
    )
    checks = {
        "coordenador_aprovou": payload.coordenador.recomendacao == Recomendacao.APROVAR,
        "valor_nao_supera_pago": caso.valor_solicitado <= pedido.valor_pago,
        "sem_reembolso_anterior": not pedido.historico_estornos,
        "vinculo_cliente_valido": bool(provided) and all(provided),
        "nome_informado_confere": (
            caso.cliente_nome is None
            or normalize_customer_name(caso.cliente_nome)
            == normalize_customer_name(pedido.cliente_nome)
        ),
        "citacao_normativa_valida": (
            bool(checked)
            and len(checked) == len(citations)
            and not payload.politicas.citacoes_rejeitadas
        ),
        "citacao_aplicavel": applicable,
        "dentro_da_alcada": caso.valor_solicitado <= limite,
        "anexo_consistente": caso.anexo_status == StatusAnexo.OK,
        "data_informada_confere": caso.data_entrega_informada == pedido.data_entrega,
        "evidencias_sem_pendencias": (
            payload.evidencias.vinculo_valido
            and payload.evidencias.anexo_legivel
            and not payload.evidencias.divergencias
            and not payload.evidencias.informacoes_ausentes
        ),
        "politica_automaticamente_aplicavel": (
            caso.motivo == Motivo.ARREPENDIMENTO
            and pedido.compra_online
            and 0 <= dias <= 7
            and payload.politicas.elegivel_automatico
            and payload.politicas.dias_desde_entrega == dias
        ),
        "pedido_informado_confere": (
            payload.pedido_informado is None or payload.pedido_informado == pedido
        ),
    }
    blocked = [name for name, passed in checks.items() if not passed]
    return ParecerValidador(
        autorizado_execucao=not blocked,
        motivo_bloqueio=", ".join(blocked) if blocked else None,
        verificacoes=checks,
    )


# Nome público usado pelo pipeline; o nome anterior permanece para compatibilidade.
validar_caso = validar
