"""A carga por troca contra um Postgres DE VERDADE (DE#108, ADR 0005). Pulado por padrão.

`tests/test_sync_troca.py` prova a orquestração com fakes; este prova o que os fakes não
conseguem: que o leitor concorrente NÃO fica bloqueado durante a carga (só durante o RENAME), que
o teto de espera derruba a tentativa e a seguinte conclui, que o rollback deixa a vigente
intacta, e que dono, permissões, RLS, políticas, comentário e índices voltam idênticos.

COMO RODAR (Postgres descartável; NUNCA aponte para PRD/DEV: o teste cria schema, papéis e
tabelas no cluster apontado):

    docker run -d --rm --name pg-troca-108 -p 55433:5432 -e POSTGRES_PASSWORD=teste postgres:17
    SYNC_TESTE_PG_URL=postgresql://postgres:teste@localhost:55433/postgres \\
        .venv/bin/python3 -m pytest tests/test_sync_troca_integracao.py -v
    docker stop pg-troca-108

O usuário do URL precisa ser superusuário (o teste cria papéis). Recusa URL que não seja
localhost.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

URL = os.getenv("SYNC_TESTE_PG_URL")

pytestmark = pytest.mark.skipif(
    not URL, reason="defina SYNC_TESTE_PG_URL (Postgres local descartável) para rodar"
)

if URL:
    assert urlparse(URL).hostname in ("localhost", "127.0.0.1"), (
        "SYNC_TESTE_PG_URL tem de ser localhost: o teste faz DDL e cria papéis"
    )

import psycopg  # noqa: E402

from src.sync import troca  # noqa: E402

S = "troca_t108"
DONO = "t108_dono"
LEITOR = "t108_leitor"
INDICE_COMPRIDO = "idx_jogos_valor_positivo_" + "x" * 38  # 62 caracteres (limite do Postgres: 63)
AGORA = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)

assert 59 <= len(INDICE_COMPRIDO) <= 63


def _exec(sql):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(sql)


def _um(sql, params=None):
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute(sql, params).fetchone()[0]


@pytest.fixture
def banco():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")
        for papel in (DONO, LEITOR):
            c.execute(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{papel}') "
                f"THEN CREATE ROLE {papel} NOLOGIN; END IF; END $$"
            )
        c.execute(f"CREATE SCHEMA {S}")
        c.execute(f"GRANT USAGE ON SCHEMA {S} TO {LEITOR}, {DONO}")
        c.execute(f"GRANT CREATE ON SCHEMA {S} TO {DONO}")
        # o estado de sincronização que o sync mantém por schema
        c.execute(
            f'CREATE TABLE {S}."_sync_state" (table_name text PRIMARY KEY, '
            f"last_synced_bq_modified_time timestamptz NOT NULL, "
            f"last_synced_at timestamptz NOT NULL DEFAULT now())"
        )
    yield
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {S} CASCADE")


def _cria_jogos(com_dono_e_rls=False, nome="jogos"):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(
            f"""CREATE TABLE {S}.{nome} (
                id int PRIMARY KEY,
                nome text NOT NULL DEFAULT 'x',
                valor numeric(10,2),
                CONSTRAINT {nome}_valor_pos CHECK (valor >= 0),
                CONSTRAINT {nome}_nome_unico UNIQUE (nome))"""
        )
        c.execute(f"CREATE INDEX {INDICE_COMPRIDO} ON {S}.{nome} (valor) WHERE valor > 5")
        c.execute(f"CREATE INDEX {nome}_idx_expr ON {S}.{nome} (lower(nome))")
        c.execute(f"INSERT INTO {S}.{nome} VALUES (1,'velho-a',1.0),(2,'velho-b',10.0)")
        if com_dono_e_rls:
            c.execute(f"COMMENT ON TABLE {S}.{nome} IS 'tabela de jogos do app'")
            c.execute(f"COMMENT ON COLUMN {S}.{nome}.valor IS 'valor da aposta'")
            c.execute(f"ALTER TABLE {S}.{nome} OWNER TO {DONO}")
            c.execute(f"GRANT SELECT ON {S}.{nome} TO {LEITOR}")
            c.execute(f"GRANT UPDATE ON {S}.{nome} TO PUBLIC")
            c.execute(f"ALTER TABLE {S}.{nome} ENABLE ROW LEVEL SECURITY")
            c.execute(f"ALTER TABLE {S}.{nome} FORCE ROW LEVEL SECURITY")
            c.execute(f"CREATE POLICY ve_positivos ON {S}.{nome} FOR SELECT TO {LEITOR} USING (valor > 0)")
            c.execute(
                f"CREATE POLICY nao_apaga ON {S}.{nome} AS RESTRICTIVE FOR DELETE TO {LEITOR} USING (false)"
            )


def _copiar(linhas, parado=None, liberar=None, parar_na=1):
    """`copiar` do contrato de troca.carrega_por_troca: escreve `linhas` (id, nome, valor)."""

    def copiar(cur, alvo):
        n = 0
        with cur.copy(f"COPY {alvo} (id, nome, valor) FROM STDIN") as cp:
            for i, linha in enumerate(linhas):
                if parado is not None and i == parar_na:
                    parado.set()
                    assert liberar.wait(timeout=30), "teste travou: ninguém liberou o COPY"
                cp.write_row(linha)
                n += 1
        return n

    return copiar


def _estado(cur):
    cur.execute(f"INSERT INTO {S}._sync_state (table_name, last_synced_bq_modified_time) "
                f"VALUES ('jogos', %s) ON CONFLICT (table_name) DO UPDATE "
                f"SET last_synced_bq_modified_time = EXCLUDED.last_synced_bq_modified_time", (AGORA,))


def _ctx(**kw):
    cfg = troca.ConfigTroca(
        teto_espera_ms=kw.pop("teto_espera_ms", 400),
        tentativas=kw.pop("tentativas", 3),
        pausa_min_s=0.0,
        pausa_max_s=0.0,
        orcamento_s=kw.pop("orcamento_s", 300.0),
    )
    return troca.ContextoTroca(cfg, **kw)


def _vigente():
    return _um(f"SELECT count(*) FROM {S}.jogos")


def _nomes_dos_objetos():
    with psycopg.connect(URL, autocommit=True) as c:
        idx = c.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=%s AND tablename='jogos' ORDER BY 1", (S,)
        ).fetchall()
        con = c.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY 1", (f"{S}.jogos",)
        ).fetchall()
        tabelas = c.execute(
            "SELECT relname FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY 1", (S,)
        ).fetchall()
    return [i[0] for i in idx], [x[0] for x in con], [t[0] for t in tabelas]


def test_a_troca_entrega_o_dado_novo_avanca_o_estado_e_nao_deixa_sombra(banco):
    _cria_jogos()
    ctx = _ctx()
    with psycopg.connect(URL) as conn:
        r = troca.carrega_por_troca(
            conn, S, "jogos", ctx, copiar=_copiar([(7, "novo", 3.0), (8, "novo2", 4.0)]),
            atualiza_estado=_estado,
        )
    assert r["rows"] == 2
    assert r["tentativas"] == 1
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [7, 8]
    assert _um(f"SELECT count(*) FROM {S}._sync_state WHERE table_name='jogos'") == 1
    _, _, tabelas = _nomes_dos_objetos()
    assert tabelas == ["_sync_state", "jogos"]


def test_o_leitor_nao_fica_bloqueado_durante_a_carga_e_ve_o_dado_antigo(banco):
    _cria_jogos()
    parado, liberar = threading.Event(), threading.Event()
    ctx = _ctx()

    def roda():
        with psycopg.connect(URL) as conn:
            troca.carrega_por_troca(
                conn, S, "jogos", ctx,
                copiar=_copiar([(7, "novo", 3.0), (8, "novo2", 4.0)], parado, liberar),
                atualiza_estado=_estado,
            )

    t = threading.Thread(target=roda)
    t.start()
    try:
        assert parado.wait(timeout=30), "a carga não chegou ao meio do COPY"
        with psycopg.connect(URL, autocommit=True) as leitor:
            leitor.execute("SET statement_timeout = '1500ms'")
            leitor.execute("SET lock_timeout = '1500ms'")
            inicio = time.monotonic()
            # Na carga no lugar (TRUNCATE + COPY) esta leitura esperaria o COPY inteiro e seria
            # cancelada pelos 1,5 s; aqui devolve o dado ANTIGO na hora.
            ids = leitor.execute(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos").fetchone()[0]
            assert time.monotonic() - inicio < 1.0
        assert ids == [1, 2]
    finally:
        liberar.set()
        t.join(timeout=30)
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [7, 8]


def test_a_carga_no_lugar_continua_bloqueando_o_leitor_e_por_isso_a_troca_existe(banco):
    """Contraprova do teste anterior: documenta o comportamento que a troca elimina."""
    _cria_jogos()
    segura = threading.Event()
    solta = threading.Event()

    def trunca():
        with psycopg.connect(URL) as c:
            c.execute(f"TRUNCATE {S}.jogos")
            segura.set()
            solta.wait(timeout=30)
            c.rollback()

    t = threading.Thread(target=trunca)
    t.start()
    try:
        assert segura.wait(timeout=30)
        with psycopg.connect(URL, autocommit=True) as leitor:
            leitor.execute("SET lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                leitor.execute(f"SELECT count(*) FROM {S}.jogos")
    finally:
        solta.set()
        t.join(timeout=30)


def test_leitor_com_plano_em_cache_passa_a_ver_a_tabela_nova_sem_erro(banco):
    _cria_jogos()
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(
            f"CREATE FUNCTION {S}.soma_sql() RETURNS bigint LANGUAGE sql STABLE AS "
            f"$$ SELECT coalesce(sum(id),0) FROM {S}.jogos $$"
        )
        c.execute(
            f"CREATE FUNCTION {S}.soma_plpgsql() RETURNS bigint LANGUAGE plpgsql STABLE AS "
            f"$$ BEGIN RETURN (SELECT coalesce(sum(id),0) FROM {S}.jogos); END $$"
        )
    leitor = psycopg.connect(URL, autocommit=True)
    try:
        for _ in range(8):  # passa do limiar em que o plpgsql guarda o plano genérico
            assert leitor.execute(f"SELECT {S}.soma_plpgsql(), {S}.soma_sql()").fetchone() == (3, 3)
        with psycopg.connect(URL) as conn:
            troca.carrega_por_troca(
                conn, S, "jogos", _ctx(), copiar=_copiar([(10, "n", 1.0), (20, "m", 2.0)]),
                atualiza_estado=_estado,
            )
        assert leitor.execute(f"SELECT {S}.soma_plpgsql(), {S}.soma_sql()").fetchone() == (30, 30)
    finally:
        leitor.close()


def test_fidelidade_dono_permissoes_rls_politicas_comentarios_e_indices_voltam_identicos(banco):
    _cria_jogos(com_dono_e_rls=True)
    consultas = {
        "dono": f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='{S}.jogos'::regclass",
        "acl": (
            f"SELECT array_agg(a::text ORDER BY a::text) FROM pg_class c, "
            f"LATERAL unnest(coalesce(c.relacl, acldefault('r', c.relowner))) a "
            f"WHERE c.oid='{S}.jogos'::regclass"
        ),
        "rls": f"SELECT relrowsecurity::text||relforcerowsecurity::text FROM pg_class WHERE oid='{S}.jogos'::regclass",
        "politicas": (
            f"SELECT array_agg(policyname||permissive||roles::text||cmd||coalesce(qual,'')||coalesce(with_check,'') "
            f"ORDER BY policyname) FROM pg_policies WHERE schemaname='{S}' AND tablename='jogos'"
        ),
        "comentario_tabela": f"SELECT obj_description('{S}.jogos'::regclass)",
        "comentario_coluna": f"SELECT col_description('{S}.jogos'::regclass, 3)",
        "indices": (
            f"SELECT array_agg(indexname||'|'||indexdef ORDER BY indexname) FROM pg_indexes "
            f"WHERE schemaname='{S}' AND tablename='jogos'"
        ),
        "constraints": (
            f"SELECT array_agg(conname||contype::text||pg_get_constraintdef(oid) ORDER BY conname) "
            f"FROM pg_constraint WHERE conrelid='{S}.jogos'::regclass"
        ),
    }
    antes = {k: _um(q) for k, q in consultas.items()}
    assert antes["comentario_tabela"] == "tabela de jogos do app"
    assert any(INDICE_COMPRIDO in x for x in antes["indices"])

    with psycopg.connect(URL) as conn:
        troca.carrega_por_troca(
            conn, S, "jogos", _ctx(), copiar=_copiar([(7, "novo", 3.0), (8, "n2", 6.0)]),
            atualiza_estado=_estado,
        )
    depois = {k: _um(q) for k, q in consultas.items()}
    assert depois == antes
    # nenhum nome provisório sobrou
    idx, con, tabelas = _nomes_dos_objetos()
    assert not any("__n" in n for n in idx + con)
    assert tabelas == ["_sync_state", "jogos"]


def test_tentativa_com_leitor_longo_falha_pelo_teto_e_a_seguinte_conclui(banco):
    _cria_jogos()
    leitor = psycopg.connect(URL)  # transação aberta com AccessShareLock = leitor longo
    leitor.execute(f"SELECT count(*) FROM {S}.jogos")
    pausas = []

    def pausa(s):
        pausas.append(s)
        leitor.rollback()  # o leitor termina durante a pausa

    ctx = _ctx(teto_espera_ms=300, tentativas=3, pausa=pausa)
    inicio = time.monotonic()
    with psycopg.connect(URL) as conn:
        r = troca.carrega_por_troca(
            conn, S, "jogos", ctx, copiar=_copiar([(7, "novo", 3.0)]), atualiza_estado=_estado
        )
    leitor.close()
    assert r["tentativas"] == 2
    assert len(pausas) == 1
    assert time.monotonic() - inicio < 10
    assert _um(f"SELECT array_agg(id) FROM {S}.jogos") == [7]


def test_leitor_que_chega_durante_a_espera_fica_bloqueado_no_maximo_o_teto(banco):
    """Linha do tempo: a sombra é commitada; a 1ª tentativa de RENAME entra na fila atrás do
    leitor longo (`segurando`); um leitor NOVO chega e fica na fila atrás do RENAME pendente; o
    RENAME estoura o teto e desiste; o leitor novo segue contra a tabela ANTIGA."""
    _cria_jogos()
    segurando = psycopg.connect(URL)
    segurando.execute(f"SELECT count(*) FROM {S}.jogos")
    medido = {}

    def novo_leitor():
        time.sleep(0.3)  # o RENAME já está na fila atrás do leitor longo (teto 800 ms)
        with psycopg.connect(URL, autocommit=True) as c:
            inicio = time.monotonic()
            medido["linhas"] = c.execute(f"SELECT count(*) FROM {S}.jogos").fetchone()[0]
            medido["espera"] = time.monotonic() - inicio

    threads = []

    def copiar_e_dispara_o_leitor(cur, alvo):
        n = _copiar([(7, "novo", 3.0)])(cur, alvo)
        # a carga termina e logo vem o RENAME: o leitor é lançado agora, para chegar DURANTE ele
        t = threading.Thread(target=novo_leitor)
        t.start()
        threads.append(t)
        return n

    def pausa(_):
        segurando.rollback()  # o leitor longo só termina depois da 1ª tentativa ter falhado

    ctx = _ctx(teto_espera_ms=800, tentativas=2, pausa=pausa)
    with psycopg.connect(URL) as conn:
        r = troca.carrega_por_troca(
            conn, S, "jogos", ctx, copiar=copiar_e_dispara_o_leitor, atualiza_estado=_estado
        )
    threads[0].join(timeout=30)
    segurando.close()
    assert r["tentativas"] == 2  # a 1ª tentativa estourou o teto: o RENAME esteve mesmo na fila
    assert medido["linhas"] == 2  # o leitor que esperou viu a tabela ANTIGA
    assert 0.2 < medido["espera"] < 0.8 + 1.5  # esperou atrás do RENAME, mas no máximo o teto


def test_esgotar_as_tentativas_levanta_remove_a_sombra_e_deixa_a_vigente_intacta(banco):
    _cria_jogos()
    leitor = psycopg.connect(URL)
    leitor.execute(f"SELECT count(*) FROM {S}.jogos")
    ctx = _ctx(teto_espera_ms=200, tentativas=2)
    try:
        with psycopg.connect(URL) as conn:
            with pytest.raises(troca.TrocaFalhou) as e:
                troca.carrega_por_troca(
                    conn, S, "jogos", ctx, copiar=_copiar([(7, "novo", 3.0)]), atualiza_estado=_estado
                )
        assert e.value.table == "jogos"
        assert e.value.motivo == troca.MOTIVO_LOCK_TIMEOUT
    finally:
        leitor.rollback()
        leitor.close()
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [1, 2]
    assert _um(f"SELECT count(*) FROM {S}._sync_state WHERE table_name='jogos'") == 0
    _, _, tabelas = _nomes_dos_objetos()
    assert tabelas == ["_sync_state", "jogos"]


def test_view_criada_entre_a_checagem_e_o_drop_faz_a_troca_reverter(banco):
    _cria_jogos()

    def copiar_e_cria_view(cur, alvo):
        n = _copiar([(7, "novo", 3.0)])(cur, alvo)
        with psycopg.connect(URL, autocommit=True) as c:  # o app cria a view durante a carga
            c.execute(f"CREATE VIEW {S}.vw_jogos AS SELECT * FROM {S}.jogos")
        return n

    with psycopg.connect(URL) as conn:
        with pytest.raises(troca.TrocaFalhou) as e:
            troca.carrega_por_troca(
                conn, S, "jogos", _ctx(), copiar=copiar_e_cria_view, atualiza_estado=_estado
            )
    assert e.value.motivo == troca.MOTIVO_DEPENDENTE_NOVO
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [1, 2]
    assert _um(f"SELECT count(*) FROM {S}.vw_jogos") == 2
    _, _, tabelas = _nomes_dos_objetos()
    assert tabelas == ["_sync_state", "jogos"]
    # o ciclo seguinte já enxerga a view como dependente
    with psycopg.connect(URL) as conn:
        assert troca.dependentes(conn, S, "jogos")


def test_coluna_adicionada_por_migration_durante_a_carga_aborta_a_troca(banco):
    _cria_jogos()

    def copiar_e_migra(cur, alvo):
        n = _copiar([(7, "novo", 3.0)])(cur, alvo)
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f"ALTER TABLE {S}.jogos ADD COLUMN extra int")
        return n

    with psycopg.connect(URL) as conn:
        with pytest.raises(troca.TrocaFalhou) as e:
            troca.carrega_por_troca(
                conn, S, "jogos", _ctx(), copiar=copiar_e_migra, atualiza_estado=_estado
            )
    assert e.value.motivo == troca.MOTIVO_FORMATO_DIVERGENTE
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [1, 2]
    assert _um(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema=%s AND table_name='jogos' "
        "AND column_name='extra'", (S,)
    ) == 1
    _, _, tabelas = _nomes_dos_objetos()
    assert tabelas == ["_sync_state", "jogos"]


def test_migration_do_app_durante_a_carga_nao_fica_na_fila_atras_da_carga(banco):
    """A criação da sombra (LIKE) não pode segurar ACCESS SHARE na vigente durante o COPY: uma
    migration (ALTER, ACCESS EXCLUSIVE) ficaria na fila e, atrás dela, todos os leitores."""
    _cria_jogos()
    resultado = {}

    def copiar_e_migra(cur, alvo):
        n = _copiar([(7, "novo", 3.0)])(cur, alvo)
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute("SET lock_timeout = '1000ms'")
            try:
                c.execute(f"COMMENT ON TABLE {S}.jogos IS 'migration durante a carga'")
                resultado["migration"] = "ok"
            except psycopg.errors.LockNotAvailable:
                resultado["migration"] = "bloqueada"
        return n

    with psycopg.connect(URL) as conn:
        troca.carrega_por_troca(
            conn, S, "jogos", _ctx(), copiar=copiar_e_migra, atualiza_estado=_estado
        )
    assert resultado["migration"] == "ok"
    # o comentário é relido da vigente SOB LOCK: a migration não se perde na troca
    assert _um(f"SELECT obj_description('{S}.jogos'::regclass)") == "migration durante a carga"


def test_staged_migration_do_app_durante_a_carga_nao_fica_na_fila(banco):
    _cria_jogos()
    resultado = {}

    def copiar_e_migra(cur, alvo):
        n = _copiar([(7, "novo", 3.0)])(cur, alvo)
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute("SET lock_timeout = '1000ms'")
            try:
                c.execute(f"ALTER TABLE {S}.jogos ADD COLUMN extra int")
                resultado["migration"] = "ok"
            except psycopg.errors.LockNotAvailable:
                resultado["migration"] = "bloqueada"
        return n

    with psycopg.connect(URL) as conn:
        troca.carrega_staged(conn, S, "jogos", _ctx(), copiar=copiar_e_migra, atualiza_estado=_estado)
    assert resultado["migration"] == "ok"
    assert _um(f"SELECT array_agg(id) FROM {S}.jogos") == [7]


def test_privilegios_padrao_do_schema_nao_vazam_para_a_sombra(banco):
    """No Supabase o ALTER DEFAULT PRIVILEGES dá ACL à tabela nova; a vigente (criada antes, com a
    ACL padrão do dono) não a tem. A troca não pode abrir a tabela ao público por causa disso."""
    _cria_jogos()  # ACL nula: só o dono
    _exec(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {S} GRANT ALL ON TABLES TO {LEITOR}")
    _exec(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {S} GRANT SELECT ON TABLES TO PUBLIC")
    antes = _um(f"SELECT relacl::text FROM pg_class WHERE oid='{S}.jogos'::regclass")
    assert antes is None
    with psycopg.connect(URL) as conn:
        troca.carrega_por_troca(
            conn, S, "jogos", _ctx(), copiar=_copiar([(7, "novo", 3.0)]), atualiza_estado=_estado
        )
    # ACL efetiva idêntica (só o dono); o Postgres guarda `{postgres=arwdDxtm/postgres}` em vez de
    # NULL depois do REVOKE/GRANT, o que é equivalente
    assert _um(
        f"SELECT array_agg(a::text) FROM pg_class c, LATERAL unnest(coalesce(c.relacl, "
        f"acldefault('r', c.relowner))) a WHERE c.oid='{S}.jogos'::regclass"
    ) == ["postgres=arwdDxtm/postgres"]
    assert _um(f"SELECT has_table_privilege('{LEITOR}', '{S}.jogos', 'SELECT')") is False
    assert _um(f"SELECT has_table_privilege('public', '{S}.jogos', 'SELECT')") is False
    _exec(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {S} REVOKE ALL ON TABLES FROM {LEITOR}")
    _exec(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {S} REVOKE SELECT ON TABLES FROM PUBLIC")


def test_view_sobre_a_tabela_aparece_como_dependente_e_tabela_limpa_nao(banco):
    _cria_jogos()
    with psycopg.connect(URL) as conn:
        assert troca.dependentes(conn, S, "jogos") == []
    _exec(f"CREATE VIEW {S}.vw AS SELECT id FROM {S}.jogos")
    with psycopg.connect(URL) as conn:
        motivos = troca.dependentes(conn, S, "jogos")
    assert motivos and "vw" in " ".join(motivos)


DDLS_DE_DEPENDENTE = [
    "CREATE FUNCTION {S}.f(x {S}.jogos) RETURNS int LANGUAGE sql AS $$ SELECT 1 $$",
    "CREATE TABLE {S}.filha (j int REFERENCES {S}.jogos(id))",
    "CREATE FUNCTION {S}.tg() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; "
    "CREATE TRIGGER tg BEFORE INSERT ON {S}.jogos FOR EACH ROW EXECUTE FUNCTION {S}.tg()",
    "CREATE SEQUENCE {S}.sq OWNED BY {S}.jogos.id",
    "GRANT SELECT (valor) ON {S}.jogos TO " + LEITOR,
    # Os dois que a lista enumerada não via (achado da revisão do PR #117): a checagem é por OID
    # e genérica, não uma lista de tipos de objeto.
    "CREATE TABLE {S}.outra (id int); ALTER TABLE {S}.outra ENABLE ROW LEVEL SECURITY; "
    "CREATE POLICY p ON {S}.outra USING (id IN (SELECT id FROM {S}.jogos))",
    "CREATE FUNCTION {S}.fa() RETURNS bigint LANGUAGE sql BEGIN ATOMIC "
    "SELECT count(*) FROM {S}.jogos; END",
    # a troca renomearia a estatística (o LIKE a recria com nome gerado): conservador, vai no fallback
    "CREATE STATISTICS {S}.est_jogos ON id, valor FROM {S}.jogos",
]
IDS_DE_DEPENDENTE = [
    "funcao_com_o_tipo", "fk", "trigger", "sequencia", "acl_por_coluna",
    "policy_de_outra_tabela", "funcao_begin_atomic",
    "estatistica_estendida",
]


@pytest.mark.parametrize("ddl", DDLS_DE_DEPENDENTE, ids=IDS_DE_DEPENDENTE)
def test_outros_dependentes_tambem_mandam_a_tabela_para_a_carga_no_lugar(banco, ddl):
    _cria_jogos()
    _exec(ddl.format(S=S))
    with psycopg.connect(URL) as conn:
        assert troca.dependentes(conn, S, "jogos")


@pytest.mark.parametrize("ddl", DDLS_DE_DEPENDENTE, ids=IDS_DE_DEPENDENTE)
def test_tabela_com_qualquer_dependente_cai_no_fallback_e_nunca_chega_a_falhar_a_troca(banco, ddl):
    """História 19: a tabela com dependente NUNCA usa a troca (o DROP da velha falharia toda
    hora). O modo é decidido pela checagem, antes de carregar a sombra."""
    _cria_jogos()
    _exec(ddl.format(S=S))
    with psycopg.connect(URL) as conn:
        modo, motivo = troca.escolhe_modo(conn, S, "jogos", _ctx(troca=["jogos"]))
    assert modo == troca.MODO_FALLBACK
    assert motivo


def test_o_que_pertence_a_propria_tabela_nao_conta_como_dependente(banco):
    """Índices, constraints, defaults, políticas e comentários da própria tabela são recriados
    ou reaplicados pela troca: a checagem genérica não pode barrá-los."""
    _cria_jogos(com_dono_e_rls=True)
    _exec(f"ALTER TABLE {S}.jogos ADD COLUMN gerada int GENERATED ALWAYS AS (id * 2) STORED")
    _exec(f"ALTER TABLE {S}.jogos ADD COLUMN texto text")  # ganha toast
    with psycopg.connect(URL) as conn:
        assert troca.dependentes(conn, S, "jogos") == []


def test_sombras_orfas_sao_removidas_e_a_com_mais_de_2h_gera_aviso(banco):
    _cria_jogos()
    velha = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    nova = datetime.now(timezone.utc).isoformat()
    _exec(f"CREATE TABLE {S}.a__new (x int)")
    _exec(f"COMMENT ON TABLE {S}.a__new IS '{troca.MARCADOR_SOMBRA}{velha}'")
    _exec(f"CREATE TABLE {S}.b__new (x int)")
    _exec(f"COMMENT ON TABLE {S}.b__new IS '{troca.MARCADOR_SOMBRA}{nova}'")
    _exec(f"CREATE TABLE {S}.c__old (x int)")
    with psycopg.connect(URL) as conn:
        avisos = troca.limpa_sombras(conn, S, _ctx())
    _, _, tabelas = _nomes_dos_objetos()
    assert tabelas == ["_sync_state", "jogos"]
    assert len(avisos) == 1 and "a__new" in avisos[0]


def test_o_aviso_de_sombra_orfa_com_mais_de_2h_tambem_vai_para_o_log(banco, caplog):
    """O retorno de `run_sync` morre no corpo HTTP (o workflow o descarta): o texto do aviso tem
    de estar também no log do Cloud Run, onde dá para achá-lo sem o corpo."""
    _cria_jogos()
    velha = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _exec(f"CREATE TABLE {S}.a__new (x int)")
    _exec(f"COMMENT ON TABLE {S}.a__new IS '{troca.MARCADOR_SOMBRA}{velha}'")
    with psycopg.connect(URL) as conn:
        avisos = troca.limpa_sombras(conn, S, _ctx())
    assert len(avisos) == 1
    assert any(avisos[0] in m for m in caplog.messages)


def test_limpeza_nao_toca_em_tabela_que_nao_e_sombra(banco):
    _cria_jogos()
    _exec(f"CREATE TABLE {S}.dados_new (x int)")  # um único underscore: não é sombra
    with psycopg.connect(URL) as conn:
        troca.limpa_sombras(conn, S, _ctx())
    _, _, tabelas = _nomes_dos_objetos()
    assert "dados_new" in tabelas


def test_staged_carrega_fora_do_lock_e_troca_o_conteudo_em_transacao_curta(banco):
    _cria_jogos()
    _exec(f"CREATE VIEW {S}.vw AS SELECT id FROM {S}.jogos")  # dependente: a troca não serve
    parado, liberar = threading.Event(), threading.Event()

    def roda():
        with psycopg.connect(URL) as conn:
            r = troca.carrega_staged(
                conn, S, "jogos", _ctx(),
                copiar=_copiar([(7, "novo", 3.0), (8, "n2", 4.0)], parado, liberar),
                atualiza_estado=_estado,
            )
            assert r["rows"] == 2

    t = threading.Thread(target=roda)
    t.start()
    try:
        assert parado.wait(timeout=30)
        with psycopg.connect(URL, autocommit=True) as leitor:
            leitor.execute("SET lock_timeout = '1000ms'")
            assert leitor.execute(f"SELECT count(*) FROM {S}.jogos").fetchone()[0] == 2
            assert leitor.execute(f"SELECT count(*) FROM {S}.vw").fetchone()[0] == 2
    finally:
        liberar.set()
        t.join(timeout=30)
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [7, 8]
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.vw") == [7, 8]
    assert _um(f"SELECT count(*) FROM {S}._sync_state WHERE table_name='jogos'") == 1


def test_staged_com_leitor_longo_esgota_as_tentativas_e_nao_avanca_o_estado(banco):
    _cria_jogos()
    leitor = psycopg.connect(URL)
    leitor.execute(f"SELECT count(*) FROM {S}.jogos")
    try:
        with psycopg.connect(URL) as conn:
            with pytest.raises(troca.TrocaFalhou) as e:
                troca.carrega_staged(
                    conn, S, "jogos", _ctx(teto_espera_ms=200, tentativas=2),
                    copiar=_copiar([(7, "novo", 3.0)]), atualiza_estado=_estado,
                )
        assert e.value.motivo == troca.MOTIVO_LOCK_TIMEOUT
    finally:
        leitor.rollback()
        leitor.close()
    assert _um(f"SELECT array_agg(id ORDER BY id) FROM {S}.jogos") == [1, 2]
    assert _um(f"SELECT count(*) FROM {S}._sync_state") == 0


def test_orcamento_de_retentativas_esgotado_falha_na_hora_sem_carregar_a_sombra(banco):
    _cria_jogos()
    ctx = _ctx(orcamento_s=10.0)
    ctx.orcamento_usado_s = 10.0
    chamou = []

    def copiar(cur, alvo):
        chamou.append(1)
        return 0

    with psycopg.connect(URL) as conn:
        with pytest.raises(troca.TrocaFalhou) as e:
            troca.carrega_por_troca(conn, S, "jogos", ctx, copiar=copiar, atualiza_estado=_estado)
    assert e.value.motivo == troca.MOTIVO_ORCAMENTO_ESGOTADO
    assert chamou == []
    _, _, tabelas = _nomes_dos_objetos()
    assert tabelas == ["_sync_state", "jogos"]


# ============================================================
# run_sync de ponta a ponta (BigQuery falso, Postgres real)
# ============================================================
from src.sync import bq_to_postgres as sync  # noqa: E402

TAB_A, TAB_B = "dim_leagues", "dim_teams"  # as duas primeiras da allowlist do futebol


class _Campo:
    def __init__(self, name):
        self.name, self.mode, self.field_type = name, "NULLABLE", "STRING"


class _LinhasDoBq:
    def __init__(self, valores):
        self.schema = [_Campo("a")]
        self.table = type("T", (), {"modified": AGORA})()
        self._valores = valores

    def __iter__(self):
        for v in self._valores:
            yield {"a": v}


@pytest.fixture
def run_banco(monkeypatch):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS futebol")
        for t in (TAB_A, TAB_B):
            c.execute(f'DROP TABLE IF EXISTS futebol."{t}" CASCADE')
            c.execute(f'CREATE TABLE futebol."{t}" (a text)')
            c.execute(f"INSERT INTO futebol.\"{t}\" VALUES ('velho')")
        c.execute('DROP TABLE IF EXISTS futebol."_sync_state"')

    class _Bq:
        def list_rows(self, ref):
            return _LinhasDoBq(["novo-1", "novo-2"])

    monkeypatch.setattr(sync, "get_pg_url", lambda env: URL)
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: _Bq())
    monkeypatch.setattr(sync, "check_schema_parity", lambda *a, **kw: [])
    yield
    with psycopg.connect(URL, autocommit=True) as c:
        for t in (TAB_A, TAB_B):
            c.execute(f'DROP TABLE IF EXISTS futebol."{t}" CASCADE')
        c.execute('DROP TABLE IF EXISTS futebol."_sync_state"')


def _valores(tabela):
    return _um(f'SELECT array_agg(a ORDER BY a) FROM futebol."{tabela}"')


def test_run_sync_so_a_tabela_habilitada_usa_a_troca_e_o_modo_e_ecoado(run_banco):
    r = sync.run_sync(tables=f"{TAB_A},{TAB_B}", env="prd", sport="futebol", troca=TAB_A)
    assert r["status"] == "success"
    modos = {x["table"]: x["modo"] for x in r["synced"]}
    assert modos == {TAB_A: "troca", TAB_B: "no_lugar"}
    assert _valores(TAB_A) == ["novo-1", "novo-2"] and _valores(TAB_B) == ["novo-1", "novo-2"]
    troca_item = next(x for x in r["synced"] if x["table"] == TAB_A)
    assert troca_item["tentativas"] == 1 and troca_item["troca_ms"] >= 0 and "duracao_s" in troca_item


def test_run_sync_sem_troca_habilitada_o_caminho_e_o_de_sempre(run_banco):
    r = sync.run_sync(tables=TAB_A, env="prd", sport="futebol")
    assert [x["modo"] for x in r["synced"]] == ["no_lugar"]
    assert r["falhas"] == []


def test_run_sync_tabela_com_dependente_cai_no_lugar_com_aviso_e_carrega_o_mesmo_dado(run_banco, caplog):
    _exec(f'CREATE VIEW futebol.vw_a AS SELECT a FROM futebol."{TAB_A}"')
    r = sync.run_sync(tables=TAB_A, env="prd", sport="futebol", troca=TAB_A)
    item = r["synced"][0]
    assert item["modo"] == "no_lugar_fallback"
    assert "vw_a" in item["fallback_motivo"]
    assert r["summary"]["fallback"] == 1
    assert _valores(TAB_A) == ["novo-1", "novo-2"]
    assert any("dependente" in m for m in caplog.messages)


def test_run_sync_staged_habilitado_serve_a_tabela_com_dependente(run_banco):
    _exec(f'CREATE VIEW futebol.vw_a AS SELECT a FROM futebol."{TAB_A}"')
    r = sync.run_sync(tables=TAB_A, env="prd", sport="futebol", staged=TAB_A)
    assert r["synced"][0]["modo"] == "staged"
    assert _um("SELECT array_agg(a ORDER BY a) FROM futebol.vw_a") == ["novo-1", "novo-2"]


def test_run_sync_falha_de_troca_numa_tabela_nao_derruba_a_seguinte_e_nao_avanca_o_estado(
    run_banco, monkeypatch
):
    leitor = psycopg.connect(URL)
    leitor.execute(f'SELECT count(*) FROM futebol."{TAB_A}"')  # leitor longo na primeira tabela
    ctx = _ctx(teto_espera_ms=200, tentativas=2)
    monkeypatch.setattr(sync.troca_mod, "novo_contexto", lambda troca, staged: _com_selecao(ctx, troca, staged))
    try:
        r = sync.run_sync(
            tables=f"{TAB_A},{TAB_B}", env="prd", sport="futebol", troca=f"{TAB_A},{TAB_B}"
        )
    finally:
        leitor.rollback()
        leitor.close()
    assert r["status"] == "swap_failed"
    assert r["falhas"] == [{"table": TAB_A, "motivo": "lock_timeout"}]
    assert [x["table"] for x in r["synced"]] == [TAB_B]  # a seguinte sincronizou
    assert _valores(TAB_A) == ["velho"]  # vigente intacta
    assert _valores(TAB_B) == ["novo-1", "novo-2"]
    estado = _um("SELECT array_agg(table_name) FROM futebol._sync_state")
    assert estado == [TAB_B]  # o estado da falha NÃO avançou: o detector de atraso a enxerga
    assert _um("SELECT count(*) FROM pg_class WHERE relnamespace='futebol'::regnamespace AND relname LIKE '%\\_\\_new'") == 0


def _com_selecao(ctx, troca_sel, staged_sel):
    ctx.troca, ctx.staged = frozenset(troca_sel), frozenset(staged_sel)
    return ctx


def test_run_sync_remove_sombra_orfa_no_inicio_mesmo_com_a_troca_desligada(run_banco):
    _exec(f'CREATE TABLE futebol."{TAB_A}__new" (a text)')
    r = sync.run_sync(tables=TAB_A, env="prd", sport="futebol")
    assert _um("SELECT count(*) FROM pg_class WHERE relnamespace='futebol'::regnamespace AND relname LIKE '%\\_\\_new'") == 0
    assert r["status"] == "success"


def test_run_sync_sombra_orfa_com_mais_de_2h_vira_aviso_no_status(run_banco):
    velha = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    _exec(f'CREATE TABLE futebol."{TAB_B}__new" (a text)')
    _exec(f"COMMENT ON TABLE futebol.\"{TAB_B}__new\" IS '{troca.MARCADOR_SOMBRA}{velha}'")
    r = sync.run_sync(tables=TAB_A, env="prd", sport="futebol")
    assert r["status"] == "success"
    assert len(r["avisos"]) == 1 and f"{TAB_B}__new" in r["avisos"][0]


def test_run_sync_drift_de_schema_com_troca_habilitada_continua_abortando_tudo(run_banco, monkeypatch):
    monkeypatch.setattr(
        sync, "check_schema_parity", lambda *a, **kw: [{"table": TAB_A, "kind": "type_mismatch", "detail": "x"}]
    )
    r = sync.run_sync(tables=f"{TAB_A},{TAB_B}", env="prd", sport="futebol", troca=TAB_A)
    assert r["status"] == "aborted_schema_drift"
    assert r["synced"] == []
    assert _valores(TAB_A) == ["velho"] and _valores(TAB_B) == ["velho"]


def test_migration_segurando_a_vigente_faz_a_criacao_da_sombra_falhar_pelo_teto_e_nao_pendurar(banco):
    _cria_jogos()
    migration = psycopg.connect(URL)
    migration.execute(f"LOCK TABLE {S}.jogos IN ACCESS EXCLUSIVE MODE")  # DDL longo do app
    try:
        inicio = time.monotonic()
        with psycopg.connect(URL) as conn:
            with pytest.raises(troca.TrocaFalhou) as e:
                troca.carrega_por_troca(
                    conn, S, "jogos", _ctx(teto_espera_ms=300), copiar=_copiar([(7, "n", 1.0)]),
                    atualiza_estado=_estado,
                )
        assert e.value.motivo == troca.MOTIVO_LOCK_TIMEOUT
        assert time.monotonic() - inicio < 5
    finally:
        migration.rollback()
        migration.close()
