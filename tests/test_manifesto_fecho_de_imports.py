"""Auditor de fecho de imports contra o manifesto de procedência (DE #55).

O manifesto (`scripts/procedencia_servicos.sh`) é declarado à mão, e "manifesto
declarativo apodrece para dentro": alguém acrescenta um import, esquece de declarar a
path, o hash passa a cobrir menos e o detector emudece sobre a parte não coberta —
FAIL-OPEN, o pecado capital do mecanismo inteiro (ver ADR 0001 deste repo).

Este teste parte do `main.py` de cada um dos 29 serviços, resolve o FECHO DE IMPORTS
por AST (transitivamente, dentro de `src/` e `scripts/`) e falha se o fecho alcançar
um módulo local que o manifesto daquele serviço não declara.

A declaração continua sendo a fonte do hash (`--paths` de `procedencia_servicos.sh`)
— o fecho NÃO vira a fonte. Um parser que errasse um import encolheria a cobertura em
silêncio, que é o problema, não a solução; o fecho é o AUDITOR do manifesto, não sua
autoridade.

LIMITE DELIBERADO — `__init__.py` implícito: a resolução aqui segue os módulos
NOMEADOS explicitamente em `import`/`from ... import` (ex.: `src.extractors.games_extractor`
→ `src/extractors/games_extractor.py`), não a cadeia de pacotes que o Python
executaria implicitamente (`src/__init__.py`, `src/extractors/__init__.py`, ...). Os
`__init__.py` deste repo são todos triviais (1-6 linhas, nenhum tem lógica de
extração) e o manifesto pré-existente (DE #50/#51) já não os declara — este auditor
audita ESSE manifesto, não redesenha a granularidade dele.

IMPORT NÃO RESOLVÍVEL É FALHA, NÃO É PULADO EM SILÊNCIO: um import cujo alvo não é
nem um arquivo local (`src/`/`scripts/`) nem um pacote instalado (stdlib/terceiro,
verificado via `importlib.util.find_spec`) levanta `ImportoNaoResolvivel` e o teste
falha — a mesma regra fail-closed do resto do mecanismo. Isso cobre tanto um typo no
nome do módulo quanto um import bare (`from extract_x import y`) apontando para um
arquivo `scripts/extract_x.py` que foi apagado.
"""
import ast
import importlib.util
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_cloud_run_argv as _deploy_argv  # reusa _parse_service_table (fonte única da tabela)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROCEDENCIA_SCRIPT = REPO_ROOT / "scripts" / "procedencia_servicos.sh"

# Roots de import que sabemos ser dependência EXTERNA (declarada em algum
# `requirements.txt` deste repo) mas que podem não estar instalados no venv de
# desenvolvimento — ele instala só o `requirements.txt` da raiz, e
# `functions_framework` só existe nos `requirements.txt` de `cloud_run/<svc>/` (roda
# apenas dentro do wrapper HTTP do Cloud Run). `google.cloud.logging`/
# `google.cloud.workflows` (só o `daily-summary` usa) NÃO precisam de entrada aqui:
# o root `google` já resolve via `find_spec` porque `google-cloud-storage` (raiz)
# cria o namespace package — só o submódulo profundo não está instalado, e este
# auditor só verifica o ROOT do import (ver `_resolver_import_local`), não a cadeia
# completa. O workflow de CI instala TODOS os `requirements.txt` (raiz + cada
# `cloud_run/*/`) antes de rodar este teste — em CI o `find_spec` já resolve sozinho
# mesmo para `functions_framework`; isto aqui é só o fallback para rodar local sem
# precisar instalar 29 conjuntos de dependências.
_EXTERNOS_SO_EM_REQUIREMENTS_DE_CLOUD_RUN = {
    "functions_framework",  # functions-framework, cloud_run/*/requirements.txt
}


class ImportoNaoResolvivel(Exception):
    """Import que o auditor não conseguiu classificar como local nem como externo."""


def _rodar_procedencia(*args):
    resultado = subprocess.run(
        ["bash", str(PROCEDENCIA_SCRIPT), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert resultado.returncode == 0, (
        f"procedencia_servicos.sh {' '.join(args)} falhou: {resultado.stderr}"
    )
    return resultado.stdout


def _manifest_services():
    return [l for l in _rodar_procedencia("--list-servicos").splitlines() if l.strip()]


def _declared_paths(servico):
    return {l for l in _rodar_procedencia(servico, "--paths").splitlines() if l.strip()}


def _service_dir(servico):
    """`cloud_run/<dir>` do serviço, via `_parse_service_table()` de
    `test_deploy_cloud_run_argv.py` — mesmo parser da tabela, não uma segunda cópia
    (achado do /code-review: duas cópias do parser podem divergir em silêncio)."""
    for nome, service_dir in _deploy_argv._parse_service_table():
        if nome == servico:
            return service_dir
    raise AssertionError(f"servico '{servico}' nao encontrado na tabela do deploy_cloud_run.sh")


def _resolver_import_local(nome_modulo: str, de_arquivo: Path):
    """Devolve a path relativa ao repo (str) se `nome_modulo` resolve para um arquivo
    LOCAL (`src/` ou `scripts/`), `None` se é externo (stdlib/terceiro), ou levanta
    `ImportoNaoResolvivel` se não conseguir classificar nenhum dos dois jeitos."""
    partes = nome_modulo.split(".")
    de_rel = de_arquivo.relative_to(REPO_ROOT)

    if partes[0] == "src":
        modulo = REPO_ROOT.joinpath(*partes).with_suffix(".py")
        pacote = REPO_ROOT.joinpath(*partes, "__init__.py")
        if modulo.is_file():
            return str(modulo.relative_to(REPO_ROOT))
        if pacote.is_file():
            return str(pacote.relative_to(REPO_ROOT))
        raise ImportoNaoResolvivel(
            f"'{nome_modulo}' (importado por {de_rel}) comeca com 'src.' mas nao existe em disco"
        )

    if len(partes) == 1:
        # Import BARE só faz sentido vindo de um `main.py` — é o padrão
        # `sys.path.insert(scripts/[futebol])` + `from extract_x import main` (ver
        # cabecalho de procedencia_servicos.sh). scripts/*.py (NBA) e
        # scripts/futebol/*.py não têm colisão de nome neste repo.
        candidato_nba = REPO_ROOT / "scripts" / f"{nome_modulo}.py"
        candidato_futebol = REPO_ROOT / "scripts" / "futebol" / f"{nome_modulo}.py"
        if candidato_nba.is_file():
            return str(candidato_nba.relative_to(REPO_ROOT))
        if candidato_futebol.is_file():
            return str(candidato_futebol.relative_to(REPO_ROOT))

    # Não bate com nada local — só pode ser externo (stdlib/terceiro). Verifica via
    # importlib (usa o MESMO interpretador/venv que roda o pytest, que precisa ter
    # `pip install -r requirements.txt` feito — é o que o workflow de CI faz). Se nem
    # isso resolver, é import quebrado: falha, não pula.
    try:
        spec = importlib.util.find_spec(partes[0])
    except (ImportError, ValueError, ModuleNotFoundError) as e:
        raise ImportoNaoResolvivel(
            f"'{nome_modulo}' (importado por {de_rel}): find_spec levantou {type(e).__name__}: {e}"
        )
    if spec is None:
        if partes[0] in _EXTERNOS_SO_EM_REQUIREMENTS_DE_CLOUD_RUN:
            return None
        raise ImportoNaoResolvivel(
            f"'{nome_modulo}' (importado por {de_rel}): nao resolve nem como arquivo local "
            f"(src/ ou scripts/) nem como pacote instalado"
        )
    if spec.origin is None or spec.origin in ("frozen", "built-in"):
        # `spec.origin` NÃO é sempre um caminho de arquivo: namespace package (ex.:
        # `google.cloud`) devolve `None`; módulo stdlib compilado no interpretador
        # (`os` -> "frozen", `sys`/`time`/`itertools` -> "built-in") devolve essas
        # strings literais. `Path("frozen").resolve()` resolveria contra o CWD e
        # PODE cair "dentro" do repo por coincidência (foi o que aconteceu rodando
        # daqui) — os três casos são igualmente externos, nenhum tem arquivo local.
        return None
    origem = Path(spec.origin).resolve()
    try:
        origem.relative_to(REPO_ROOT)
    except ValueError:
        return None  # fora do repo == externo, ignora
    raise ImportoNaoResolvivel(
        f"'{nome_modulo}' (importado por {de_rel}) resolveu para DENTRO do repo "
        f"({origem}) por um caminho que a heuristica local nao previu — auditor precisa de ajuste"
    )


def _imports_de(arquivo: Path):
    """Nomes de módulo (str) de todo `import`/`from ... import` no arquivo, incluindo
    os aninhados dentro de função/if/try — `ast.walk` visita a árvore inteira."""
    tree = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
    rel = arquivo.relative_to(REPO_ROOT)
    nomes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                nomes.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise ImportoNaoResolvivel(
                    f"import relativo (nivel {node.level}) em {rel} — auditor nao suporta"
                )
            if node.module is None:
                raise ImportoNaoResolvivel(f"'from . import ...' sem modulo em {rel}")
            nomes.append(node.module)
    return nomes


def fecho_de_imports(entry_file: Path):
    """Fecho transitivo (BFS) dos arquivos LOCAIS alcançados a partir de `entry_file`.
    Levanta `ImportoNaoResolvivel` na primeira falha de resolução (fail-closed). A
    ordem de travessia não muda o resultado (é um conjunto), mas `popleft()` mantém o
    código fiel ao nome — `pop()` do mesmo lado do `append()` seria DFS, não BFS."""
    visitados = set()
    fila = deque([entry_file])
    while fila:
        atual = fila.popleft()
        rel = str(atual.relative_to(REPO_ROOT))
        if rel in visitados:
            continue
        visitados.add(rel)
        for nome in _imports_de(atual):
            resolvido = _resolver_import_local(nome, atual)
            if resolvido is None:
                continue  # externo
            if resolvido not in visitados:
                fila.append(REPO_ROOT / resolvido)
    return visitados


def _coberto_pelo_manifesto(path_do_fecho: str, declaradas: set) -> bool:
    """`path_do_fecho` está coberto se está LITERALMENTE nas paths declaradas, ou se
    é um arquivo dentro de uma path declarada que é um DIRETÓRIO (`src/reporting`,
    `src/sync`, `src/clients`, etc. — o mesmo prefix-match que `git ls-files -- <dir>`
    já faz dentro de `hash_paths()` em `procedencia_servicos.sh`)."""
    if path_do_fecho in declaradas:
        return True
    for d in declaradas:
        if (REPO_ROOT / d).is_dir() and path_do_fecho.startswith(d.rstrip("/") + "/"):
            return True
    return False


@pytest.mark.parametrize("servico", _manifest_services())
def test_fecho_de_imports_esta_coberto_pelo_manifesto(servico):
    entry = REPO_ROOT / "cloud_run" / _service_dir(servico) / "main.py"
    assert entry.is_file(), f"{servico}: main.py nao existe em {entry}"

    fecho = fecho_de_imports(entry)
    declaradas = _declared_paths(servico)

    nao_declarados = sorted(p for p in fecho if not _coberto_pelo_manifesto(p, declaradas))
    assert not nao_declarados, (
        f"{servico}: o fecho de imports alcanca {nao_declarados}, que o manifesto de "
        f"procedencia_servicos.sh NAO declara para este servico. O manifesto apodreceu "
        f"para dentro (DE #55) — declare a(s) path(s) acima em procedencia_servicos.sh."
    )


def _escrever_arvore_fake(root: Path, com_import_extra: bool):
    (root / "src").mkdir(parents=True)
    (root / "src" / "x_extractor.py").write_text("X = 1\n", encoding="utf-8")
    (root / "src" / "y_helper.py").write_text("Y = 1\n", encoding="utf-8")

    (root / "scripts").mkdir()
    corpo = "from src.x_extractor import X\n"
    if com_import_extra:
        corpo += "from src.y_helper import Y\n"
    corpo += "\n\ndef main():\n    return 0\n"
    (root / "scripts" / "extract_x.py").write_text(corpo, encoding="utf-8")

    (root / "cloud_run" / "svc").mkdir(parents=True)
    (root / "cloud_run" / "svc" / "main.py").write_text(
        "from extract_x import main\n", encoding="utf-8"
    )


def test_falsificacao_import_nao_declarado_falha_e_declarar_devolve_verde(tmp_path, monkeypatch):
    """A falsificação do ADR/AC da DE #55: acrescentar um import não declarado a um
    serviço reprova o auditor; declará-lo devolve o verde — testado direto contra as
    funções do auditor, com uma árvore sintética (não a árvore real deste repo)."""
    monkeypatch.setattr("test_manifesto_fecho_de_imports.REPO_ROOT", tmp_path)
    _escrever_arvore_fake(tmp_path, com_import_extra=True)

    entry = tmp_path / "cloud_run" / "svc" / "main.py"
    fecho = fecho_de_imports(entry)
    assert fecho == {
        "cloud_run/svc/main.py",
        "scripts/extract_x.py",
        "src/x_extractor.py",
        "src/y_helper.py",
    }

    # "Sujo": o manifesto declara tudo MENOS o import novo (src/y_helper.py).
    declaradas_incompleta = {"cloud_run/svc/main.py", "scripts/extract_x.py", "src/x_extractor.py"}
    nao_declarados = sorted(p for p in fecho if not _coberto_pelo_manifesto(p, declaradas_incompleta))
    assert nao_declarados == ["src/y_helper.py"]

    # "Limpo": declarar a path que faltava devolve o verde.
    declaradas_completa = declaradas_incompleta | {"src/y_helper.py"}
    nao_declarados_depois = sorted(
        p for p in fecho if not _coberto_pelo_manifesto(p, declaradas_completa)
    )
    assert nao_declarados_depois == []


def test_import_local_nao_resolvivel_e_falha_nao_e_pulado(tmp_path, monkeypatch):
    """AC: import que o parser não consegue resolver (aqui: aponta para um arquivo
    `src/` que não existe) é reportado como FALHA, não pulado em silêncio."""
    monkeypatch.setattr("test_manifesto_fecho_de_imports.REPO_ROOT", tmp_path)
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "cloud_run" / "svc").mkdir(parents=True)
    (tmp_path / "cloud_run" / "svc" / "main.py").write_text(
        "from src.modulo_que_nao_existe import Alguma\n", encoding="utf-8"
    )

    with pytest.raises(ImportoNaoResolvivel, match="modulo_que_nao_existe"):
        fecho_de_imports(tmp_path / "cloud_run" / "svc" / "main.py")


def test_import_bare_apontando_para_arquivo_apagado_e_falha(tmp_path, monkeypatch):
    """A mesma regra para o padrão BARE (`from extract_x import main`, usado pelos
    `main.py` reais): se `scripts/extract_x.py` sumir do disco, falha — não vira
    silenciosamente "externo"."""
    monkeypatch.setattr("test_manifesto_fecho_de_imports.REPO_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "cloud_run" / "svc").mkdir(parents=True)
    (tmp_path / "cloud_run" / "svc" / "main.py").write_text(
        "from extract_x import main\n", encoding="utf-8"
    )
    # scripts/extract_x.py deliberadamente ausente.

    with pytest.raises(ImportoNaoResolvivel, match="extract_x"):
        fecho_de_imports(tmp_path / "cloud_run" / "svc" / "main.py")


if __name__ == "__main__":
    import sys
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
