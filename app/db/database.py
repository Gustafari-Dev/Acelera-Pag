"""SQLite com autorização persistida, centavos inteiros e execução idempotente."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.schemas import (
    CasoEntrada,
    CasoResultado,
    Citacao,
    ExecucaoEntrada,
    Pedido,
    Reembolso,
    ResultadoAnalise,
    ValidacaoEntrada,
)
from app.core.validator import validar

SCHEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    pedido_id TEXT PRIMARY KEY,
    cliente_id TEXT NOT NULL,
    cpf TEXT NOT NULL,
    cliente_nome TEXT NOT NULL,
    itens_json TEXT NOT NULL,
    valor_pago_centavos INTEGER NOT NULL CHECK (valor_pago_centavos > 0),
    data_entrega TEXT NOT NULL,
    compra_online INTEGER NOT NULL CHECK (compra_online IN (0, 1)),
    simular_falha INTEGER NOT NULL DEFAULT 0,
    tentativas_execucao INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS casos (
    case_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    resultado_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reembolsos (
    transaction_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE,
    pedido_id TEXT NOT NULL UNIQUE REFERENCES pedidos(pedido_id),
    valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
    status TEXT NOT NULL CHECK (status = 'CONCLUIDO'),
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analises (
    case_id TEXT PRIMARY KEY REFERENCES casos(case_id),
    caso_json TEXT NOT NULL,
    resultado_json TEXT NOT NULL
);
"""


def cents(value: Decimal) -> int:
    return int(value * 100)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.database_path

    @contextmanager
    def connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
        if self.settings.seed_demo_data:
            self.seed()

    def seed(self) -> None:
        today = self.settings.now().date()
        fixtures = [
            ("PED-001", "CLI-001", "11111111111", "Ana Demo", "199.90", 3, 0),
            ("PED-002", "CLI-002", "22222222222", "Bruno Demo", "150.00", 2, 0),
            ("PED-003", "CLI-003", "33333333333", "Carla Demo", "250.00", 4, 0),
            ("PED-004", "CLI-004", "44444444444", "Diego Demo", "89.90", 1, 1),
        ]
        with self.connection(immediate=True) as connection:
            for order_id, client, cpf, name, amount, days, fail in fixtures:
                connection.execute(
                    """INSERT OR IGNORE INTO pedidos
                    (pedido_id, cliente_id, cpf, cliente_nome, itens_json, valor_pago_centavos,
                    data_entrega, compra_online, simular_falha)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        order_id,
                        client,
                        cpf,
                        name,
                        json.dumps(["Produto demonstrativo"]),
                        cents(Decimal(amount)),
                        (today - timedelta(days=days)).isoformat(),
                        fail,
                    ),
                )
            # Mantém a janela de 7 dias válida em bancos antigos: só renova pedidos de demo
            # ainda intocados (sem reembolso e sem tentativa de execução).
            for order_id, *_, days, _fail in fixtures:
                connection.execute(
                    """UPDATE pedidos SET data_entrega = ?
                    WHERE pedido_id = ? AND tentativas_execucao = 0
                    AND NOT EXISTS (
                        SELECT 1 FROM reembolsos r WHERE r.pedido_id = pedidos.pedido_id
                    )""",
                    ((today - timedelta(days=days)).isoformat(), order_id),
                )
            connection.execute(
                """INSERT OR IGNORE INTO reembolsos
                (transaction_id, case_id, pedido_id, valor_centavos, status, criado_em)
                VALUES (?, ?, 'PED-002', 15000, 'CONCLUIDO', ?)""",
                (str(UUID(int=2)), str(UUID(int=102)), self.settings.now().isoformat()),
            )

    @staticmethod
    def _refund(row: sqlite3.Row) -> Reembolso:
        return Reembolso(
            transaction_id=row["transaction_id"],
            case_id=row["case_id"],
            pedido_id=row["pedido_id"],
            valor=Decimal(row["valor_centavos"]) / 100,
            status=row["status"],
            criado_em=row["criado_em"],
        )

    def _order(self, connection: sqlite3.Connection, pedido_id: str) -> Pedido:
        row = connection.execute(
            "SELECT * FROM pedidos WHERE pedido_id = ?", (pedido_id,)
        ).fetchone()
        if row is None:
            raise DomainError(404, "Pedido não encontrado.")
        refunds = connection.execute(
            "SELECT * FROM reembolsos WHERE pedido_id = ?", (pedido_id,)
        ).fetchall()
        return Pedido(
            pedido_id=row["pedido_id"],
            cliente_id=row["cliente_id"],
            cpf=row["cpf"],
            cliente_nome=row["cliente_nome"],
            itens=json.loads(row["itens_json"]),
            valor_pago=Decimal(row["valor_pago_centavos"]) / 100,
            data_entrega=row["data_entrega"],
            compra_online=bool(row["compra_online"]),
            data_referencia=self.settings.now().date(),
            historico_estornos=[self._refund(item) for item in refunds],
        )

    def get_order(self, pedido_id: str) -> Pedido:
        with self.connection() as connection:
            return self._order(connection, pedido_id)

    @staticmethod
    def _existing_case(
        connection: sqlite3.Connection, payload: ValidacaoEntrada
    ) -> CasoResultado | None:
        row = connection.execute(
            "SELECT * FROM casos WHERE case_id = ?", (str(payload.caso.case_id),)
        ).fetchone()
        if row is None:
            return None
        if ValidacaoEntrada.model_validate_json(row["payload_json"]) != payload:
            raise DomainError(409, "case_id já utilizado com outro conteúdo; gere um novo UUID.")
        return CasoResultado.model_validate_json(row["resultado_json"])

    def get_case(self, payload: ValidacaoEntrada) -> CasoResultado | None:
        with self.connection() as connection:
            return self._existing_case(connection, payload)

    def save_case(self, payload: ValidacaoEntrada, result: CasoResultado) -> CasoResultado:
        with self.connection(immediate=True) as connection:
            existing = self._existing_case(connection, payload)
            if existing is not None:
                return existing
            connection.execute(
                "INSERT INTO casos VALUES (?, ?, ?)",
                (str(payload.caso.case_id), payload.model_dump_json(), result.model_dump_json()),
            )
        return result

    @staticmethod
    def _existing_analysis(
        connection: sqlite3.Connection, caso: CasoEntrada
    ) -> ResultadoAnalise | None:
        row = connection.execute(
            "SELECT * FROM analises WHERE case_id = ?", (str(caso.case_id),)
        ).fetchone()
        if row is None:
            # Uma autorização legada não pode ser substituída pelo novo pipeline.
            if connection.execute(
                "SELECT 1 FROM casos WHERE case_id = ?", (str(caso.case_id),)
            ).fetchone():
                raise DomainError(409, "case_id já validado por outro fluxo; gere um novo UUID.")
            return None
        if CasoEntrada.model_validate_json(row["caso_json"]) != caso:
            raise DomainError(409, "case_id já analisado com outro conteúdo; gere um novo UUID.")
        return ResultadoAnalise.model_validate_json(row["resultado_json"])

    def get_analysis(self, caso: CasoEntrada) -> ResultadoAnalise | None:
        with self.connection() as connection:
            return self._existing_analysis(connection, caso)

    def save_analysis(
        self, envelope: ValidacaoEntrada, authorization: CasoResultado, result: ResultadoAnalise
    ) -> ResultadoAnalise:
        with self.connection(immediate=True) as connection:
            existing = self._existing_analysis(connection, envelope.caso)
            if existing is not None:
                return existing
            connection.execute(
                "INSERT INTO casos VALUES (?, ?, ?)",
                (
                    str(envelope.caso.case_id),
                    envelope.model_dump_json(),
                    authorization.model_dump_json(),
                ),
            )
            connection.execute(
                "INSERT INTO analises VALUES (?, ?, ?)",
                (
                    str(envelope.caso.case_id),
                    envelope.caso.model_dump_json(),
                    result.model_dump_json(),
                ),
            )
        return result

    def execute(
        self, request: ExecucaoEntrada, citation_valid: Callable[[Citacao], bool]
    ) -> Reembolso:
        temporary_failure = False
        result = None
        # O bloqueio de escrita engloba releitura, travas e inserção: evita dupla execução.
        with self.connection(immediate=True) as connection:
            previous = connection.execute(
                "SELECT * FROM reembolsos WHERE case_id = ?", (str(request.case_id),)
            ).fetchone()
            if previous is not None:
                if previous["pedido_id"] != request.pedido_id or previous[
                    "valor_centavos"
                ] != cents(request.valor):
                    raise DomainError(409, "case_id já executado com outro pedido ou valor.")
                return self._refund(previous)

            order = self._order(connection, request.pedido_id)
            if order.historico_estornos:
                raise DomainError(409, "Pedido já possui reembolso concluído.")
            saved = connection.execute(
                "SELECT * FROM casos WHERE case_id = ?", (str(request.case_id),)
            ).fetchone()
            if saved is None:
                raise DomainError(403, "Execução exige autorização prévia em POST /casos/analisar.")
            payload = ValidacaoEntrada.model_validate_json(saved["payload_json"])
            analysis = CasoResultado.model_validate_json(saved["resultado_json"])
            if (
                payload.caso.pedido_id != request.pedido_id
                or payload.caso.valor_solicitado != request.valor
            ):
                raise DomainError(409, "Pedido/valor não correspondem à autorização persistida.")
            age = (self.settings.now() - analysis.recebido_em).total_seconds()
            if age < 0 or age > self.settings.autorizacao_ttl_seconds:
                raise DomainError(403, "Autorização expirada; gere novo case_id e revalide.")
            validation = validar(
                payload,
                order,
                analysis.recebido_em,
                self.settings.limite_alcada_automatica,
                citation_valid,
            )
            if not analysis.autorizado_execucao or not validation.autorizado_execucao:
                raise DomainError(403, validation.motivo_bloqueio or "Caso bloqueado na validação.")

            simulation = connection.execute(
                "SELECT simular_falha, tentativas_execucao FROM pedidos WHERE pedido_id = ?",
                (request.pedido_id,),
            ).fetchone()
            connection.execute(
                "UPDATE pedidos SET tentativas_execucao = tentativas_execucao + 1 "
                "WHERE pedido_id = ?",
                (request.pedido_id,),
            )
            temporary_failure = (
                bool(simulation["simular_falha"]) and not simulation["tentativas_execucao"]
            )
            if not temporary_failure:
                result = Reembolso(
                    transaction_id=uuid4(),
                    case_id=request.case_id,
                    pedido_id=request.pedido_id,
                    valor=request.valor,
                    criado_em=self.settings.now(),
                )
                connection.execute(
                    "INSERT INTO reembolsos VALUES (?, ?, ?, ?, 'CONCLUIDO', ?)",
                    (
                        str(result.transaction_id),
                        str(result.case_id),
                        result.pedido_id,
                        cents(result.valor),
                        result.criado_em.isoformat(),
                    ),
                )
        # Lançar depois do commit preserva o contador da falha, inclusive após reinicialização.
        if temporary_failure:
            raise DomainError(503, "Falha temporária simulada. Repita o mesmo pedido e case_id.")
        if result is None:
            raise RuntimeError("Execução sem resultado persistido.")
        return result
