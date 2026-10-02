"""Gera o mapa RPC de serving x tabela sincronizada, lendo o `pg_proc` do PRD.

POR QUE ISTO É GERADO, e não escrito à mão:
o `dbt_futebol/docs/contrato-serving-rpcs.md` do analytics-engineering é mantido à mão e
DERIVOU: em 29/08/2026 ele dizia "5 × int_futebol_premissas_* | 2 RPCs cada", mas o PRD
tinha 4 RPCs lendo a `premissas_ou`. Quem confiou na contagem removeu colunas achando que
mexia em 2 leitores. O `prop-play-predictor/docs/futebol-prod-deploy.sql` tinha derivado
igual: 18 das 20 RPCs vivas.

O QUE ESTE ARQUIVO NÃO SUBSTITUI:
a suposição de GRÃO ("esta RPC assume uma linha por fixture") não é extraível do texto da
função — continua sendo julgamento humano, no doc do AE. Este mapa cobre a metade
mecânica, que é justamente onde as duas fontes erraram.

DETERMINISMO É REQUISITO, não elegância: a saída é commitada e comparada semanalmente, e
qualquer coisa variável (data de geração, ordem de dicionário) faria o check acusar
mudança toda semana até virar ruído — a mesma doença que este projeto já tem com alarme.
Por isso: sem carimbo de data, tudo ordenado.
"""
import re

from src.sync import alvo
from src.sync.alvo import resolve_alvo_sync
from src.sync.retencao import TABELA_ODDS
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

CABECALHO = """# Mapa gerado: RPCs de serving × tabelas sincronizadas

<!-- GERADO por scripts/gera_contrato_serving.py a partir do pg_proc do PRD.
     NÃO editar à mão: o CI regenera e compara. -->

Quais funções `public.*` leem cada tabela da allowlist do sync, e quais colunas dessa
tabela aparecem no corpo. Serve para responder "quem quebra se esta coluna sair?" antes
de mexer no mart.

**Limites conhecidos.** A associação coluna→tabela é por nome: uma função que lê duas
tabelas com uma coluna homônima (`season`, `fixture_id`) lista a coluna nas duas. E
referência em literal de texto (`'linha_subindo'` entre aspas) não conta como leitura —
é a diferença entre quebrar e não quebrar num `DROP COLUMN`.

⚠️ **O limite que erra para o lado perigoso:** só contam referências QUALIFICADAS
(`alias.coluna`). Uma função de tabela única pode escrever `select linha_subindo from
futebol.int_futebol_premissas_ou` sem alias — legal mesmo com `search_path` vazio, que
obriga a qualificar *tabelas*, não *colunas* — e aparecer aqui como leitora sem nenhuma
coluna. Ler "_nenhuma coluna nomeada_" como "não lê nada" é o erro que este doc existe
para impedir: quando aparecer, abrir a função. Hoje não há nenhuma ocorrência.

A suposição de **grão** não está aqui; mora em
`analytics-engineering/dbt_futebol/docs/contrato-serving-rpcs.md`.
"""


def _referencias_de_coluna(corpo: str, colunas: set[str]) -> list[str]:
    """Colunas citadas como referência qualificada (`alias.coluna`), não como literal.

    A distinção é load-bearing: em 29/08/2026 a `get_futebol_fixture_reason_contract`
    citava 'linha_subindo' como string e não quebrava com o DROP, enquanto quatro outras
    citavam `o.linha_subindo` e quebravam.
    """
    achadas = {c for c in colunas if re.search(rf"\b\w+\.{re.escape(c)}\b", corpo)}
    return sorted(achadas)


_SQL_FUNCOES = """
    select p.oid::regprocedure::text, pg_get_functiondef(p.oid)
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    -- prokind='f' (função comum): pg_get_functiondef LEVANTA em agregado ('a') e
    -- window ('w'), e derrubaria o gerador inteiro por causa de uma entrada que
    -- nem é RPC de serving.
    where n.nspname = 'public' and p.prokind = 'f'
    order by 1
"""


def coleta_mapa(pg_conn, schema: str, tabelas) -> dict:
    """{tabela: [(assinatura, [colunas lidas]), ...]}, tudo ordenado."""
    with pg_conn.cursor() as cur:
        cur.execute(_SQL_FUNCOES)
        funcoes = cur.fetchall()

        # pg_catalog, e NÃO information_schema.columns: aquela view filtra por privilégio,
        # e este script roda como `detector_atraso`, que só tem SELECT em `_sync_state` e
        # `_detector_state`. Sob aquele papel o information_schema devolveria zero colunas
        # para as 22 tabelas — e o gerador não falharia: renderizaria "nenhuma coluna
        # nomeada" para toda RPC, de forma determinística, e o check semanal ficaria
        # vermelho para sempre contra um arquivo que parece plausível. O metadado do
        # pg_catalog é visível independentemente dos grants.
        cur.execute(
            """
            select c.relname, a.attname
            from pg_attribute a
            join pg_class c on c.oid = a.attrelid
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = %s and a.attnum > 0 and not a.attisdropped
              -- tabela, partição, view, view materializada, foreign table. Sem o filtro,
              -- índices também têm linhas em pg_attribute.
              and c.relkind in ('r', 'p', 'v', 'm', 'f')
            """,
            (schema,),
        )
        colunas_por_tabela: dict[str, set[str]] = {}
        for tabela, coluna in cur.fetchall():
            colunas_por_tabela.setdefault(tabela, set()).add(coluna)

    mapa: dict[str, list] = {}
    for tabela in sorted(tabelas):
        leitores = []
        # `schema.tabela` qualificado: é como as RPCs referenciam (elas rodam com
        # search_path vazio, então a qualificação é obrigatória e confiável).
        padrao = re.compile(rf"\b{re.escape(schema)}\.{re.escape(tabela)}\b")
        for assinatura, corpo in funcoes:
            if not padrao.search(corpo):
                continue
            lidas = _referencias_de_coluna(corpo, colunas_por_tabela.get(tabela, set()))
            leitores.append((assinatura, lidas))
        mapa[tabela] = leitores
    return mapa


def renderiza(mapa: dict) -> str:
    linhas = [CABECALHO]
    orfas = [t for t, leitores in mapa.items() if not leitores]

    for tabela, leitores in mapa.items():
        if not leitores:
            continue
        linhas.append(f"\n## `{tabela}`\n")
        linhas.append(f"Lida por {len(leitores)} RPC(s):\n")
        for assinatura, lidas in leitores:
            cols = ", ".join(f"`{c}`" for c in lidas) if lidas else "_nenhuma coluna nomeada_"
            linhas.append(f"- `{assinatura}` — {cols}")

    if orfas:
        linhas.append("\n## Sem leitor nenhum\n")
        linhas.append(
            "Sincronizadas para o Postgres mas não lidas por nenhuma função `public.*`. "
            "Ou o app as consome por outro caminho, ou estão sendo copiadas à toa:\n"
        )
        linhas.extend(f"- `{t}`" for t in orfas)

    return "\n".join(linhas) + "\n"


def gera(pg_conn=None) -> str:
    """Gera o markdown. Abre a conexão de leitura ao PRD se não vier uma pronta."""
    dataset, schema, tabelas = resolve_alvo_sync("futebol")
    if pg_conn is not None:
        return renderiza(coleta_mapa(pg_conn, schema, tabelas))

    import psycopg

    from src.config import get_pg_url_ro

    with psycopg.connect(get_pg_url_ro("prd"), connect_timeout=15) as conn:
        return renderiza(coleta_mapa(conn, schema, tabelas))


# ============================================================
# Mercados servidos x RPCs vivas (DE#109, história 38)
# ============================================================
# Os mercados de `fact_odds_snapshot` que o dbt grava e que a lista de mercados servidos NÃO traz
# para o Postgres de PRD (o `WHERE market_id IN (...)` do dbt tem 13 ids; os 6 servidos estão em
# `alvo.MERCADOS_SERVIDOS_NOMES`). Existe só para o check enxergar uma RPC que cite um desses por
# NOME ou por id; um mercado novo no dbt entra aqui quando alguém o ligar no app.
MERCADOS_CONHECIDOS_NAO_SERVIDOS: dict[int, str] = {
    7: "HT/FT Double",
    10: "Exact Score",
    45: "Corners Over Under",
    56: "Corners Asian Handicap",
    57: "Home Corners Over/Under",
    58: "Away Corners Over/Under",
    77: "Total Corners (1st Half)",
}

_NOME_NA_COMPARACAO = re.compile(r"market_name\s*(?:=|<>|!=)\s*'((?:[^']|'')*)'", re.IGNORECASE)
_NOMES_EM_LISTA = re.compile(r"market_name\s+(?:not\s+)?in\s*\(([^)]*)\)", re.IGNORECASE)
_ID_NA_COMPARACAO = re.compile(r"market_id\s*(?:=|<>|!=)\s*(\d+)", re.IGNORECASE)
_IDS_EM_LISTA = re.compile(r"market_id\s+(?:not\s+)?in\s*\(([\d,\s]+)\)", re.IGNORECASE)
_IDS_EM_ANY = re.compile(
    r"market_id\s*=\s*any\s*\(\s*(?:array\s*)?[\[(]([\d,\s]+)[\])]", re.IGNORECASE
)


def _literais(trecho: str) -> list[str]:
    return [m.replace("''", "'") for m in re.findall(r"'((?:[^']|'')*)'", trecho)]


def mercados_fora_da_lista(funcoes, schema: str, tabela: str) -> dict:
    """{assinatura: [mercados citados fora da lista de servidos]} para as funções que leem `tabela`.

    A lista é `alvo.MERCADOS_SERVIDOS_NOMES`, lida na CHAMADA (a MESMA constante do sync). Conta
    como citação: o NOME do mercado numa comparação ou lista de `market_name`, um literal igual a
    um mercado conhecido que a lista não serve (cobre `case market_name when '...'`), e o id em
    `market_id = N`, `IN (...)` e `= ANY(ARRAY[...])`. Função que não lê `schema.tabela` é ignorada.
    """
    servidos_nomes = set(alvo.MERCADOS_SERVIDOS_NOMES.values())
    servidos_ids = set(alvo.MERCADOS_SERVIDOS)
    nao_servidos_por_nome = {n: i for i, n in MERCADOS_CONHECIDOS_NAO_SERVIDOS.items()
                             if i not in servidos_ids}
    leitura = re.compile(rf"\b{re.escape(schema)}\.{re.escape(tabela)}\b")
    achados: dict[str, list[str]] = {}
    for assinatura, corpo in funcoes:
        if not leitura.search(corpo):
            continue
        fora: list[str] = []

        def anota(texto):
            if texto not in fora:
                fora.append(texto)

        nomes = [m.replace("''", "'") for m in _NOME_NA_COMPARACAO.findall(corpo)]
        for lista in _NOMES_EM_LISTA.findall(corpo):
            nomes.extend(_literais(lista))
        for nome in nomes:
            if nome in servidos_nomes:
                continue
            if nome in nao_servidos_por_nome:
                anota(f"{nome} (id {nao_servidos_por_nome[nome]})")
            else:
                anota(f"{nome} (mercado desconhecido)")
        # `case market_name when 'X'` e qualquer outro literal que seja um mercado conhecido.
        for literal in _literais(corpo):
            if literal in nao_servidos_por_nome:
                anota(f"{literal} (id {nao_servidos_por_nome[literal]})")

        ids = [int(i) for i in _ID_NA_COMPARACAO.findall(corpo)]
        for lista in (*_IDS_EM_LISTA.findall(corpo), *_IDS_EM_ANY.findall(corpo)):
            ids.extend(int(i) for i in re.findall(r"\d+", lista))
        for i in ids:
            if i not in servidos_ids:
                anota(f"market_id {i}")
        if fora:
            achados[assinatura] = fora
    return achados


def confere_mercados_servidos(pg_conn=None) -> list[str]:
    """Mensagens (uma por RPC) das funções vivas do PRD que citam mercado fora da lista de servidos.

    Lista vazia = a lista e as RPCs estão no mesmo passo. Só leitura, no molde de `gera`.
    """
    dataset, schema, _ = resolve_alvo_sync("futebol")
    if pg_conn is None:
        import psycopg

        from src.config import get_pg_url_ro

        with psycopg.connect(get_pg_url_ro("prd"), connect_timeout=15) as conn:
            return confere_mercados_servidos(conn)
    with pg_conn.cursor() as cur:
        cur.execute(_SQL_FUNCOES)
        funcoes = cur.fetchall()
    achados = mercados_fora_da_lista(funcoes, schema, TABELA_ODDS)
    return [
        f"{assinatura} cita mercado fora da lista de servidos: {', '.join(mercados)}"
        for assinatura, mercados in sorted(achados.items())
    ]

