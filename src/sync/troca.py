"""Carga por troca: tabela-sombra + RENAME, para o sync não bloquear o app (DE#108, ADR 0005).

POR QUE EXISTE. A carga no lugar (TRUNCATE + COPY na mesma transação) pega ACCESS EXCLUSIVE no
TRUNCATE e só solta no commit, ao fim do COPY: toda leitura do app na tabela fica na fila e o
PostgREST a cancela por statement_timeout (3 s `anon`, 8 s `authenticated`). O lock dura o tempo
de LEITURA DO BIGQUERY (que começa depois do TRUNCATE): `fact_fixtures` segura 22-75 s toda
hora, e em 26/09 foram 52 cancelamentos às 18:00 UTC. Aqui a tabela-sombra é carregada, indexada
e analisada fora do caminho dos leitores (que seguem vendo o dado antigo) e trocada pela
vigente numa transação de milissegundos.

O QUE A TROCA FAZ (uma transação, com `lock_timeout` curto):
    RENAME vigente -> __old  (aqui espera o lock; é o único ponto em que um leitor atrapalha)
    reaplica dono, permissões, RLS (com force), políticas, opções e comentário LIDOS da __old
    confere o FINGERPRINT de formato (vigente vs sombra, sob lock)
    RENAME sombra -> vigente, DROP __old, devolve aos índices os nomes canônicos
    atualiza o estado de sincronização
Qualquer falha (teto de espera, formato divergente, dependente novo) reverte tudo: a vigente
fica intacta, o estado NÃO avança e a tabela falha alto (`TrocaFalhou`). A sombra é carregada
e commitada ANTES da troca, então as retentativas a reaproveitam.

POR QUE A SOMBRA É DERIVADA, NUNCA DEFINIDA. O DDL é do app. `CREATE TABLE ... LIKE ...
INCLUDING ALL EXCLUDING INDEXES` não leva RLS, políticas, ACL, dono nem o comentário da tabela
(medido): por isso são relidos do catálogo da vigente e reaplicados sob lock. Os índices e as
constraints únicas são criados DEPOIS da carga (mais rápido), com nome provisório derivado do
canônico, e voltam ao nome canônico depois do DROP da velha, que libera os nomes (migrations do
app criam índice por nome com `IF NOT EXISTS`).

QUEM NÃO ENTRA NA TROCA. Tabela com dependente por OID (view, regra, função com o tipo da tabela
na assinatura, sequência, FK, trigger, publicação...) faria o DROP da velha falhar toda hora ou
perderia o dependente em silêncio: hoje são as cinco `int_futebol_premissas_*`, por causa da
`vw_premissas_acesas`. Elas caem no caminho STAGED (COPY para temporária e, numa transação
curta, TRUNCATE + INSERT...SELECT, com o mesmo teto de espera) quando habilitado, ou na carga
no lugar com WARNING.

PROTEÇÕES. `fact_odds_snapshot` NÃO entra até a DE#109 (ADR 0006): ela não é append-only, e a
sombra completa custaria ~+920 MB. NBA nunca usa a troca. O módulo só cria e remove sombra com a
trava de sync (DE#107) já em mãos: quem chama é `bq_to_postgres.run_sync`.

Sem dependência de nuvem; só psycopg (os testes de integração usam um Postgres 17 descartável).
"""
import hashlib
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

import psycopg

from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# ============================================================
# Constantes (números da spec DE#112; todos parâmetros do contexto, nunca hardcode no fluxo)
# ============================================================
SUFIXO_SOMBRA = "__new"
SUFIXO_VELHA = "__old"
SUFIXO_STAGED = "__stg"
MARCADOR_SOMBRA = "sync-sombra criada em "
MAX_IDENT = 63  # limite de identificador do Postgres

TETO_ESPERA_MS = 2000  # `lock_timeout` de cada tentativa de troca
TENTATIVAS = 8
PAUSA_MIN_S = 8.0  # pausa entre tentativas (com jitter): 8 tentativas ~ 60-90 s no total
PAUSA_MAX_S = 12.0
ORCAMENTO_RETENTATIVAS_S = 300.0  # teto de tempo gasto em retentativas POR EXECUÇÃO
IDADE_ORFA_S = 2 * 3600  # sombra órfã mais velha que isto gera aviso no status

# Status parcial de `run_sync` quando alguma tabela habilitada não conseguiu trocar; o handler o
# traduz para HTTP 500 com os nomes das tabelas no corpo.
STATUS_TROCA_FALHOU = "swap_failed"

MODO_TROCA = "troca"
MODO_STAGED = "staged"
MODO_NO_LUGAR = "no_lugar"
MODO_FALLBACK = "no_lugar_fallback"  # habilitada na troca, mas tem dependente: carga no lugar

# `fact_odds_snapshot` só entra na troca depois da DE#109 (ADR 0006).
TABELAS_FORA_DA_TROCA = frozenset({"fact_odds_snapshot"})

MOTIVO_LOCK_TIMEOUT = "lock_timeout"
MOTIVO_ORCAMENTO_ESGOTADO = "orcamento_esgotado"
MOTIVO_FORMATO_DIVERGENTE = "formato_divergente"
MOTIVO_DEPENDENTE_NOVO = "dependente_novo"
MOTIVO_ERRO = "erro_na_troca"


class TrocaFalhou(Exception):
    """A tabela não foi trocada (a vigente está intacta e o estado não avançou).

    `motivo` é um código curto e SEGURO para o corpo da resposta HTTP (sem texto do banco);
    `detalhe` fica só no log.
    """

    def __init__(self, table: str, motivo: str, detalhe: str = ""):
        super().__init__(f"{table}: {motivo} {detalhe}".strip())
        self.table = table
        self.motivo = motivo
        self.detalhe = detalhe


class _FormatoDivergente(Exception):
    def __init__(self, secoes):
        super().__init__(f"seções divergentes: {secoes}")
        self.secoes = secoes


# ============================================================
# Configuração e contexto da execução
# ============================================================
@dataclass
class ConfigTroca:
    teto_espera_ms: int = TETO_ESPERA_MS
    tentativas: int = TENTATIVAS
    pausa_min_s: float = PAUSA_MIN_S
    pausa_max_s: float = PAUSA_MAX_S
    orcamento_s: float = ORCAMENTO_RETENTATIVAS_S
    idade_orfa_s: float = IDADE_ORFA_S


class ContextoTroca:
    """Estado de UMA execução do sync: o que está habilitado e o orçamento de retentativas.

    `pausa`, `relogio`, `sorteio` e `agora` são injetáveis para os testes (a pausa real é de
    segundos). `orcamento_usado_s` soma o tempo das tentativas que falharam e das pausas.
    """

    def __init__(
        self,
        cfg: ConfigTroca | None = None,
        *,
        troca: Iterable[str] = (),
        staged: Iterable[str] = (),
        pausa: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
        sorteio: Callable[[float, float], float] = random.uniform,
        agora: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.cfg = cfg or ConfigTroca()
        self.troca = frozenset(troca)
        self.staged = frozenset(staged)
        self.pausa = pausa
        self.relogio = relogio
        self.sorteio = sorteio
        self.agora = agora
        self.orcamento_usado_s = 0.0

    def orcamento_esgotado(self) -> bool:
        return self.orcamento_usado_s >= self.cfg.orcamento_s

    def consome(self, segundos: float) -> None:
        self.orcamento_usado_s += max(0.0, segundos)


def novo_contexto(troca: Iterable[str], staged: Iterable[str]) -> ContextoTroca:
    """Contexto de uma execução com os números da spec. Ponto único de criação: os testes de
    `run_sync` o substituem para encurtar pausas e teto de espera."""
    return ContextoTroca(ConfigTroca(), troca=troca, staged=staged)


def parse_lista(valor) -> frozenset:
    """'a,b' / lista / None -> conjunto de nomes (vazio = nada habilitado)."""
    if not valor:
        return frozenset()
    if isinstance(valor, str):
        valor = valor.split(",")
    return frozenset(v.strip() for v in valor if v and v.strip())


def valida_selecao(sport: str, troca: frozenset, staged: frozenset, resolved: list[str]) -> None:
    """Recusa, ANTES de tocar em qualquer coisa, uma seleção que o desenho proíbe."""
    pedidas = troca | staged
    if not pedidas:
        return
    if (sport or "").lower() != "futebol":
        raise ValueError(
            f"carga por troca só vale para o futebol (sport={sport!r}); o NBA e o resto ficam "
            f"na carga no lugar (ADR 0005)"
        )
    proibidas = sorted(pedidas & TABELAS_FORA_DA_TROCA)
    if proibidas:
        raise ValueError(
            f"{proibidas} não entram na carga por troca até a DE#109 (ADR 0006): a tabela não é "
            f"append-only e a sombra completa custaria ~+920 MB"
        )
    fora = sorted(pedidas - set(resolved))
    if fora:
        raise ValueError(f"carga por troca pedida para tabelas fora desta execução: {fora}")


# ============================================================
# SQL helpers (identificadores sempre citados; valores por parâmetro ou literal)
# ============================================================
def _q(nome: str) -> str:
    return '"' + nome.replace('"', '""') + '"'


def _qual(schema: str, nome: str) -> str:
    return f"{_q(schema)}.{_q(nome)}"


def _lit(cur, valor) -> str:
    """Literal SQL para utilitários que não aceitam parâmetro (COMMENT)."""
    if valor is None:
        return "NULL"
    return psycopg.sql.Literal(valor).as_string(cur)


def _confere_nome(table: str) -> None:
    mais_longo = max(len(SUFIXO_SOMBRA), len(SUFIXO_VELHA), len(SUFIXO_STAGED))
    if len(table) + mais_longo > MAX_IDENT:
        raise ValueError(f"nome de tabela comprido demais para a sombra: {table!r}")


def _provisorio(canonico: str) -> str:
    """Nome provisório (<= 63) e único para um índice/constraint da sombra.

    Prefixo do canônico + hash do canônico inteiro: nomes de 59+ caracteres não colidem e o
    nome provisório nunca coincide com o canônico (que ainda existe na vigente).
    """
    h = hashlib.md5(canonico.encode()).hexdigest()[:8]
    return f"{canonico[:45]}_{h}__n"


def _oid(cur, schema: str, table: str):
    cur.execute(
        "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s",
        (schema, table),
    )
    row = cur.fetchone()
    return row[0] if row else None


# ============================================================
# Checagem de dependentes (por OID)
# ============================================================
def dependentes(pg_conn, schema: str, table: str) -> list[str]:
    """Motivos pelos quais a tabela NÃO pode usar a troca (lista vazia = pode).

    Olha o catálogo por OID, não por texto: views/regras de OUTRAS relações, funções com o tipo
    da tabela na assinatura, sequências da tabela (serial/identity), chaves estrangeiras nos dois
    sentidos, triggers, publicações, regras da própria tabela, ACL por coluna, identidade de
    réplica por índice, herança/partição. O que a troca não reproduz na sombra e não é
    descartável com a velha manda a tabela para o caminho staged ou para a carga no lugar. O que
    pertence à tabela e é recriado (índices, constraints, defaults, comentários) não conta.
    """
    motivos: list[str] = []
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT c.oid, c.reltype, c.relkind::text, c.relispartition, c.relreplident::text "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s",
            (schema, table),
        )
        row = cur.fetchone()
        if row is None:
            return [f"tabela {schema}.{table} não existe"]
        oid, reltype, relkind, particao, replident = row
        if relkind != "r" or particao:
            motivos.append(f"tabela não comum (relkind={relkind}, partição={particao})")
        if replident == "i":
            motivos.append("identidade de réplica por índice")

        def nomes(sql, params):
            cur.execute(sql, params)
            return [r[0] for r in cur.fetchall()]

        for n in nomes(
            "SELECT DISTINCT c.relname FROM pg_depend d "
            "JOIN pg_rewrite r ON r.oid = d.objid "
            "JOIN pg_class c ON c.oid = r.ev_class "
            "WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass "
            "AND d.refobjid = %s AND r.ev_class <> %s ORDER BY 1",
            (oid, oid),
        ):
            motivos.append(f"view ou regra de outra relação: {n}")
        for n in nomes(
            "SELECT rulename FROM pg_rewrite WHERE ev_class = %s ORDER BY 1", (oid,)
        ):
            motivos.append(f"regra da própria tabela: {n}")
        for n in nomes(
            "SELECT DISTINCT p.proname FROM pg_depend d JOIN pg_proc p ON p.oid = d.objid "
            "WHERE d.classid = 'pg_proc'::regclass AND d.refclassid = 'pg_type'::regclass "
            "AND d.refobjid = %s ORDER BY 1",
            (reltype,),
        ):
            motivos.append(f"função com o tipo da tabela na assinatura: {n}")
        for n in nomes(
            "SELECT s.relname FROM pg_depend d JOIN pg_class s ON s.oid = d.objid "
            "WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass "
            "AND d.refobjid = %s AND s.relkind = 'S' ORDER BY 1",
            (oid,),
        ):
            motivos.append(f"sequência da tabela: {n}")
        for n in nomes(
            "SELECT conname FROM pg_constraint WHERE confrelid = %s "
            "OR (conrelid = %s AND contype = 'f') ORDER BY 1",
            (oid, oid),
        ):
            motivos.append(f"chave estrangeira: {n}")
        for n in nomes(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal ORDER BY 1",
            (oid,),
        ):
            motivos.append(f"trigger: {n}")
        for n in nomes(
            "SELECT p.pubname FROM pg_publication_rel r JOIN pg_publication p ON p.oid = r.prpubid "
            "WHERE r.prrelid = %s ORDER BY 1",
            (oid,),
        ):
            motivos.append(f"publicação: {n}")
        for n in nomes(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s AND attnum > 0 "
            "AND NOT attisdropped AND attacl IS NOT NULL ORDER BY 1",
            (oid,),
        ):
            motivos.append(f"permissão por coluna: {n}")
        for n in nomes(
            "SELECT 'herança' FROM pg_inherits WHERE inhrelid = %s OR inhparent = %s LIMIT 1",
            (oid, oid),
        ):
            motivos.append(n)
    return motivos


def escolhe_modo(pg_conn, schema: str, table: str, ctx: ContextoTroca | None) -> tuple:
    """(modo, motivo_do_fallback) da carga desta tabela, decidido NO INÍCIO da carga dela.

    staged habilitada -> staged; troca habilitada -> troca, ou fallback no lugar (com WARNING)
    se houver dependente; fora disso, a carga no lugar de sempre (sem consultar o catálogo).
    """
    if ctx is None:
        return MODO_NO_LUGAR, None
    if table in ctx.staged:
        return MODO_STAGED, None
    if table in ctx.troca:
        deps = dependentes(pg_conn, schema, table)
        pg_conn.commit()
        if deps:
            motivo = "; ".join(deps)
            logger.warning(
                f"{table}: habilitada na carga por troca, mas tem dependente(s) ({motivo}); "
                f"usando a carga no lugar (modo={MODO_FALLBACK})"
            )
            return MODO_FALLBACK, motivo
        return MODO_TROCA, None
    return MODO_NO_LUGAR, None


# ============================================================
# Fingerprint de formato
# ============================================================
_TAIL_INDICE = " USING "


def fingerprint(cur, schema: str, table: str) -> dict:
    """Retrato do formato da tabela, comparável entre a vigente e a sombra.

    Colunas (nome, tipo, ordem, NOT NULL, default, identidade, geração, storage, compressão,
    collation, comentário, ACL de coluna), índices e constraints por DEFINIÇÃO e não por nome
    (a sombra usa nomes provisórios; constraints únicas/PK/exclusão também ignoram o nome),
    CHECKs com nome, dono, permissões (grantee/privilégio, sem o grantor), RLS e force, políticas,
    opções de armazenamento, identidade de réplica, persistência, comentário da tabela,
    triggers, publicações, regras e quem referencia a tabela.
    """
    oid = _oid(cur, schema, table)
    if oid is None:
        raise ValueError(f"tabela {schema}.{table} não existe")
    fp: dict = {}

    cur.execute(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, "
        "pg_get_expr(d.adbin, d.adrelid), a.attidentity::text, a.attgenerated::text, "
        "a.attstorage::text, a.attcompression::text, col_description(a.attrelid, a.attnum), "
        "a.attcollation::regcollation::text, a.attacl::text "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (oid,),
    )
    fp["colunas"] = [tuple(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT i.indisunique, pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "WHERE i.indrelid = %s AND i.indisvalid",
        (oid,),
    )
    fp["indices"] = sorted(
        (unico, definicao.split(_TAIL_INDICE, 1)[-1]) for unico, definicao in cur.fetchall()
    )

    cur.execute(
        "SELECT contype::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = %s AND contype <> 'n'",
        (oid,),
    )
    fp["constraints"] = sorted(
        (tipo, None if tipo in ("p", "u", "x") else nome, definicao)
        for tipo, nome, definicao in cur.fetchall()
    )

    cur.execute(
        "SELECT c.relkind::text, c.relpersistence::text, c.relrowsecurity, c.relforcerowsecurity, "
        "c.relreplident::text, c.reloptions::text, pg_get_userbyid(c.relowner), "
        "obj_description(c.oid, 'pg_class') FROM pg_class c WHERE c.oid = %s",
        (oid,),
    )
    fp["tabela"] = tuple(cur.fetchone())

    cur.execute(
        "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, "
        "a.privilege_type, a.is_grantable FROM pg_class c, "
        "LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a WHERE c.oid = %s",
        (oid,),
    )
    fp["permissoes"] = sorted(tuple(r) for r in cur.fetchall())

    cur.execute(
        "SELECT policyname, permissive, roles::text, cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = %s AND tablename = %s",
        (schema, table),
    )
    fp["politicas"] = sorted(tuple(r) for r in cur.fetchall())

    def lista(sql):
        cur.execute(sql, (oid,))
        return sorted(r[0] for r in cur.fetchall())

    fp["triggers"] = lista("SELECT tgname FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal")
    fp["publicacoes"] = lista(
        "SELECT p.pubname FROM pg_publication_rel r JOIN pg_publication p ON p.oid = r.prpubid "
        "WHERE r.prrelid = %s"
    )
    fp["regras"] = lista("SELECT rulename FROM pg_rewrite WHERE ev_class = %s")
    fp["referenciada_por"] = lista("SELECT conname FROM pg_constraint WHERE confrelid = %s")
    return fp


def _compara(a: dict, b: dict) -> list[str]:
    return [secao for secao in a if a[secao] != b[secao]]


# ============================================================
# Sombra: criar, indexar, reaplicar o que o LIKE não leva, descartar
# ============================================================
@dataclass
class _Plano:
    """O que a troca precisa lembrar da sombra criada: linhas e renomeações canônicas."""

    rows: int
    renomeacoes: list  # (tipo 'constraint'|'index', provisório, canônico)


def _cria_sombra(cur, schema: str, table: str, agora: datetime) -> None:
    sombra = _qual(schema, table + SUFIXO_SOMBRA)
    vigente = _qual(schema, table)
    cur.execute(f"DROP TABLE IF EXISTS {sombra}")
    cur.execute("SELECT relpersistence::text FROM pg_class WHERE oid = %s::regclass", (vigente,))
    nao_logada = cur.fetchone()[0] == "u"
    cur.execute(
        f"CREATE {'UNLOGGED ' if nao_logada else ''}TABLE {sombra} "
        f"(LIKE {vigente} INCLUDING ALL EXCLUDING INDEXES)"
    )
    cur.execute(
        f"COMMENT ON TABLE {sombra} IS {_lit(cur, MARCADOR_SOMBRA + agora.isoformat())}"
    )


def _cria_indices(cur, schema: str, table: str) -> list:
    """Cria na sombra os índices e as constraints PK/UNIQUE/EXCLUDE da vigente, com nome
    provisório. Devolve as renomeações canônicas (feitas depois do DROP da velha)."""
    sombra = _qual(schema, table + SUFIXO_SOMBRA)
    vigente_oid = _oid(cur, schema, table)
    renomeacoes = []
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid), conindid FROM pg_constraint "
        "WHERE conrelid = %s AND contype IN ('p','u','x') ORDER BY conname",
        (vigente_oid,),
    )
    constraints = cur.fetchall()
    de_constraint = {indid for _, _, indid in constraints}
    for nome, definicao, _ in constraints:
        prov = _provisorio(nome)
        cur.execute(f"ALTER TABLE {sombra} ADD CONSTRAINT {_q(prov)} {definicao}")
        renomeacoes.append(("constraint", prov, nome))
    cur.execute(
        "SELECT i.indexrelid, ic.relname, i.indisunique, pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid "
        "WHERE i.indrelid = %s AND i.indisvalid ORDER BY ic.relname",
        (vigente_oid,),
    )
    for indexrelid, nome, unico, definicao in cur.fetchall():
        if indexrelid in de_constraint:
            continue
        prov = _provisorio(nome)
        cauda = definicao.split(_TAIL_INDICE, 1)[1]
        cur.execute(
            f"CREATE {'UNIQUE ' if unico else ''}INDEX {_q(prov)} ON {sombra} USING {cauda}"
        )
        renomeacoes.append(("index", prov, nome))
    return renomeacoes


def _reaplica(cur, schema: str, origem: str, sombra: str) -> None:
    """Relê do catálogo de `origem` (a vigente, já renomeada e travada) o que o LIKE não leva e
    reaplica em `sombra`: dono, permissões, RLS e force, políticas, opções, identidade de
    réplica e comentário da tabela."""
    o = _qual(schema, origem)
    s = _qual(schema, sombra)
    cur.execute(
        "SELECT pg_get_userbyid(c.relowner), current_user, c.relacl IS NULL, "
        "c.relrowsecurity, c.relforcerowsecurity, c.relreplident::text, c.reloptions, "
        "obj_description(c.oid, 'pg_class') FROM pg_class c WHERE c.oid = %s::regclass",
        (o,),
    )
    dono, atual, acl_padrao, rls, force, replident, reloptions, comentario = cur.fetchone()

    if dono != atual:
        cur.execute(f"ALTER TABLE {s} OWNER TO {_q(dono)}")

    # ACL: zera o que o `ALTER DEFAULT PRIVILEGES` do Supabase pôs na sombra e reaplica o da
    # vigente. ACL nula (padrão) volta a ser nula: revoga tudo e devolve só o do dono.
    cur.execute(
        "SELECT DISTINCT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END "
        "FROM pg_class c, LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a "
        "WHERE c.oid = %s::regclass",
        (s,),
    )
    for (grantee,) in cur.fetchall():
        cur.execute(f"REVOKE ALL ON TABLE {s} FROM {'PUBLIC' if grantee == 'PUBLIC' else _q(grantee)}")
    if acl_padrao:
        cur.execute(f"GRANT ALL ON TABLE {s} TO {_q(dono)}")
    else:
        cur.execute(
            "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END, "
            "a.privilege_type, a.is_grantable FROM pg_class c, LATERAL aclexplode(c.relacl) a "
            "WHERE c.oid = %s::regclass ORDER BY 1, 2",
            (o,),
        )
        for grantee, privilegio, com_opcao in cur.fetchall():
            alvo = "PUBLIC" if grantee == "PUBLIC" else _q(grantee)
            cur.execute(
                f"GRANT {privilegio} ON TABLE {s} TO {alvo}{' WITH GRANT OPTION' if com_opcao else ''}"
            )

    cur.execute(f"ALTER TABLE {s} {'ENABLE' if rls else 'DISABLE'} ROW LEVEL SECURITY")
    cur.execute(f"ALTER TABLE {s} {'FORCE' if force else 'NO FORCE'} ROW LEVEL SECURITY")
    cur.execute(
        "SELECT policyname, permissive, roles::text[], cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = %s AND tablename = %s ORDER BY policyname",
        (schema, origem),
    )
    for nome, permissiva, papeis, comando, qual, com_check in cur.fetchall():
        alvo = ", ".join("PUBLIC" if p.lower() == "public" else _q(p) for p in papeis)
        sql = (
            f"CREATE POLICY {_q(nome)} ON {s} AS {permissiva} FOR {comando} TO {alvo}"
            + (f" USING ({qual})" if qual else "")
            + (f" WITH CHECK ({com_check})" if com_check else "")
        )
        cur.execute(sql)
    if reloptions:
        cur.execute(f"ALTER TABLE {s} SET ({', '.join(reloptions)})")
    cur.execute(
        f"ALTER TABLE {s} REPLICA IDENTITY "
        f"{ {'d': 'DEFAULT', 'n': 'NOTHING', 'f': 'FULL'}[replident] }"
    )
    cur.execute(f"COMMENT ON TABLE {s} IS {_lit(cur, comentario)}")


def _descarta(pg_conn, schema: str, nome: str, ctx: ContextoTroca) -> None:
    """DROP IF EXISTS numa transação NOVA, com teto de espera. Nunca levanta (só loga)."""
    try:
        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{int(ctx.cfg.teto_espera_ms)}ms'")
            cur.execute(f"DROP TABLE IF EXISTS {_qual(schema, nome)}")
        pg_conn.commit()
    except Exception as e:
        logger.warning(f"Não consegui remover {schema}.{nome} ({type(e).__name__}): {e}")
        try:
            pg_conn.rollback()
        except Exception:
            pass


# ============================================================
# A troca
# ============================================================
def _tenta_trocar(pg_conn, schema, table, plano: _Plano, ctx, atualiza_estado) -> float:
    """Uma tentativa de troca numa transação. Devolve a duração em ms. Levanta o que der."""
    inicio = ctx.relogio()
    vigente, velha, sombra = table, table + SUFIXO_VELHA, table + SUFIXO_SOMBRA
    with pg_conn.cursor() as cur:
        cur.execute(f"SET LOCAL lock_timeout = '{int(ctx.cfg.teto_espera_ms)}ms'")
        # Único ponto de espera: pega ACCESS EXCLUSIVE na vigente (atrás dos leitores atuais).
        cur.execute(f"ALTER TABLE {_qual(schema, vigente)} RENAME TO {_q(velha)}")
        _reaplica(cur, schema, velha, sombra)
        divergentes = _compara(fingerprint(cur, schema, velha), fingerprint(cur, schema, sombra))
        if divergentes:
            raise _FormatoDivergente(divergentes)
        cur.execute(f"ALTER TABLE {_qual(schema, sombra)} RENAME TO {_q(vigente)}")
        cur.execute(f"DROP TABLE {_qual(schema, velha)}")
        for tipo, prov, canonico in plano.renomeacoes:
            if tipo == "constraint":
                cur.execute(
                    f"ALTER TABLE {_qual(schema, vigente)} RENAME CONSTRAINT {_q(prov)} TO {_q(canonico)}"
                )
            else:
                cur.execute(f"ALTER INDEX {_qual(schema, prov)} RENAME TO {_q(canonico)}")
        atualiza_estado(cur)
    pg_conn.commit()
    return (ctx.relogio() - inicio) * 1000.0


def _com_retentativas(pg_conn, table, ctx, tenta: Callable[[], float]) -> tuple:
    """Roda `tenta` até concluir, com o teto de tentativas e o orçamento da execução.

    Só o `lock_timeout` é retentado. Formato divergente e dependente novo não melhoram esperando;
    qualquer outro erro vira falha da tabela (a transação reverte: a vigente fica intacta).
    Devolve (tentativas, duração da troca em ms).
    """
    tentativas = 0
    while True:
        tentativas += 1
        t0 = ctx.relogio()
        try:
            return tentativas, tenta()
        except psycopg.errors.LockNotAvailable:
            pg_conn.rollback()
            ctx.consome(ctx.relogio() - t0)
            logger.warning(
                f"{table}: troca não conseguiu o lock em {ctx.cfg.teto_espera_ms} ms "
                f"(tentativa {tentativas}/{ctx.cfg.tentativas})"
            )
            if tentativas >= ctx.cfg.tentativas:
                raise TrocaFalhou(table, MOTIVO_LOCK_TIMEOUT, f"{tentativas} tentativas")
            if ctx.orcamento_esgotado():
                raise TrocaFalhou(table, MOTIVO_ORCAMENTO_ESGOTADO, "durante as retentativas")
            pausa = ctx.sorteio(ctx.cfg.pausa_min_s, ctx.cfg.pausa_max_s)
            ctx.consome(pausa)
            ctx.pausa(pausa)
        except _FormatoDivergente as e:
            pg_conn.rollback()
            logger.error(
                f"{table}: formato da sombra diverge da vigente ({e.secoes}); troca abortada, a "
                f"sombra é descartada e recriada no próximo ciclo"
            )
            raise TrocaFalhou(table, MOTIVO_FORMATO_DIVERGENTE, ",".join(e.secoes))
        except psycopg.errors.DependentObjectsStillExist as e:
            pg_conn.rollback()
            logger.error(
                f"{table}: apareceu dependente entre a checagem e o DROP; troca revertida, o "
                f"próximo ciclo a trata como dependente ({e})"
            )
            raise TrocaFalhou(table, MOTIVO_DEPENDENTE_NOVO, type(e).__name__)
        except Exception as e:
            pg_conn.rollback()
            logger.error(f"{table}: erro na troca ({type(e).__name__}): {e}", exc_info=True)
            raise TrocaFalhou(table, MOTIVO_ERRO, type(e).__name__)


def _confere_orcamento(table: str, ctx: ContextoTroca) -> None:
    if ctx.orcamento_esgotado():
        raise TrocaFalhou(
            table,
            MOTIVO_ORCAMENTO_ESGOTADO,
            f"orçamento de retentativas ({ctx.cfg.orcamento_s:.0f} s) já gasto nesta execução",
        )


def carrega_por_troca(
    pg_conn,
    schema: str,
    table: str,
    ctx: ContextoTroca,
    *,
    copiar: Callable,
    atualiza_estado: Callable,
) -> dict:
    """Carrega `table` por tabela-sombra e a troca pela vigente.

    `copiar(cur, alvo_qualificado) -> linhas` escreve os dados no alvo (o COPY com o flush
    periódico da DE#110 mora em quem chama). `atualiza_estado(cur)` grava o estado de
    sincronização DENTRO da transação da troca (só avança se a troca acontecer).

    Levanta `TrocaFalhou` (a vigente fica intacta e a sombra é removida numa transação nova).
    Qualquer outro erro (leitura do BigQuery, COPY) também remove a sombra e propaga como antes.
    """
    _confere_nome(table)
    _confere_orcamento(table, ctx)
    inicio = ctx.relogio()
    sombra = table + SUFIXO_SOMBRA
    try:
        # Criar a sombra (LIKE) lê a vigente com ACCESS SHARE até o fim da transação. Commit
        # logo: uma transação de carga longa que segurasse esse lock enfileiraria qualquer
        # migration do app (ALTER precisa de ACCESS EXCLUSIVE) e, atrás dela, todos os leitores.
        try:
            with pg_conn.cursor() as cur:
                cur.execute(f"SET LOCAL lock_timeout = '{int(ctx.cfg.teto_espera_ms)}ms'")
                _cria_sombra(cur, schema, table, ctx.agora())
            pg_conn.commit()
        except psycopg.errors.LockNotAvailable:
            # Uma migration do app segurando a vigente: não esperar até o statement_timeout.
            pg_conn.rollback()
            raise TrocaFalhou(table, MOTIVO_LOCK_TIMEOUT, "ao criar a sombra")
        with pg_conn.cursor() as cur:
            rows = copiar(cur, _qual(schema, sombra))
            renomeacoes = _cria_indices(cur, schema, table)
            cur.execute(f"ANALYZE {_qual(schema, sombra)}")
        pg_conn.commit()  # a sombra fica commitada: as tentativas a reaproveitam
        plano = _Plano(rows=rows, renomeacoes=renomeacoes)
        tentativas, troca_ms = _com_retentativas(
            pg_conn,
            table,
            ctx,
            lambda: _tenta_trocar(pg_conn, schema, table, plano, ctx, atualiza_estado),
        )
    except BaseException:
        _descarta(pg_conn, schema, sombra, ctx)
        raise
    return {
        "rows": rows,
        "tentativas": tentativas,
        "troca_ms": round(troca_ms, 1),
        "duracao_s": round(ctx.relogio() - inicio, 2),
    }


def carrega_staged(
    pg_conn,
    schema: str,
    table: str,
    ctx: ContextoTroca,
    *,
    copiar: Callable,
    atualiza_estado: Callable,
) -> dict:
    """Caminho staged (tabela com dependente): COPY para temporária, fora de qualquer lock da
    vigente; depois, numa transação curta, TRUNCATE + INSERT...SELECT + estado.

    Mesmo teto de espera, mesmas retentativas, mesmo tratamento de falha da troca. O lock da
    vigente dura só o INSERT...SELECT (proporcional ao tamanho da tabela: serve para as pequenas).
    """
    _confere_nome(table)
    _confere_orcamento(table, ctx)
    inicio = ctx.relogio()
    temp = table + SUFIXO_STAGED
    temp_qual = _qual("pg_temp", temp)
    vigente = _qual(schema, table)
    try:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(f"SET LOCAL lock_timeout = '{int(ctx.cfg.teto_espera_ms)}ms'")
                cur.execute(f"DROP TABLE IF EXISTS {temp_qual}")
                cur.execute(f"CREATE TEMP TABLE {_q(temp)} (LIKE {vigente} INCLUDING DEFAULTS)")
            pg_conn.commit()  # solta o ACCESS SHARE da vigente antes do COPY (ver carrega_por_troca)
        except psycopg.errors.LockNotAvailable:
            pg_conn.rollback()
            raise TrocaFalhou(table, MOTIVO_LOCK_TIMEOUT, "ao criar a temporária")
        with pg_conn.cursor() as cur:
            rows = copiar(cur, temp_qual)
            cur.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
                "AND NOT attisdropped AND attgenerated = '' ORDER BY attnum",
                (temp_qual,),  # as colunas CARREGADAS: uma migration na vigente não as muda
            )
            colunas = ", ".join(_q(r[0]) for r in cur.fetchall())
        pg_conn.commit()

        def tenta() -> float:
            t0 = ctx.relogio()
            with pg_conn.cursor() as cur:
                cur.execute(f"SET LOCAL lock_timeout = '{int(ctx.cfg.teto_espera_ms)}ms'")
                cur.execute(f"TRUNCATE TABLE {vigente}")
                cur.execute(f"INSERT INTO {vigente} ({colunas}) SELECT {colunas} FROM {temp_qual}")
                atualiza_estado(cur)
            pg_conn.commit()
            return (ctx.relogio() - t0) * 1000.0

        tentativas, troca_ms = _com_retentativas(pg_conn, table, ctx, tenta)
    finally:
        try:
            pg_conn.rollback()
            with pg_conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {temp_qual}")
            pg_conn.commit()
        except Exception:
            pass
    return {
        "rows": rows,
        "tentativas": tentativas,
        "troca_ms": round(troca_ms, 1),
        "duracao_s": round(ctx.relogio() - inicio, 2),
    }


# ============================================================
# Sombras órfãs
# ============================================================
_PADROES_SOMBRA = (r"%\_\_new", r"%\_\_old")


def limpa_sombras(pg_conn, schema: str, ctx: ContextoTroca) -> list[str]:
    """Remove toda tabela `__new`/`__old` do schema, independente do conjunto configurado.

    SÓ pode ser chamada com a trava de sync (DE#107) já em mãos: com ela, qualquer sombra
    existente é de uma execução anterior que morreu. Sombra com marcador mais velha que
    `idade_orfa_s` devolve um AVISO (várias execuções interrompidas seguidas), sem abortar as
    outras tabelas. Falha ao remover também vira aviso. Devolve a lista de avisos.
    """
    avisos: list[str] = []
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, obj_description(c.oid, 'pg_class') FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s "
            "AND c.relkind IN ('r', 'p') AND (c.relname LIKE %s OR c.relname LIKE %s) ORDER BY 1",
            (schema, *_PADROES_SOMBRA),
        )
        orfas = cur.fetchall()
    pg_conn.commit()
    for nome, comentario in orfas:
        if comentario and comentario.startswith(MARCADOR_SOMBRA):
            try:
                criada = datetime.fromisoformat(comentario[len(MARCADOR_SOMBRA):])
                idade = (ctx.agora() - criada).total_seconds()
                if idade > ctx.cfg.idade_orfa_s:
                    avisos.append(
                        f"sombra órfã {schema}.{nome} com {idade / 3600:.1f} h (> "
                        f"{ctx.cfg.idade_orfa_s / 3600:.0f} h): execução anterior interrompida"
                    )
            except ValueError:
                pass
        logger.warning(f"Removendo sombra órfã {schema}.{nome}")
        with pg_conn.cursor() as cur:
            try:
                cur.execute(f"SET LOCAL lock_timeout = '{int(ctx.cfg.teto_espera_ms)}ms'")
                cur.execute(f"DROP TABLE IF EXISTS {_qual(schema, nome)}")
                pg_conn.commit()
            except Exception as e:
                pg_conn.rollback()
                avisos.append(f"não consegui remover a sombra órfã {schema}.{nome}: {type(e).__name__}")
    return avisos
