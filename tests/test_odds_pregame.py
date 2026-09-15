"""Testes do poll pré-jogo de odds (OddsExtractor).

Cobre a janela DIÁRIA (horizonte de 7 dias, N capturas por fixture por dia em blocos
não-uniformes — FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES, PPP#366, date-stampada por bloco) e
a disjunção das bandas — que é requisito, não detalhe: bandas sobrepostas fazem a mesma
passada bucketar o mesmo fixture duas vezes, gastando duas chamadas e gravando duas linhas
com rótulos diferentes para o mesmo preço.

Infra (GCS/API) é mockada — nenhum acesso de rede, no idioma de
tests/test_per_fixture_extractor.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config import (
    BRASILEIRAO_ID,
    FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES,
    FUTEBOL_ODDS_HORIZON_MIN,
    FUTEBOL_ODDS_WINDOWS,
    FUTEBOL_ODDS_WINDOWS_DIARIAS,
)
from src.extractors.odds_extractor import _daily_bucket_stamp


@pytest.fixture
def ext():
    # Patches só p/ o __init__ não instanciar GCSStorage()/ApiFootballClient() reais.
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.odds_extractor.ApiFootballClient"), \
         patch("src.extractors.odds_extractor.time.sleep", lambda *_: None):
        from src.extractors.odds_extractor import OddsExtractor

        e = OddsExtractor()
        e.storage = MagicMock()
        e.client = MagicMock()
        e.storage.bucket.blob.return_value.exists.return_value = False
        e.storage.upload_json.side_effect = lambda **kw: (
            f"gs://b/odds/{kw['game_id']}_{kw['mode']}_{kw.get('date')}.json"
        )
        yield e


def _fixture(horas_ate_o_apito, fixture_id=999, league_id=BRASILEIRAO_ID):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=horas_ate_o_apito)
    return {
        "fixture_id": fixture_id,
        "league_id": league_id,
        "season": 2026,
        "kickoff_ts": int(kickoff.timestamp()),
    }


def _envelope_com_odds():
    return {
        "errors": [],
        "response": [
            {
                "update": "2026-08-07T00:00:00+00:00",
                "bookmakers": [
                    {"id": 4, "name": "Pinnacle", "bets": [{"id": 1, "name": "Match Winner"}]}
                ],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# As bandas
# --------------------------------------------------------------------------- #
def test_o_horizonte_e_um_parametro():
    # AC: "ampliar de 7 para N dias é mudar um número, não acrescentar uma banda nova".
    _, teto = FUTEBOL_ODDS_WINDOWS["daily"]
    assert teto == FUTEBOL_ODDS_HORIZON_MIN
    assert FUTEBOL_ODDS_HORIZON_MIN > 24 * 60  # cobre alem do dia seguinte


def test_as_bandas_sao_disjuntas():
    # O teste que protege a disjuncao no nivel da configuracao: qualquer sobreposicao
    # futura quebra aqui, nao em producao com chamada dobrada.
    bandas = sorted(FUTEBOL_ODDS_WINDOWS.values())
    for (lo_a, hi_a), (lo_b, hi_b) in zip(bandas, bandas[1:]):
        assert hi_a < lo_b, f"bandas se sobrepoem: ({lo_a},{hi_a}) e ({lo_b},{hi_b})"


def test_a_janela_diaria_comeca_imediatamente_acima_da_de_24h():
    _, teto_t24h = FUTEBOL_ODDS_WINDOWS["t24h"]
    piso_daily, _ = FUTEBOL_ODDS_WINDOWS["daily"]
    assert piso_daily == teto_t24h + 1


def test_as_bandas_de_fechamento_ficam_intactas():
    # AC: "t24h, t1h e t15m intactas em posição e semântica".
    assert FUTEBOL_ODDS_WINDOWS["t24h"] == (1320, 1440)
    assert FUTEBOL_ODDS_WINDOWS["t1h"] == (30, 60)
    assert FUTEBOL_ODDS_WINDOWS["t15m"] == (0, 15)


def test_as_janelas_diarias_sao_um_subconjunto_das_janelas():
    # Invariante, nao o valor: uma janela declarada como diaria mas ausente do mapa
    # seria date-stamp em janela que nao existe.
    assert FUTEBOL_ODDS_WINDOWS_DIARIAS <= set(FUTEBOL_ODDS_WINDOWS)
    assert FUTEBOL_ODDS_WINDOWS_DIARIAS  # e ha pelo menos uma


@pytest.mark.parametrize(
    "horas,janela_esperada",
    [
        (0.1, "t15m"),
        (0.2, "t15m"),
        (0.75, "t1h"),
        (1.0, "t1h"),
        (22.5, "t24h"),
        (23.9, "t24h"),
        (48, "daily"),
        (72, "daily"),
        (167, "daily"),
        (12, None),   # vao intencional entre t1h e t24h
        (200, None),  # alem do horizonte
    ],
)
def test_cada_lead_cai_na_janela_certa(ext, horas, janela_esperada):
    # Mais forte que "no maximo uma janela": pina QUAL, entao capturar zero vezes
    # tambem quebra.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(horas)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    if janela_esperada is None:
        ext.storage.upload_json.assert_not_called()
    else:
        assert ext.storage.upload_json.call_count == 1
        assert ext.storage.upload_json.call_args.kwargs["mode"] == janela_esperada


# --------------------------------------------------------------------------- #
# Em que janela cada fixture cai
# --------------------------------------------------------------------------- #
def test_fixture_a_poucos_dias_cai_na_janela_diaria(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    paths = ext.extract_and_save()

    assert len(paths) == 1
    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "daily"
    assert kw["data"]["collection_window"] == "daily"


def test_fixture_perto_do_apito_continua_na_banda_de_24h(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(23)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "t24h"


def test_fixture_no_limite_do_horizonte_ainda_entra(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(FUTEBOL_ODDS_HORIZON_MIN / 60 - 1)
    ]
    ext.client.get_odds.return_value = _envelope_com_odds()

    assert len(ext.extract_and_save()) == 1


def test_fixture_alem_do_horizonte_nao_gasta_chamada(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(FUTEBOL_ODDS_HORIZON_MIN / 60 + 24)
    ]

    assert ext.extract_and_save() == []
    ext.client.get_odds.assert_not_called()


def test_nenhum_fixture_cai_em_duas_janelas_na_mesma_passada(ext):
    # Bandas sobrepostas gastariam duas chamadas e gravariam duas linhas com rotulos
    # diferentes p/ o mesmo preco. Varre varios leads, um fixture por vez.
    ext.client.get_odds.return_value = _envelope_com_odds()

    for horas in (0.1, 0.2, 0.75, 1, 12, 22.5, 23.9, 30, 48, 72, 120, 167):
        ext.client.get_odds.reset_mock()
        ext.storage.upload_json.reset_mock()
        ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(horas)]

        ext.extract_and_save()

        assert ext.client.get_odds.call_count <= 1, f"lead de {horas}h caiu em 2 janelas"
        assert ext.storage.upload_json.call_count <= 1, f"lead de {horas}h gravou 2x"


# --------------------------------------------------------------------------- #
# Date-stamp e idempotencia diaria (PPP#366: bloco de horas, nao mais o dia inteiro)
# --------------------------------------------------------------------------- #
def test_a_captura_diaria_e_date_stampada(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    esperado = _daily_bucket_stamp(datetime.now(timezone.utc), ext.daily_bucket_boundaries)
    assert ext.storage.upload_json.call_args.kwargs["date"] == esperado


def test_as_bandas_de_fechamento_nao_date_stampam(ext):
    # Sem date-stamp o nome do arquivo fica exatamente como hoje — o fato ja le assim.
    for horas, janela in ((23, "t24h"), (0.75, "t1h"), (0.1, "t15m")):
        ext.storage.upload_json.reset_mock()
        ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(horas)]
        ext.client.get_odds.return_value = _envelope_com_odds()

        ext.extract_and_save()

        kw = ext.storage.upload_json.call_args.kwargs
        assert kw["mode"] == janela
        assert kw.get("date") is None, f"{janela} nao pode date-stampar"


def test_captura_diaria_idempotente_dentro_do_bloco(ext):
    # Segunda passada no MESMO bloco de horas: skip-if-exists por (fixture, janela, bloco).
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.storage.bucket.blob.return_value.exists.return_value = True

    assert ext.extract_and_save() == []
    ext.client.get_odds.assert_not_called()
    ext.storage.upload_json.assert_not_called()


def test_captura_diaria_repete_num_bloco_novo(ext):
    # PPP#366: e o ponto inteiro da mudanca — o mesmo fixture, no mesmo dia, recaptura
    # quando o relogio cruza pra proxima fronteira de FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES.
    # Sem mockar o relogio real (os outros testes usam datetime.now de verdade): testa a
    # funcao pura direto, que e quem decide o stamp. Usa a fronteira 06h/09h, uma das cinco
    # transicoes de 3h nas boundaries aprovadas (06,09,12,15,18,21) — so 00h e diferente,
    # com 6h de largura de proposito.
    boundaries = FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES
    inicio_do_bloco = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)

    stamp_bloco_06h = _daily_bucket_stamp(inicio_do_bloco, boundaries)
    stamp_ainda_no_bloco_06h = _daily_bucket_stamp(
        inicio_do_bloco + timedelta(hours=2, minutes=59), boundaries
    )
    stamp_bloco_09h = _daily_bucket_stamp(
        inicio_do_bloco + timedelta(hours=3), boundaries
    )

    assert stamp_bloco_06h == stamp_ainda_no_bloco_06h, "mesmo bloco tem que dar o mesmo stamp"
    assert stamp_bloco_06h != stamp_bloco_09h, "bloco seguinte tem que destravar o skip-if-exists"


def test_daily_bucket_stamp_marca_o_bloco_nao_so_o_dia():
    # Boundaries aprovadas (15/09, wdx6zf0fq2): 00h-06h e 1 bloco so (odd nao anda de
    # madrugada), dali em diante blocos de 3h. Duas horas no mesmo bloco dao o mesmo stamp;
    # a fronteira do bloco muda o stamp.
    boundaries = FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc), boundaries) == "2026-09-04_00h"
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 5, 59, tzinfo=timezone.utc), boundaries) == "2026-09-04_00h"
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc), boundaries) == "2026-09-04_06h"
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 8, 59, tzinfo=timezone.utc), boundaries) == "2026-09-04_06h"
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc), boundaries) == "2026-09-04_09h"
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc), boundaries) == "2026-09-04_21h"
    # boundaries=range(24) (o pior caso, so pra provar que a formula generaliza) da 1 bloco/hora.
    assert _daily_bucket_stamp(datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc), tuple(range(24))) == "2026-09-04_13h"


def test_daily_bucket_stamp_nao_estoura_sem_zero_nas_boundaries():
    # Achado do code-review: _daily_bucket_stamp roda fora do try/except por-fixture de
    # extract_and_save. Um max() de gerador vazio (nenhum limite <= a hora atual) levantaria
    # ValueError e derrubaria o poll inteiro — inclusive as bandas de fechamento forward-only
    # (t15m) de todo fixture daquela passada. Boundaries sem 0 e hora antes do primeiro
    # limite é o caso que provoca isso; a função cai pro menor limite em vez de estourar.
    boundaries_sem_zero = (6, 9, 12, 15, 18, 21)
    antes_do_primeiro_limite = datetime(2026, 9, 4, 3, 0, tzinfo=timezone.utc)
    assert _daily_bucket_stamp(antes_do_primeiro_limite, boundaries_sem_zero) == "2026-09-04_06h"


def test_o_pior_caso_de_capturas_por_dia_e_limitado_pelo_bloco():
    # E a garantia de cota que FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES existe pra dar: o numero
    # de stamps distintos que um fixture pode receber num dia e EXATAMENTE
    # len(boundaries) — 7, nao 96 (a cadencia do poll de 15min) — ver o comentario "bomba de
    # cota" no config.
    boundaries = FUTEBOL_ODDS_DAILY_BUCKET_BOUNDARIES
    dia = datetime(2026, 9, 4, tzinfo=timezone.utc)
    stamps_do_dia = {
        _daily_bucket_stamp(dia + timedelta(minutes=15 * i), boundaries)
        for i in range(24 * 60 // 15)  # toda passada do poll de 15min ao longo do dia
    }
    assert len(stamps_do_dia) == len(boundaries)


def test_o_skip_if_exists_da_diaria_olha_o_caminho_com_bloco(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    esperado = _daily_bucket_stamp(datetime.now(timezone.utc), ext.daily_bucket_boundaries)
    consultado = ext.storage.bucket.blob.call_args.args[0]
    assert consultado == f"futebol/odds/raw_futebol_odds_999_daily_{esperado}.json"


# --------------------------------------------------------------------------- #
# Sem regressao
# --------------------------------------------------------------------------- #
def test_liga_fora_dos_targets_nao_gasta_chamada(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(72, league_id=999999)
    ]

    assert ext.extract_and_save() == []
    ext.client.get_odds.assert_not_called()


def test_banda_de_fechamento_sem_odds_continua_sem_gravar(ext):
    # Banda curta (minutos) e forward-only: a casa pode publicar a qualquer momento, e
    # gravar aqui travaria o skip-if-exists e perderia a linha de fechamento.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(23)]
    ext.client.get_odds.return_value = {"errors": [], "response": []}

    assert ext.extract_and_save() == []
    ext.storage.upload_json.assert_not_called()


# --------------------------------------------------------------------------- #
# Vazio registrado na janela diaria
#
# Sem isso a banda diaria e uma bomba de cota: ela tem DIAS de largura, entao um fixture
# sem odds publicadas nunca grava arquivo, o skip-if-exists nunca trava, e o poll de 15min
# repergunta o mesmo vazio ~96x/dia por ate uma semana. Liga dormente (coverage.odds=FALSE
# ate a abertura) devolve vazio de proposito — e sao 5 delas armadas hoje.
# --------------------------------------------------------------------------- #
def test_janela_diaria_sem_odds_grava_o_vazio_registrado(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = {"errors": [], "response": []}

    ext.extract_and_save()

    ext.storage.upload_json.assert_called_once()
    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "daily"
    assert kw["data"]["total_bookmakers"] == 0
    assert kw["data"]["fixture_id"] == 999
    assert kw["date"] == _daily_bucket_stamp(datetime.now(timezone.utc), ext.daily_bucket_boundaries)


def test_vazio_registrado_da_diaria_nao_abre_o_gate_do_dbt(ext):
    # Arquivo sem casa nenhuma nao gera linha no fato (o UNNEST de bets vazio elimina a
    # linha no staging), entao nao ha rebuild a fazer.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = {"errors": [], "response": []}

    assert ext.extract_and_save() == []
    ext.storage.upload_json.assert_called_once()


def test_com_o_vazio_gravado_a_diaria_nao_repergunta_no_mesmo_dia(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.storage.bucket.blob.return_value.exists.return_value = True

    ext.extract_and_save()

    ext.client.get_odds.assert_not_called()


def test_a_varredura_pede_ao_storage_o_horizonte_novo(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = []

    ext.extract_and_save()

    ext.storage.get_upcoming_fixtures_with_kickoff.assert_called_once_with(
        FUTEBOL_ODDS_HORIZON_MIN
    )
