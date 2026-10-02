"""Linha de base da RPC `get_futebol_fixture_quotes` antes do cache de serving (DE#109).

POR QUE EXISTE. O critério de pronto da #109 (spec #112) exige a RPC idêntica "antes e depois"
do cache. Depois de ligar o cache em PRD a saída de antes não existe mais: o único jeito de
comparar é GRAVAR a saída antes, de uma amostra de fixtures, e reconsultar os MESMOS ids depois.

DOIS MODOS (só leitura; a conexão é posta em `read_only`):
- captura: sorteia (determinístico, por md5 do id) fixtures de três grupos, chama a RPC para cada
  uma e grava ids e saída num JSON que NUNCA é sobrescrito;
- diff: lê o JSON, reconsulta os mesmos ids (`fact_fixtures` não é cortada, os ids seguem valendo)
  e compara linha a linha. Chave da linha = (market_key, outcome_label).

OS TRÊS GRUPOS E O QUE SE ESPERA DEPOIS DO CORTE
- `recente`: kickoff nos últimos `DIAS_MAX_RECENTE` dias, jogo já disputado. Tem de ser IDÊNTICO.
  O limite é menor que os 30 dias do cache de propósito: uma fixture a 29,9 dias na captura teria
  passado dos 30 até o diff e perderia legitimamente as janelas que não são o T-15m;
- `futura`: a coleta continua entre as duas capturas (a RPC usa a janela mais recente de jogo
  futuro), então valor diferente é AVISO; linha que SOME é falha;
- `antiga`: mais de 30 dias. Só o T-15m sobrou e a RPC já prefere o T-15m de jogo passado, então
  as linhas se mantêm; `pin_open` (T-24h da Pinnacle) some e é AVISO. Os ~13 jogos sem T-15m são
  a exceção aceita (história 56): se a amostra pegar um, a linha sumida aparece como falha e se
  explica pelo próprio fixture.

Vermelho (falha): linha que sumiu em qualquer grupo; valor diferente ou linha nova em `recente`.
Ponto flutuante:`avg_odd` é média de float8 e a ordem física muda depois do TRUNCATE+carga, então
a comparação usa tolerância relativa de 1e-9.
"""
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DIAS_MAX_RECENTE = 25
DIAS_CORTE_CACHE = 30
GRUPOS = ("recente", "futura", "antiga")
TOLERANCIA_RELATIVA = 1e-9
STATEMENT_TIMEOUT_S = 90

# Só SELECT. `exists` em fact_odds_snapshot: fixture sem odds não prova nada. A ordem por md5 do id é
# determinística e espalha a amostra (sem `random()`, que mudaria a cada chamada).
SQL_AMOSTRA = f"""
(select f.fixture_id, f.kickoff_utc, 'recente' as grupo
   from futebol.fact_fixtures f
  where f.kickoff_utc <= (now() at time zone 'UTC')
    and f.kickoff_utc >= (now() at time zone 'UTC') - interval '{DIAS_MAX_RECENTE} days'
    and exists (select 1 from futebol.fact_odds_snapshot o where o.fixture_id = f.fixture_id)
  order by md5(f.fixture_id::text) limit %(n)s)
union all
(select f.fixture_id, f.kickoff_utc, 'futura' as grupo
   from futebol.fact_fixtures f
  where f.kickoff_utc > (now() at time zone 'UTC')
    and exists (select 1 from futebol.fact_odds_snapshot o where o.fixture_id = f.fixture_id)
  order by md5(f.fixture_id::text) limit %(n)s)
union all
(select f.fixture_id, f.kickoff_utc, 'antiga' as grupo
   from futebol.fact_fixtures f
  where f.kickoff_utc < (now() at time zone 'UTC') - interval '{DIAS_CORTE_CACHE} days'
    and exists (select 1 from futebol.fact_odds_snapshot o where o.fixture_id = f.fixture_id)
  order by md5(f.fixture_id::text) limit %(n)s)
"""

SQL_RPC = "select * from public.get_futebol_fixture_quotes(%s)"


def _json_seguro(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _linhas_do_cursor(cur) -> list[dict]:
    colunas = [d[0] for d in cur.description]
    return [{c: _json_seguro(v) for c, v in zip(colunas, r)} for r in cur.fetchall()]


def _chama_rpc(conn, fixture_id: int) -> list[dict]:
    return _linhas_do_cursor(conn.execute(SQL_RPC, (fixture_id,)))


def _prepara(conn) -> None:
    """Somente leitura (ANTES de qualquer consulta: o psycopg recusa mudar dentro de transação) e
    um teto de tempo: a carga horária das odds pode segurar lock na tabela."""
    conn.read_only = True
    conn.execute(f"set statement_timeout = '{STATEMENT_TIMEOUT_S}s'")


def captura(conn, n_por_grupo: int, agora: datetime) -> dict:
    """Amostra os grupos, chama a RPC para cada fixture e devolve o arquivo (dict JSON-ável)."""
    _prepara(conn)
    amostra = _linhas_do_cursor(conn.execute(SQL_AMOSTRA, {"n": n_por_grupo}))
    if not any(a["grupo"] == "recente" for a in amostra):
        raise ValueError(
            "a amostra não trouxe nenhuma fixture 'recente' com odds: sem ela o diff seria "
            "vacuamente verde. Confira fact_fixtures/fact_odds_snapshot no PRD."
        )
    fixtures = [
        {
            "fixture_id": a["fixture_id"],
            "kickoff_utc": a["kickoff_utc"],
            "grupo": a["grupo"],
            "linhas": _chama_rpc(conn, a["fixture_id"]),
        }
        for a in amostra
    ]
    if not any(f["linhas"] for f in fixtures):
        raise ValueError("a RPC não devolveu nenhuma linha para a amostra inteira: nada a gravar.")
    return {"capturado_em": agora.isoformat(), "rpc": "get_futebol_fixture_quotes", "fixtures": fixtures}


def recaptura(conn, antes: dict, agora: datetime) -> dict:
    """Reconsulta a RPC para os MESMOS fixture_id do arquivo (não reamostra)."""
    _prepara(conn)
    return {
        "capturado_em": agora.isoformat(),
        "rpc": antes["rpc"],
        "fixtures": [
            {**{k: f[k] for k in ("fixture_id", "kickoff_utc", "grupo")},
             "linhas": _chama_rpc(conn, f["fixture_id"])}
            for f in antes["fixtures"]
        ],
    }


@dataclass
class Relatorio:
    falhas: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    por_grupo: dict = field(default_factory=dict)


def _iguais(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=TOLERANCIA_RELATIVA, abs_tol=0.0)
    return a == b


def _por_chave(linhas: list[dict]) -> dict:
    return {(l["market_key"], l["outcome_label"]): l for l in linhas}


def compara(antes: dict, depois: dict) -> Relatorio:
    r = Relatorio()
    depois_por_id = {f["fixture_id"]: f for f in depois["fixtures"]}
    for f in antes["fixtures"]:
        fid, grupo = f["fixture_id"], f["grupo"]
        stats = r.por_grupo.setdefault(grupo, {"fixtures": 0, "iguais": 0})
        stats["fixtures"] += 1
        a, d = _por_chave(f["linhas"]), _por_chave(depois_por_id[fid]["linhas"])
        sumiram = sorted(set(a) - set(d))
        novas = sorted(set(d) - set(a))
        mudaram = {}
        for k in sorted(set(a) & set(d)):
            cols = [c for c in a[k] if not _iguais(a[k][c], d[k].get(c))]
            if cols:
                mudaram[k] = cols
        if not (sumiram or novas or mudaram):
            stats["iguais"] += 1
            continue
        rotulo = f"fixture {fid} ({grupo})"
        if sumiram:
            r.falhas.append(f"{rotulo}: {len(sumiram)} linha(s) sumiu/sumiram: {sumiram[:3]}")
        destino = r.falhas if grupo == "recente" else r.avisos
        if novas:
            destino.append(f"{rotulo}: {len(novas)} linha(s) nova(s): {novas[:3]}")
        if mudaram:
            cols = sorted({c for v in mudaram.values() for c in v})
            destino.append(f"{rotulo}: {len(mudaram)} linha(s) com valor diferente em {cols}")
    return r


def _texto_do_relatorio(r: Relatorio) -> str:
    linhas = [f"  {g}: {s['iguais']}/{s['fixtures']} fixtures idênticas" for g, s in r.por_grupo.items()]
    linhas += [f"  FALHA: {f}" for f in r.falhas]
    linhas += [f"  aviso: {a}" for a in r.avisos]
    linhas.append("VERMELHO: a RPC mudou onde não podia." if r.falhas else "VERDE: nenhuma falha.")
    return "\n".join(linhas)


def executa(modo: str, caminho: str, conn, n_por_grupo: int, agora: datetime) -> tuple[int, str]:
    """(código de saída, texto). 0 = ok/verde; 1 = diff vermelho. Erros levantam."""
    destino = Path(caminho)
    if modo == "captura":
        if destino.exists():
            raise FileExistsError(f"{destino} já existe: a linha de base nunca é sobrescrita")
        arq = captura(conn, n_por_grupo, agora)
        destino.write_text(json.dumps(arq, ensure_ascii=False, indent=1), encoding="utf-8")
        total = sum(len(f["linhas"]) for f in arq["fixtures"])
        return 0, f"GRAVADO {destino}: {len(arq['fixtures'])} fixtures, {total} linhas da RPC."
    if modo == "diff":
        antes = json.loads(destino.read_text(encoding="utf-8"))
        depois = recaptura(conn, antes, agora)
        r = compara(antes, depois)
        return (1 if r.falhas else 0), _texto_do_relatorio(r)
    raise ValueError(f"modo desconhecido: {modo!r} (use 'captura' ou 'diff')")
