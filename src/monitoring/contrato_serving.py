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

from src.config import get_sync_target
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
é a diferença entre quebrar e não quebrar num `DROP COLUMN`. A suposição de **grão** não
está aqui; mora em `analytics-engineering/dbt_futebol/docs/contrato-serving-rpcs.md`.
"""


def _referencias_de_coluna(corpo: str, colunas: set[str]) -> list[str]:
    """Colunas citadas como referência qualificada (`alias.coluna`), não como literal.

    A distinção é load-bearing: em 29/08/2026 a `get_futebol_fixture_reason_contract`
    citava 'linha_subindo' como string e não quebrava com o DROP, enquanto quatro outras
    citavam `o.linha_subindo` e quebravam.
    """
    achadas = {c for c in colunas if re.search(rf"\b\w+\.{re.escape(c)}\b", corpo)}
    return sorted(achadas)


def coleta_mapa(pg_conn, schema: str, tabelas) -> dict:
    """{tabela: [(assinatura, [colunas lidas]), ...]}, tudo ordenado."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            select p.oid::regprocedure::text, pg_get_functiondef(p.oid)
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public'
            order by 1
            """
        )
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
    dataset, schema, tabelas = get_sync_target("futebol")
    if pg_conn is not None:
        return renderiza(coleta_mapa(pg_conn, schema, tabelas))

    import psycopg

    from src.config import get_pg_url_ro

    with psycopg.connect(get_pg_url_ro("prd"), connect_timeout=15) as conn:
        return renderiza(coleta_mapa(conn, schema, tabelas))
