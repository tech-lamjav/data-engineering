"""Guarda da tag `live` e dos modos `--so-tag-live` e `--derivados` do `deploy_cloud_run.sh`.

A cleanup policy do repo `cloud-run-source-deploy` (infra/ar-cleanup-cloud-run-source-deploy.json)
mantém tudo que tem tag com prefixo `live`. Ela só é segura se a tag seguir o digest que o
serviço SERVE (`status.traffic` → `status.imageDigest` de cada revisão), e não o que acabou de
ser empurrado: `latest` é movida no push, antes de a revisão existir. Os serviços escalam a
zero, então o digest vivo apagado quebra o cold start.

Tudo roda contra o `gcloud` de mentira do golden test (`test_deploy_cloud_run_argv.GCLOUD_STUB`),
com os botões `STUB_*` dele. Nada aqui toca a GCP.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_cloud_run_argv as base  # noqa: E402

PACOTE = "us-east1-docker.pkg.dev/fake-project/cloud-run-source-deploy/{svc}"


def _digest(rev):
    """O mesmo digest determinístico que o stub devolve para uma revisão."""
    svc = rev.rsplit("-", 2)[0]
    return f"{PACOTE.format(svc=svc)}@sha256:{hashlib.sha256(rev.encode()).hexdigest()}"


def _tag(rev, nome):
    svc = rev.rsplit("-", 2)[0]
    return f"add {_digest(rev)} {PACOTE.format(svc=svc)}:{nome} --quiet"


@pytest.fixture(scope="module")
def repo_falso(tmp_path_factory):
    """Uma árvore falsa só, reaproveitada: montar a árvore (29 serviços, ~30 chamadas ao
    manifesto) é a parte cara. Cada teste usa um CAPTURE_DIR próprio."""
    root = tmp_path_factory.mktemp("repo-tag-live") / "repo"
    root.mkdir()
    base._build_fake_repo(root, api_football_key=True)
    stub_dir = root / "_stubs"
    stub_dir.mkdir()
    for nome, corpo in (("gcloud", base.GCLOUD_STUB), ("sleep", base.SLEEP_STUB)):
        (stub_dir / nome).write_text(corpo, encoding="utf-8")
        (stub_dir / nome).chmod(0o755)
    return root


def rodar(root, tmp_path, *args, **extra_env):
    capture_dir = tmp_path / "_captura"
    capture_dir.mkdir()
    env = {
        "PATH": f"{root / '_stubs'}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "HOME": str(root),
        **extra_env,
    }
    resultado = subprocess.run(
        ["bash", str(root / "scripts" / "deploy_cloud_run.sh"), *args],
        capture_output=True, text=True, env=env, cwd=str(root),
    )
    tags_log = capture_dir / "tags.log"
    tags = tags_log.read_text(encoding="utf-8").splitlines() if tags_log.exists() else []
    deployados = sorted(p.stem for p in capture_dir.glob("*.argv"))
    return resultado, tags, deployados


# ---------------------------------------------------------------- tag live no deploy


def test_deploy_aplica_live_no_digest_da_revisao_com_trafego(repo_falso, tmp_path):
    resultado, tags, deployados = rodar(repo_falso, tmp_path, "extract-games")

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert deployados == ["extract-games"]
    assert tags == [_tag("extract-games-00001-abc", "live")]


def test_traffic_split_marca_live_na_maior_e_live_prev_na_segunda(repo_falso, tmp_path):
    trafego = [
        {"revisionName": "extract-games-00007-new", "percent": 10},
        {"revisionName": "extract-games-00006-old", "percent": 90},
    ]
    resultado, tags, _ = rodar(repo_falso, tmp_path, "extract-games",
                               STUB_TRAFFIC=json.dumps(trafego))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert tags == [
        _tag("extract-games-00006-old", "live"),
        _tag("extract-games-00007-new", "live-prev"),
    ]


def test_empate_de_trafego_da_live_para_a_revisao_mais_nova(repo_falso, tmp_path):
    trafego = [
        {"revisionName": "extract-games-00006-old", "percent": 50},
        {"revisionName": "extract-games-00007-new", "percent": 50},
    ]
    resultado, tags, _ = rodar(repo_falso, tmp_path, "extract-games",
                               STUB_TRAFFIC=json.dumps(trafego))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert tags[0] == _tag("extract-games-00007-new", "live")


def test_revisao_com_percent_zero_nao_e_marcada(repo_falso, tmp_path):
    """Revisão nomeada no tráfego com 0% (tag de URL de teste, rollback desfeito) não está
    servindo nada: não pode virar Keep permanente."""
    trafego = [
        {"revisionName": "extract-games-00007-new", "percent": 100},
        {"revisionName": "extract-games-00006-old", "percent": 0, "tag": "teste"},
    ]
    resultado, tags, _ = rodar(repo_falso, tmp_path, "extract-games",
                               STUB_TRAFFIC=json.dumps(trafego))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert tags == [_tag("extract-games-00007-new", "live")]


def test_latest_revision_sem_nome_usa_latest_ready(repo_falso, tmp_path):
    trafego = [{"latestRevision": True, "percent": 100}]
    resultado, tags, _ = rodar(repo_falso, tmp_path, "extract-games",
                               STUB_TRAFFIC=json.dumps(trafego))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert tags == [_tag("extract-games-00001-abc", "live")]


@pytest.mark.parametrize("trafego", [
    "null",  # describe sem status.traffic
    "[]",
    json.dumps([{"revisionName": "extract-games-00001-abc", "percent": 0}]),
    json.dumps([  # três revisões: só há dois nomes fixos de tag
        {"revisionName": "extract-games-00001-aaa", "percent": 50},
        {"revisionName": "extract-games-00002-bbb", "percent": 30},
        {"revisionName": "extract-games-00003-ccc", "percent": 20},
    ]),
], ids=["sem-traffic", "traffic-vazio", "tudo-zero", "tres-revisoes"])
def test_trafego_ilegivel_falha_o_deploy_sem_aplicar_tag(repo_falso, tmp_path, trafego):
    resultado, tags, deployados = rodar(repo_falso, tmp_path, "extract-games",
                                        STUB_TRAFFIC=trafego)

    assert deployados == ["extract-games"]
    assert resultado.returncode != 0, resultado.stdout
    assert tags == []
    assert "tag live NÃO aplicada" in resultado.stdout
    assert "--so-tag-live extract-games" in resultado.stdout


def test_digest_ausente_falha_sem_aplicar_tag(repo_falso, tmp_path):
    resultado, tags, _ = rodar(repo_falso, tmp_path, "extract-games", STUB_DIGEST_VAZIO="1")

    assert resultado.returncode != 0, resultado.stdout
    assert tags == []
    assert "imageDigest" in resultado.stdout + resultado.stderr


def test_deploy_com_exit_code_ruim_mas_servico_vivo_ainda_marca_live(repo_falso, tmp_path):
    """O ramo de falha do deploy diz "o serviço existe! O erro pode ter sido apenas na
    visualização de logs" — nesse caso há um digest novo servindo tráfego, e ele precisa da
    tag do mesmo jeito. O deploy continua reportado como falha."""
    resultado, tags, _ = rodar(repo_falso, tmp_path, "extract-games", STUB_DEPLOY_FALHA="1")

    assert resultado.returncode != 0
    assert tags == [_tag("extract-games-00001-abc", "live")]


# ---------------------------------------------------------------- --so-tag-live


def test_so_tag_live_nao_deploya_e_marca_um_servico(repo_falso, tmp_path):
    resultado, tags, deployados = rodar(repo_falso, tmp_path, "--so-tag-live", "extract-odds")

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert deployados == []
    assert tags == [_tag("extract-odds-00001-abc", "live")]


def test_so_tag_live_de_um_esporte_marca_os_13(repo_falso, tmp_path):
    resultado, tags, deployados = rodar(repo_falso, tmp_path, "--so-tag-live", "futebol")

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert deployados == []
    assert len(tags) == 13
    assert all(t.endswith(":live --quiet") for t in tags)


def test_so_tag_live_falha_quando_algum_alvo_falha(repo_falso, tmp_path):
    resultado, tags, _ = rodar(repo_falso, tmp_path, "--so-tag-live", "extract-odds",
                               STUB_TRAFFIC="[]")

    assert resultado.returncode != 0
    assert tags == []
    assert "1 falha(s)" in resultado.stdout


# ---------------------------------------------------------------- --derivados


def _hash_local(root, servico):
    return subprocess.run(
        ["bash", str(root / "scripts" / "procedencia_servicos.sh"), servico],
        capture_output=True, text=True, cwd=str(root), check=True,
    ).stdout.strip()


def _servicos_futebol():
    return [nome for nome, d in base._parse_service_table() if d.startswith("futebol/")]


@pytest.fixture
def carimbos(repo_falso, tmp_path):
    """Diretório STUB_CARIMBOS com os 13 de futebol EM DIA (hash gravado = hash do disco)."""
    pasta = tmp_path / "_carimbos"
    pasta.mkdir()
    for servico in _servicos_futebol():
        (pasta / servico).write_text(_hash_local(repo_falso, servico), encoding="utf-8")
    return pasta


def test_derivados_deploya_so_deriva_e_sem_carimbo(repo_falso, tmp_path, carimbos):
    (carimbos / "extract-teams").write_text("hash-velho", encoding="utf-8")  # deriva
    (carimbos / "extract-leagues").unlink()                                # sem carimbo

    resultado, tags, deployados = rodar(repo_falso, tmp_path, "--derivados", "futebol",
                                        STUB_CARIMBOS=str(carimbos))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert deployados == ["extract-leagues", "extract-teams"]
    assert sorted(tags) == sorted([
        _tag("extract-leagues-00001-abc", "live"),
        _tag("extract-teams-00001-abc", "live"),
    ])
    assert "em dia (pulado): extract-odds" in resultado.stdout


def test_derivados_sem_nada_derivado_nao_deploya(repo_falso, tmp_path, carimbos):
    resultado, tags, deployados = rodar(repo_falso, tmp_path, "--derivados", "futebol",
                                        STUB_CARIMBOS=str(carimbos))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert deployados == []
    assert tags == []
    assert "Nenhum serviço derivado" in resultado.stdout


def test_derivados_com_erro_de_leitura_aborta_antes_de_qualquer_deploy(
    repo_falso, tmp_path, carimbos,
):
    """Erro de leitura não é "em dia": pular deixaria produção defasada sem ninguém ver.
    Aborta a invocação inteira, inclusive os que derivaram de verdade."""
    (carimbos / "extract-teams").write_text("hash-velho", encoding="utf-8")
    (carimbos / "extract-odds.erro").write_text("", encoding="utf-8")

    resultado, tags, deployados = rodar(repo_falso, tmp_path, "--derivados", "futebol",
                                        STUB_CARIMBOS=str(carimbos))

    assert resultado.returncode != 0
    assert deployados == []
    assert tags == []
    assert "extract-odds: erro de leitura" in resultado.stdout


@pytest.mark.parametrize("args", [
    ["--derivados", "extract-games"],
    ["--derivados", "futebol", "extract-odds"],
], ids=["servico", "dois-args"])
def test_derivados_so_aceita_esporte(repo_falso, tmp_path, args):
    resultado, tags, deployados = rodar(repo_falso, tmp_path, *args)

    assert resultado.returncode != 0
    assert deployados == []
    assert "--derivados [sport]" in resultado.stdout


# ---------------------------------------------------------------- --lista-vivos


def test_lista_vivos_le_o_trafego_sem_escrever_tag(repo_falso, tmp_path):
    trafego = [
        {"revisionName": "extract-odds-00006-old", "percent": 90},
        {"revisionName": "extract-odds-00007-new", "percent": 10},
    ]
    resultado, tags, deployados = rodar(repo_falso, tmp_path, "--lista-vivos", "extract-odds",
                                        STUB_TRAFFIC=json.dumps(trafego))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert deployados == []
    assert tags == []
    vivos = [l for l in resultado.stdout.splitlines() if l.startswith("vivo ")]
    assert vivos == [
        f"vivo extract-odds live {_digest('extract-odds-00006-old')}",
        f"vivo extract-odds live-prev {_digest('extract-odds-00007-new')}",
    ]
