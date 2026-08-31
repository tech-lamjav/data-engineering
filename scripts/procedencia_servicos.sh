#!/bin/bash
# Carimbo de procedência de um serviço Cloud Run.
#
# Imprime um hash do CONTEÚDO EM DISCO das paths que o serviço EXECUTA (não das que o
# deploy COPIA). O `deploy_cloud_run.sh` grava esse hash como env var no serviço; o
# detector (`checa_deriva_servicos.sh`) recalcula a mesma coisa e compara. Divergência =
# deriva: o que roda em produção não é o que está no master.
#
# Uso:
#   scripts/procedencia_servicos.sh <servico>            # hash combinado (a verdade)
#   scripts/procedencia_servicos.sh <servico> --nucleo   # só o núcleo compartilhado
#   scripts/procedencia_servicos.sh <servico> --svc      # só a parte própria do serviço
#   scripts/procedencia_servicos.sh <servico> --paths    # lista as paths declaradas e sai
#
# TRACER BULLET (DE #50): só `extract-games` está declarado. Os outros 28 entram na
# DE #51, que também estende o `deploy_cloud_run.sh` às quatro ramificações do
# `deploy_service()`. Pedir o hash de um serviço não declarado sai com exit 3 — é
# escopo, não erro de path (esse é o exit 1, abaixo).
#
# POR QUE O HASH É DO DISCO, e não de `git rev-parse HEAD:<path>`:
# o `gcloud run deploy --source` empacota o diretório temporário que o
# `deploy_cloud_run.sh` monta EM DISCO, não o HEAD do git. Um build feito de um
# `.claude/worktrees/` numa branch com alteração ainda não commitada (é assim que este
# repo trabalha — ver CLAUDE.md) carimbaria o hash do master enquanto o container sobe
# outra coisa. O carimbo mentiria na direção perigosa ("está fresco" quando não está).
# Hasheando o disco, um build sujo aparece como deriva até virar commit — que é a
# verdade que o operador precisa.
#
# POR QUE O CARIMBO MORA NO SERVIÇO (env var), e não em label da imagem:
# o que apodrece não é a tag da imagem no Artifact Registry, é o hash FIXADO NA REVISÃO
# ativa — é essa a pergunta que o detector precisa responder, e a env var é legível por
# `gcloud run services describe` com só `roles/run.viewer`, sem tocar o registry (mesma
# escolha do carimbo dos Cloud Run Jobs dbt, ver ADR 0001 do analytics-engineering).
#
# POR QUE NÚCLEO E SVC SÃO HASHES SEPARADOS (decisão A do ADR 0001 deste repo):
# um serviço deriva sozinho quando só o próprio `main.py` muda (15 commits em
# `cloud_run/<dir>/` em 92 dias); um alvo único cego para isso faria os 29 acenderem
# juntos por qualquer commit em qualquer serviço. O combinado é o que o detector
# compara; os dois componentes existem para o relatório (DE #52) agrupar por causa
# comum sem reduzir a verdade por serviço a uma média.
#
# Ver docs/adr/0001-carimbo-de-procedencia-dos-servicos-cloud-run.md

set -euo pipefail

# Ordenação e hash precisam ser idênticos entre quem faz o deploy (macOS) e o detector
# (ubuntu-latest, no CI que a DE #54 vai criar). Sem LC_ALL=C a collation difere entre os
# dois, a ordem das linhas muda e o hash combinado muda por causa de locale, não de
# código — parecendo bug misterioso em vez de diferença de ambiente.
export LC_ALL=C

SERVICO="${1:-}"
FLAG="${2:-}"

if [ -z "$SERVICO" ]; then
    echo "ERROR: uso: $0 <servico> [--paths|--nucleo|--svc]" >&2
    exit 2
fi

# Manifesto declarativo: serviço -> núcleo compartilhado + parte própria do serviço.
# "Parte própria" funde os tiers "módulo" e "serviço" do ADR (extractor/script +
# main.py/Procfile) num só componente, PROCEDENCIA_HASH_SVC — o ADR só grava dois
# componentes por serviço porque o relatório (#52) só precisa distinguir "é o núcleo
# compartilhado" de "não é".
#
# Acrescentar um serviço aqui é acrescentar um ramo — feito na DE #51 para os outros 28.
declarar_paths() {
    local servico=$1
    case "$servico" in
        extract-games)
            NUCLEO_PATHS=(
                src/config.py
                src/clients
                src/storage
                src/utils
                src/bigquery
                cloud_run/extract_games/requirements.txt
            )
            SVC_PATHS=(
                src/extractors/games_extractor.py
                scripts/extract_games.py
                cloud_run/extract_games/main.py
            )
            ;;
        *)
            return 3
            ;;
    esac
    return 0
}

if ! declarar_paths "$SERVICO"; then
    echo "ERROR: servico '$SERVICO' ainda nao esta no manifesto do procedencia_servicos.sh." >&2
    echo "       Tracer bullet da DE #50 cobre so extract-games; os outros 28 entram na DE #51." >&2
    exit 3
fi

if [ "$FLAG" = "--paths" ]; then
    printf '%s\n' "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Fail-closed no inventário: path declarada que sumiu do disco ABORTA em vez de ser
# pulada em silêncio. Um typo no manifesto não pode encolher a cobertura calado — é a
# mesma regra do procedencia.sh dos jobs dbt (analytics-engineering).
#
# Isto cobre arquivo apagado. Diretório que ficou vazio (todo arquivo dentro dele
# apagado, mas a pasta em si continua no disco) passaria no `-e` e o `hash_paths`
# abaixo contribuiria silenciosamente ZERO entradas para aquele componente — a mesma
# classe de encolhimento calado que este bloco existe para barrar, só que por dentro
# de uma path que "existe". Por isso o segundo teste, só para diretório.
for p in "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"; do
    if [ ! -e "$p" ]; then
        echo "ERROR: path declarada para $SERVICO nao existe: $p" >&2
        exit 1
    fi
    if [ -d "$p" ] && [ -z "$(git ls-files -co --exclude-standard -- "$p")" ]; then
        echo "ERROR: path declarada para $SERVICO esta vazia (nenhum arquivo dentro): $p" >&2
        exit 1
    fi
done

# Par `<path> <blob>`, e não só o blob: nome de arquivo é comportamento aqui também —
# renomear `games_extractor.py` sem mudar uma linha quebra o import em
# `scripts/extract_games.py`, e um hash de conteúdo solto não veria isso.
#
# `git ls-files -co --exclude-standard`: rastreados (-c) + não-rastreados que o
# .gitignore não cobre (-o), que é exatamente o conjunto que o `docker build`/`gcloud
# run deploy --source` copiaria. Arquivo rastreado mas apagado do disco é pulado (o
# deploy também não o copiaria) — omiti-lo mantém o hash fiel ao disco.
hash_paths() {
    git ls-files -co --exclude-standard -- "$@" | while IFS= read -r f; do
        [ -f "$f" ] || continue
        printf '%s %s\n' "$f" "$(git hash-object "$f")"
    done | sort | git hash-object --stdin
}

HASH_NUCLEO=$(hash_paths "${NUCLEO_PATHS[@]}")
HASH_SVC=$(hash_paths "${SVC_PATHS[@]}")

case "$FLAG" in
    --nucleo)
        echo "$HASH_NUCLEO"
        ;;
    --svc)
        echo "$HASH_SVC"
        ;;
    "")
        # Combinado = hash sobre a UNIÃO das duas listas de paths, não sobre a
        # concatenação dos dois hashes já prontos — assim ele reproduz exatamente o
        # valor que hashear tudo de uma vez daria, e "a verdade é o combinado" (decisão A
        # do ADR) fica literal: nenhuma composição extra entre os componentes.
        hash_paths "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"
        ;;
    *)
        echo "ERROR: flag desconhecida: $FLAG (esperado --paths, --nucleo ou --svc)" >&2
        exit 2
        ;;
esac
