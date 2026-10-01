"""Detector de sync concorrente (DE#107): o parser e a regra de sobreposição.

Amostras sintéticas no formato real do Cloud Logging do Cloud Run (conferido em 01/10/2026):
o `timestamp` do log de requisição é a CHEGADA, `httpRequest.latency` é a duração e a
instância vem em `labels.instanceId`. Nenhum teste chama o `gcloud`.
"""
from datetime import datetime, timedelta, timezone

from src.monitoring import sync_concorrente as det

T0 = datetime(2026, 9, 28, 13, 0, 0, tzinfo=timezone.utc)
URL = "https://sync-bq-to-postgres-r52p4w2f2a-ue.a.run.app/?env={env}&sport={sport}"


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"  # nanossegundos, como o Logging


def req(seg, latencia, status=200, env="prd", sport="futebol", inst="A"):
    return {
        "timestamp": _iso(T0 + timedelta(seconds=seg)),
        "httpRequest": {
            "requestUrl": URL.format(env=env, sport=sport),
            "status": status,
            "latency": f"{latencia}s",
        },
        "labels": {"instanceId": inst},
    }


def log(seg, msg, inst="A", json_payload=False):
    e = {"timestamp": _iso(T0 + timedelta(seconds=seg)), "labels": {"instanceId": inst}}
    if json_payload:
        e["jsonPayload"] = {"message": msg}
    else:
        e["textPayload"] = msg
    return e


def inicio(seg, env="prd", sport="futebol", inst="A"):
    return log(seg + 0.1, f"Sync solicitado sport={sport} env={env} para 22 tabela(s)", inst)


def fim(seg, env="prd", sport="futebol", inst="A"):
    return log(seg, f"Sync sport={sport} env={env} concluído: 3 tabela(s)", inst)


def falha(seg, env="prd", sport="futebol", inst="A"):
    return log(seg, f"Falha no sync (sport={sport}, env={env}): boom", inst)


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------
def test_timestamp_com_nanossegundos_e_latencia_em_segundos():
    t = det.parse_ts("2026-10-01T19:19:39.421936123Z")
    assert t == datetime(2026, 10, 1, 19, 19, 39, 421936, tzinfo=timezone.utc)
    assert det.parse_latencia("809.871782031s") == timedelta(seconds=809.871782031)
    assert det.parse_latencia(None) == timedelta(0)


def test_chave_sai_da_url_com_os_defaults_do_handler():
    assert det.chave_da_url(URL.format(env="dev", sport="futebol")) == ("futebol", "dev")
    assert det.chave_da_url("https://x.run.app/") == ("nba", "prd")


# ------------------------------------------------------------------
# Verde
# ------------------------------------------------------------------
def test_um_sync_normal_e_verde():
    entradas = [req(0, 60), inicio(0), fim(60)]

    r = det.extrai_syncs(entradas)

    assert len(r.syncs) == 1 and r.syncs[0].como == "resposta"
    texto, codigo = det.monta_relatorio(r)
    assert codigo == 0 and "VERDE" in texto


def test_prd_e_dev_em_sequencia_ou_ao_mesmo_tempo_nao_sao_concorrentes():
    entradas = [
        req(0, 100, env="prd"), inicio(0, env="prd"), fim(100, env="prd"),
        req(50, 100, env="dev"), inicio(50, env="dev"), fim(150, env="dev"),
    ]

    assert det.acha_concorrentes(det.extrai_syncs(entradas).syncs) == []


def test_requisicao_sem_sync_solicitado_nao_e_sync():
    # 401/403/429: a requisição nunca chegou ao app.
    r = det.extrai_syncs([req(0, 0.01, status=403)])

    assert r.syncs == []


# ------------------------------------------------------------------
# Vermelho: o retry do workflow em cima da órfã de 504 (a causa da issue)
# ------------------------------------------------------------------
def test_retry_em_cima_da_orfa_de_504_e_concorrente():
    entradas = [
        req(0, 900, status=504), inicio(0),            # cortada aos 900 s, thread segue
        req(905, 300), inicio(905), fim(1205),          # retry 5 s depois, mesma instância
        fim(1000),                                      # a órfã só termina aos 1000 s
    ]

    r = det.extrai_syncs(entradas)
    pares = det.acha_concorrentes(r.syncs)

    assert len(pares) == 1
    a, b = pares[0]
    assert a.status == 504 and a.como == "órfã, linha de fim" and a.fim == T0 + timedelta(seconds=1000)
    assert det.sobreposicao_segundos(a, b) == 95
    texto, codigo = det.monta_relatorio(r)
    assert codigo == 1 and "CONCORRENTE futebol/prd" in texto and "+100s depois do corte" in texto


def test_orfa_que_termina_antes_do_retry_nao_e_concorrente():
    entradas = [
        req(0, 900, status=504), inicio(0), fim(902),   # terminou 2 s depois do corte
        req(905, 300), inicio(905), fim(1205),
    ]

    r = det.extrai_syncs(entradas)

    assert det.acha_concorrentes(r.syncs) == []


def test_falha_no_sync_em_textpayload_e_em_jsonpayload_encerra_a_orfa():
    for jp in (False, True):
        entradas = [
            req(0, 900, status=504), inicio(0),
            log(950, "Falha no sync (sport=futebol, env=prd): boom", json_payload=jp),
            req(905, 30), inicio(905), fim(935),
        ]
        r = det.extrai_syncs(entradas)
        orfa = next(s for s in r.syncs if s.orfa)
        assert orfa.fim == T0 + timedelta(seconds=950), jp
        assert len(det.acha_concorrentes(r.syncs)) == 1, jp


def test_orfa_sem_linha_de_fim_morre_com_a_instancia():
    entradas = [
        req(0, 900, status=504), inicio(0),
        log(940, "COPY fact_odds_snapshot em andamento"),  # último sinal de vida da instância
        req(905, 30, inst="A"), inicio(905), fim(935),
    ]

    r = det.extrai_syncs(entradas)
    orfa = next(s for s in r.syncs if s.orfa)

    assert orfa.como == "órfã, morreu com a instância"
    assert orfa.fim == T0 + timedelta(seconds=940)
    assert len(det.acha_concorrentes(r.syncs)) == 1


def test_a_linha_de_fim_de_outra_instancia_nao_encerra_a_orfa():
    # Casar sem a instância inventava "órfãs de horas" (diagnóstico de 28/09).
    entradas = [
        req(0, 900, status=504, inst="A"), inicio(0, inst="A"),
        fim(5000, inst="B"),
    ]

    r = det.extrai_syncs(entradas)

    assert r.syncs[0].como == "órfã, morreu com a instância"
    assert r.syncs[0].fim == T0 + timedelta(seconds=900)  # nenhum log de A além da resposta


def test_499_do_cliente_que_desistiu_tambem_deixa_orfa():
    entradas = [req(0, 1800, status=499), inicio(0), fim(1850), req(1805, 50), inicio(1805), fim(1855)]

    r = det.extrai_syncs(entradas)

    assert len(det.acha_concorrentes(r.syncs)) == 1


# ------------------------------------------------------------------
# Depois da trava: o 409 é a trava trabalhando, não concorrência
# ------------------------------------------------------------------
def test_retry_barrado_com_409_nao_e_concorrente_e_e_contado():
    entradas = [
        req(0, 1800, status=499), inicio(0), fim(1850),
        req(1805, 0.3, status=409), inicio(1805),        # o handler loga o início e devolve 409
    ]

    r = det.extrai_syncs(entradas)

    assert len(r.syncs) == 1
    assert r.barradas == [(("futebol", "prd"), T0 + timedelta(seconds=1805))]
    texto, codigo = det.monta_relatorio(r)
    assert codigo == 0 and "barradas pela trava (409)=1" in texto


def test_dois_syncs_200_sobrepostos_continuam_vermelhos_mesmo_depois_da_trava():
    # Se acontecer depois do deploy, a trava falhou: é exatamente o que o detector pega.
    entradas = [
        req(0, 100), inicio(0), fim(100),
        req(50, 100, inst="B"), inicio(50, inst="B"), fim(150, inst="B"),
    ]

    _, codigo = det.monta_relatorio(det.extrai_syncs(entradas))

    assert codigo == 1


# ------------------------------------------------------------------
# Execuções do Workflows
# ------------------------------------------------------------------
def _execucao(nome, ini, fim_, estado="SUCCEEDED"):
    d = {"name": f"projects/1/locations/us-east1/workflows/wf/executions/{nome}",
         "startTime": ini, "state": estado}
    if fim_:
        d["endTime"] = fim_
    return d


def test_execucoes_sobrepostas_do_workflow():
    itens = [
        _execucao("aaaa1111", "2026-09-28T13:00:00.1Z", "2026-09-28T13:52:00.1Z"),
        _execucao("bbbb2222", "2026-09-28T13:00:05.1Z", "2026-09-28T13:10:00.1Z"),
        _execucao("cccc3333", "2026-09-28T15:00:00.1Z", "2026-09-28T15:01:00.1Z"),
    ]

    execs = det.parse_execucoes(itens, ate=T0 + timedelta(days=1))
    pares = det.acha_execucoes_sobrepostas(execs)

    assert [(a.nome, b.nome) for a, b in pares] == [("aaaa1111", "bbbb2222")]


def test_execucao_ativa_termina_no_fim_da_janela():
    execs = det.parse_execucoes(
        [_execucao("ativa000", "2026-09-28T13:00:00Z", None, estado="ACTIVE")], ate=T0 + timedelta(hours=2)
    )

    assert execs[0].fim == T0 + timedelta(hours=2)


def test_sobreposicao_de_execucoes_e_so_informativa():
    execs = det.parse_execucoes(
        [
            _execucao("aaaa1111", "2026-09-28T13:00:00Z", "2026-09-28T13:52:00Z"),
            _execucao("bbbb2222", "2026-09-28T13:30:00Z", "2026-09-28T13:40:00Z"),
        ],
        ate=T0 + timedelta(days=1),
    )

    texto, codigo = det.monta_relatorio(det.extrai_syncs([req(0, 60), inicio(0), fim(60)]), execs)

    assert codigo == 0
    assert "sobrepostas entre si: 1" in texto
