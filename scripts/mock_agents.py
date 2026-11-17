"""Fallback dos três agentes: Python determinístico, sem LLM nem chamadas de rede."""

from decimal import Decimal

from app.core.identity import normalize_customer_name
from app.core.schemas import (
    CasoEntrada,
    Citacao,
    Motivo,
    ParecerCoordenador,
    ParecerEvidencias,
    ParecerPoliticas,
    Pedido,
    Recomendacao,
    StatusAnexo,
    ValidacaoEntrada,
)


def analisar_evidencias(caso: CasoEntrada, pedido: Pedido) -> ParecerEvidencias:
    """Compara os campos disponíveis sem inferir evidências ausentes."""
    matches, differences, missing = [], [], []
    identities = []
    for field in ("cliente_id", "cpf"):
        value = getattr(caso, field)
        if value is not None:
            agrees = value == getattr(pedido, field)
            identities.append(agrees)
            (matches if agrees else differences).append(field)
    if not identities:
        missing.append("cliente_id ou cpf")
    if caso.cliente_nome is not None:
        agrees = normalize_customer_name(caso.cliente_nome) == normalize_customer_name(
            pedido.cliente_nome
        )
        (matches if agrees else differences).append("cliente_nome")
    if caso.valor_solicitado != pedido.valor_pago:
        differences.append("valor_solicitado diverge do valor integral pago")
    else:
        matches.append("valor_solicitado")
    if caso.data_entrega_informada is None:
        missing.append("data_entrega_informada")
    elif caso.data_entrega_informada != pedido.data_entrega:
        differences.append("data_entrega_informada")
    else:
        matches.append("data_entrega_informada")
    if caso.anexo_status == StatusAnexo.INCONSISTENTE:
        differences.append("anexo inconsistente")
    elif caso.anexo_status != StatusAnexo.OK:
        missing.append("anexo ausente ou ilegível")
    else:
        matches.append("anexo legível")
    return ParecerEvidencias(
        coincidencias=matches,
        divergencias=differences,
        informacoes_ausentes=missing,
        vinculo_valido=bool(identities) and all(identities),
        anexo_legivel=caso.anexo_status == StatusAnexo.OK,
    )


def analisar_politicas(
    caso: CasoEntrada, pedido: Pedido, trechos: list[Citacao]
) -> ParecerPoliticas:
    """Seleciona fontes recuperadas, sem criar ou reescrever nenhuma citação."""
    days = (pedido.data_referencia - pedido.data_entrega).days
    sections = (
        {"Seção 1.1", "Seção 1.3", "Seção 2.1"}
        if (caso.motivo == Motivo.ARREPENDIMENTO)
        else {"Seção 1.4", "Seção 2.2", "Seção 2.3"}
    )
    citations = [item for item in trechos if item.secao in sections]
    eligible = (
        caso.motivo == Motivo.ARREPENDIMENTO
        and pedido.compra_online
        and 0 <= days <= 7
        and any(item.secao == "Seção 2.1" for item in citations)
    )
    return ParecerPoliticas(
        dias_desde_entrega=days,
        elegivel_automatico=eligible,
        fundamentacao="Arrependimento em até 7 dias." if eligible else "Requer revisão humana.",
        citacoes=citations,
    )


def coordenar_caso(
    caso: CasoEntrada,
    pedido: Pedido,
    evidence: ParecerEvidencias,
    policies: ParecerPoliticas,
    limite: Decimal,
) -> ParecerCoordenador:
    """Mantém a ordem de decisão dos prompts reais e dos cenários legados."""
    if pedido.historico_estornos:
        decision, reason = Recomendacao.REJEITAR, "Pedido já reembolsado."
    elif evidence.divergencias:
        decision, reason = Recomendacao.REVISAO_HUMANA, "Dados divergentes."
    elif evidence.informacoes_ausentes:
        decision, reason = Recomendacao.PEDIR_INFORMACOES, "Complementar dados ou comprovante."
    elif not policies.elegivel_automatico or caso.valor_solicitado > limite:
        decision, reason = Recomendacao.REVISAO_HUMANA, "Fora dos critérios de automação."
    else:
        decision, reason = Recomendacao.APROVAR, "Evidências e política compatíveis."
    return ParecerCoordenador(recomendacao=decision, justificativa=reason)


def simulate_agents(
    caso: CasoEntrada, pedido: Pedido, trechos: list[Citacao], limite: Decimal
) -> ValidacaoEntrada:
    """Interface anterior preservada para os testes e consumidores existentes."""
    evidence = analisar_evidencias(caso, pedido)
    policies = analisar_politicas(caso, pedido, trechos)
    coordinator = coordenar_caso(caso, pedido, evidence, policies, limite)
    return ValidacaoEntrada(
        caso=caso,
        pedido_informado=pedido,
        evidencias=evidence,
        politicas=policies,
        coordenador=coordinator,
    )
