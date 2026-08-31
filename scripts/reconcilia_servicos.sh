#!/bin/bash
# Reconciliação entre os serviços Cloud Run VIVOS e o manifesto de procedência
# (`procedencia_servicos.sh`) — DE #53.
#
# `checa_deriva_servicos.sh` (DE #50/#51/#52) confere deriva DENTRO do universo do
# manifesto. Esse universo é uma lista escrita à mão, e um detector cujo universo é
# uma lista é CEGO para tudo que não está nela — essa cegueira não faz barulho
# nenhum, que é a definição do problema da #44 aplicada ao próprio conserto. Esta
# reconciliação fecha essa armadilha de segunda ordem comparando o universo
# DECLARADO (o manifesto) com o universo REAL (`gcloud run services list`).
#
# Uso:
#   scripts/reconcilia_servicos.sh
#
# Não aceita alvo específico como `checa_deriva_servicos.sh` — reconciliação é sobre
# os DOIS universos inteiros; "reconciliar um serviço só" não é uma pergunta que faz
# sentido (o objetivo é achar o que NENHUMA lista sabia que precisava checar).
#
# DOIS ESTADOS, cada um notícia diferente (nenhum dos dois é "deriva" nem "sem
# carimbo" — são sobre o universo, não sobre o conteúdo de um serviço já conhecido):
#   órfão vivo              serviço RODA no Cloud Run e não está no manifesto — nada
#                            o vigia, ninguém sabe de que código
#   nunca deployado          o manifesto promete cobertura que não existe no Cloud Run
#
# Caso concreto que motivou (ver comentário em `deploy_cloud_run.sh` e
# `procedencia_servicos.sh`): `cloud_run/extract_player_props/` é um diretório órfão,
# substituído pelas 3 variantes por vendor. A varredura de 31/08/2026
# (`gcloud run services list --region=us-east1 --project=smartbetting-dados`) confirma
# que os 29 vivos são EXATAMENTE os 29 do manifesto — `extract-player-props` está
# PROVADAMENTE AUSENTE do Cloud Run, não é órfão vivo. Resolvido, não contornado.
#
# Ver docs/adr/0001-carimbo-de-procedencia-dos-servicos-cloud-run.md (decisão "Cinco
# estados, e o quinto é novo").

set -uo pipefail

GCP_PROJECT_ID="${GCP_PROJECT_ID:-smartbetting-dados}"
GCP_REGION="${GCP_REGION:-us-east1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Serviços vivos no MESMO projeto/região que não fazem parte deste pipeline — cada
# entrada carrega o motivo por escrito. NUNCA um filtro silencioso de nome (ex.: "tudo
# que começa com extract- é nosso"): um filtro de padrão erra tanto para dentro
# (esconde um órfão de verdade cujo nome combina) quanto para fora (acusa um serviço
# nosso com nome atípico). Formato: "nome-do-servico:motivo".
#
# Vazio hoje: a varredura de 31/08/2026 (`gcloud run services list`) confirmou que os
# 29 serviços vivos no projeto/região são EXATAMENTE os 29 do manifesto — nenhum
# serviço vivo fora do pipeline foi encontrado. Se isso mudar (alguém sobe um serviço
# de outro time no mesmo projeto/região), acrescente aqui — não pule pelo nome.
ISENTOS=()

is_isento() {
    local nome=$1
    local entry
    # `${ISENTOS[@]:-}` e não `${ISENTOS[@]}`: bash 3.2 (macOS) trata array VAZIO
    # como variável não setada sob `set -u` e aborta — o mesmo acidente de versão que
    # o achado do /code-review pegou no `checa_deriva_servicos.sh` (DE #52). ISENTOS
    # está vazio por padrão (ver comentário acima), então este é o caso comum, não a
    # exceção.
    for entry in "${ISENTOS[@]:-}"; do
        [ -z "$entry" ] && continue
        [[ "$entry" == "$nome:"* ]] && return 0
    done
    return 1
}

# LIVE: fail-closed. Se `gcloud run services list` falhar (rede/IAM), o certo é
# ABORTAR — reportar "reconciliado" sem ter conseguido listar o universo real seria
# fingir que a checagem rodou. Mesma regra do resto do mecanismo (erro de leitura ≠
# "tudo em dia").
tmp_err=$(mktemp)
LIVE_RAW=$(gcloud run services list \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT_ID" \
    --format="value(metadata.name)" 2>"$tmp_err")
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "ERRO: nao consegui listar os servicos Cloud Run vivos (rede ou IAM):" >&2
    cat "$tmp_err" >&2
    rm -f "$tmp_err"
    exit 2
fi
rm -f "$tmp_err"

LIVE=()
while IFS= read -r svc; do
    [ -z "$svc" ] && continue
    LIVE+=("$svc")
done <<< "$LIVE_RAW"

# MANIFESTO: mesmo fail-closed do `checa_deriva_servicos.sh` — `--list-servicos`
# falhar ou vir vazio aborta, não reporta "tudo reconciliado" sem ter checado nada.
MANIFESTO_RAW=$("$SCRIPT_DIR/procedencia_servicos.sh" --list-servicos)
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "ERRO: nao consegui obter a lista de servicos do manifesto (procedencia_servicos.sh --list-servicos falhou)." >&2
    exit 2
fi
if [ -z "$MANIFESTO_RAW" ]; then
    echo "ERRO: procedencia_servicos.sh --list-servicos nao devolveu nenhum servico." >&2
    exit 2
fi
MANIFESTO=()
while IFS= read -r svc; do
    [ -z "$svc" ] && continue
    MANIFESTO+=("$svc")
done <<< "$MANIFESTO_RAW"

no_manifesto() {
    local nome=$1
    local m
    for m in "${MANIFESTO[@]:-}"; do
        [ -z "$m" ] && continue
        [ "$m" = "$nome" ] && return 0
    done
    return 1
}

no_live() {
    local nome=$1
    local l
    for l in "${LIVE[@]:-}"; do
        [ -z "$l" ] && continue
        [ "$l" = "$nome" ] && return 0
    done
    return 1
}

ORFAOS_VIVOS=()
for svc in "${LIVE[@]:-}"; do
    [ -z "$svc" ] && continue
    if ! no_manifesto "$svc" && ! is_isento "$svc"; then
        ORFAOS_VIVOS+=("$svc")
    fi
done

NUNCA_DEPLOYADOS=()
for svc in "${MANIFESTO[@]:-}"; do
    [ -z "$svc" ] && continue
    if ! no_live "$svc"; then
        NUNCA_DEPLOYADOS+=("$svc")
    fi
done

echo "Reconciliacao Cloud Run x manifesto — ${#LIVE[@]} vivos, ${#MANIFESTO[@]} no manifesto"
echo

if [ "${#ORFAOS_VIVOS[@]}" -gt 0 ]; then
    for svc in "${ORFAOS_VIVOS[@]}"; do
        echo "  ● ${svc}: ORFAO VIVO — roda no Cloud Run e NAO esta no manifesto"
        echo "    nenhum detector vigia este servico; codigo desconhecido"
    done
fi

if [ "${#NUNCA_DEPLOYADOS[@]}" -gt 0 ]; then
    for svc in "${NUNCA_DEPLOYADOS[@]}"; do
        echo "  ● ${svc}: NUNCA DEPLOYADO — esta no manifesto mas nao existe no Cloud Run"
        echo "    o manifesto promete cobertura que nao existe"
    done
fi

echo
if [ "${#ORFAOS_VIVOS[@]}" -gt 0 ] || [ "${#NUNCA_DEPLOYADOS[@]}" -gt 0 ]; then
    echo "RESULTADO: ${#ORFAOS_VIVOS[@]} orfao(s) vivo(s), ${#NUNCA_DEPLOYADOS[@]} nunca deployado(s)."
    exit 1
fi

echo "RESULTADO: reconciliado — os vivos e o manifesto sao o mesmo conjunto."
