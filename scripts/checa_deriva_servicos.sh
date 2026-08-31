#!/bin/bash
# Detector de DERIVA para Cloud Run Services — irmão do `checa_deriva.sh` do
# analytics-engineering, que cobre só os dois Cloud Run Jobs dbt (`dbt-futebol`,
# `dbt-nba`). Os 29 serviços deste repo são um mecanismo separado (ver decisão B do
# ADR 0001 deste repo): dono e escritor do carimbo são o `deploy_cloud_run.sh` e o
# `procedencia_servicos.sh`, os dois AQUI.
#
# Uso:
#   scripts/checa_deriva_servicos.sh                 # checa os alvos cobertos
#   scripts/checa_deriva_servicos.sh extract-games   # checa um alvo específico
#
# QUATRO ESTADOS (o quinto, "órfão" — serviço vivo fora do manifesto —, é a DE #53):
#   em dia        hash combinado do serviço bate com o hash local
#   deriva        hash combinado diverge — o serviço RODA código diferente do master.
#                 Sub-classificado por CAUSA usando os componentes NUCLEO/SVC que o
#                 `deploy_cloud_run.sh` grava à parte (decisão A do ADR): núcleo
#                 compartilhado, módulo próprio do serviço, ou os dois
#   sem carimbo   o serviço existe e não tem PROCEDENCIA_HASH nenhum — nunca passou
#                 pelo `deploy_cloud_run.sh` novo. FAIL-CLOSED: conta como vermelho,
#                 mas com remédio e texto diferentes de "deriva" (não é a mesma notícia)
#   erro de leitura   API/IAM falhou (serviço inexistente, sem permissão), ou o hash
#                 local não pôde ser calculado. NÃO é deriva — é o detector não
#                 sabendo, e reportar como deriva esconderia um problema de acesso
#                 (ou de manifesto) atrás de um problema de código
#
# APRESENTAÇÃO POR CAUSA COMUM (DE #52), não estrutura: o carimbo continua sendo por
# serviço — um serviço deriva sozinho quando só o `main.py` dele muda. O que este
# script faz é AGRUPAR na tela os serviços cujo NÚCLEO divergiu (a mesma causa: um
# commit em `src/config.py`/`src/clients/`/etc. deriva os 29 juntos, ~a cada 2,4 dias
# medido) numa linha só, em vez de repetir a mesma notícia 29 vezes — um alarme que
# dispara 29 vezes por commit rotineiro treina o time a ignorá-lo. Serviço verde não
# imprime linha nenhuma: verde é ausência de notícia.
#
# Cada NUCLEO local NÃO é idêntico entre os 29 mesmo quando todos estão em dia — o
# manifesto inclui o `requirements.txt` PRÓPRIO de cada serviço no componente núcleo
# (ver `procedencia_servicos.sh`). O agrupamento não compara VALORES de hash entre
# serviços; agrupa quem tem a mesma CATEGORIA de divergência ("o componente núcleo
# deste serviço diverge do que está gravado nele").
#
# Ver docs/adr/0001-carimbo-de-procedencia-dos-servicos-cloud-run.md

set -uo pipefail

GCP_PROJECT_ID="${GCP_PROJECT_ID:-smartbetting-dados}"
GCP_REGION="${GCP_REGION:-us-east1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Sem argumento, os alvos são os 29 do manifesto (`procedencia_servicos.sh
# --list-servicos`) — fonte única, os dois scripts evoluem juntos (DE #51). Isto
# reflete só o QUINTO estado ainda não coberto por este arquivo: serviço VIVO no Cloud
# Run e ausente do manifesto continua invisível aqui de propósito — é a reconciliação
# da DE #53, um mecanismo diferente (lista vs. universo real).
if [ $# -gt 0 ]; then
    ALVOS=("$@")
else
    # Fail-closed: se `--list-servicos` falhar ou nao imprimir nada, o loop abaixo
    # rodaria zero vezes e o script sairia dizendo "todos os alvos em dia" sem ter
    # checado UM sequer — o oposto do que um detector fail-closed pode fazer. Captura
    # em variavel (nao em process substitution) justamente para poder checar o exit
    # code do comando e o conteudo antes de popular ALVOS.
    LISTA_SERVICOS=$("$SCRIPT_DIR/procedencia_servicos.sh" --list-servicos) || {
        echo "ERRO: nao consegui obter a lista de servicos do manifesto (procedencia_servicos.sh --list-servicos falhou)." >&2
        exit 1
    }
    if [ -z "$LISTA_SERVICOS" ]; then
        echo "ERRO: procedencia_servicos.sh --list-servicos nao devolveu nenhum servico." >&2
        exit 1
    fi
    ALVOS=()
    while IFS= read -r svc; do
        [ -z "$svc" ] && continue
        ALVOS+=("$svc")
    done <<< "$LISTA_SERVICOS"
fi

# Passe 1: coleta o estado de cada alvo (rede/cálculo local incluídos) em três arrays
# paralelos — bash 3.x não tem array associativo. Nada é impresso aqui: a apresentação
# agrupada (passe 2) precisa do total antes de decidir se agrupa.
R_SERVICO=()
R_ESTADO=()   # em-dia | deriva-nucleo | deriva-svc | deriva-ambos | sem-carimbo | erro
R_DETALHE=()  # mensagem curta (erro) ou vazio

# Stderr de cada chamada vai para um arquivo temporário, NUNCA misturado (`2>&1`) ao
# stdout que é comparado como hash — um aviso do git (CRLF, dubious ownership, etc.) no
# caminho de SUCESSO contaminaria a string comparada e viraria falso positivo de
# deriva, o oposto do que a DE #52 existe para evitar. Arquivo (não `2>&1`) é o mesmo
# padrão que `procedencia_servicos.sh` já usa para o mesmo motivo.
for SERVICO in "${ALVOS[@]}"; do
    # Progresso vai pro STDERR, não pro relatório (stdout) — a apresentação agrupada
    # (passe 2) é o que precisa caber numa tela; isto aqui é só para um operador ver
    # em qual serviço o script está preso se um `gcloud describe` travar no meio dos 29.
    echo "checando ${SERVICO}..." >&2

    tmp_err=$(mktemp)
    # `--todos`: as 3 linhas (combinado/nucleo/svc) numa chamada só, em vez de 3
    # processos repetindo a checagem de existência das paths e o `git ls-files` — DE #52.
    TRES=$("$SCRIPT_DIR/procedencia_servicos.sh" "$SERVICO" --todos 2>"$tmp_err")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        R_SERVICO+=("$SERVICO"); R_ESTADO+=("erro"); R_DETALHE+=("hash local: $(cat "$tmp_err")")
        rm -f "$tmp_err"
        continue
    fi
    rm -f "$tmp_err"
    ESPERADO=$(echo "$TRES" | sed -n '1p')
    ESPERADO_NUCLEO=$(echo "$TRES" | sed -n '2p')
    ESPERADO_SVC=$(echo "$TRES" | sed -n '3p')

    # `--format=json` inteiro, e não `--format="json(caminho)"`: quando o caminho não
    # resolve, o segundo devolve o literal `null` em vez do esqueleto aninhado e o
    # parser quebra — um detector que acusa deriva porque o próprio parser quebrou é um
    # detector falso. Por isso "erro de leitura" é estado à parte, nunca deriva.
    tmp_err=$(mktemp)
    SVC_JSON=$(gcloud run services describe "$SERVICO" \
        --region="$GCP_REGION" \
        --project="$GCP_PROJECT_ID" \
        --format=json 2>"$tmp_err")
    rc=$?
    if [ "$rc" -ne 0 ]; then
        R_SERVICO+=("$SERVICO"); R_ESTADO+=("erro")
        R_DETALHE+=("leitura do servico: $(cat "$tmp_err")")
        rm -f "$tmp_err"
        continue
    fi
    rm -f "$tmp_err"

    # Um nível a menos de aninhamento que o job: serviço não tem o `template` extra do
    # task template. `env` some do JSON quando o serviço nunca recebeu env var nenhuma
    # (o estado inicial dos 29 antes do primeiro deploy carimbado) — ausência da chave é
    # ausência do carimbo, tratada como "sem carimbo" abaixo. Os quatro nomes lidos aqui
    # são os quatro que `deploy_cloud_run.sh::carimbo_env_vars` grava juntos, sempre no
    # mesmo `--set-env-vars` — por isso testar só PROCEDENCIA_HASH já cobre "carimbo
    # ausente" (os quatro nascem e somem juntos).
    LIDO=$(echo "$SVC_JSON" | python3 -c '
import json, sys

doc = json.load(sys.stdin)
spec = doc["spec"]["template"]["spec"]
env = {v.get("name"): v.get("value") or "" for v in (spec["containers"][0].get("env") or [])}
print(env.get("PROCEDENCIA_HASH", ""))
print(env.get("PROCEDENCIA_HASH_NUCLEO", ""))
print(env.get("PROCEDENCIA_HASH_SVC", ""))
')
    if [ $? -ne 0 ]; then
        R_SERVICO+=("$SERVICO"); R_ESTADO+=("erro"); R_DETALHE+=("nao consegui interpretar a resposta do servico")
        continue
    fi
    ENCONTRADO=$(echo "$LIDO" | sed -n '1p')
    ENCONTRADO_NUCLEO=$(echo "$LIDO" | sed -n '2p')
    ENCONTRADO_SVC=$(echo "$LIDO" | sed -n '3p')

    if [ -z "$ENCONTRADO" ]; then
        R_SERVICO+=("$SERVICO"); R_ESTADO+=("sem-carimbo"); R_DETALHE+=("")
    elif [ "$ENCONTRADO" = "$ESPERADO" ]; then
        R_SERVICO+=("$SERVICO"); R_ESTADO+=("em-dia"); R_DETALHE+=("")
    else
        nucleo_diverge=0
        svc_diverge=0
        [ "$ENCONTRADO_NUCLEO" != "$ESPERADO_NUCLEO" ] && nucleo_diverge=1
        [ "$ENCONTRADO_SVC" != "$ESPERADO_SVC" ] && svc_diverge=1
        R_SERVICO+=("$SERVICO")
        # Detalhe (esperado|encontrado, combinado) só serve às linhas INDIVIDUAIS —
        # svc-only e ambos. A linha agrupada do núcleo não imprime valor nenhum de
        # propósito (é o que a DE #52 existe para não repetir 29x).
        if [ "$nucleo_diverge" -eq 1 ] && [ "$svc_diverge" -eq 1 ]; then
            R_ESTADO+=("deriva-ambos")
        elif [ "$nucleo_diverge" -eq 1 ]; then
            R_ESTADO+=("deriva-nucleo")
        else
            # Combinado divergiu mas nenhum componente sozinho? Só acontece se o
            # combinado foi lido/calculado de forma inconsistente com os componentes —
            # fail-closed: trata como deriva de módulo (não deixa cair em "em dia").
            R_ESTADO+=("deriva-svc")
        fi
        R_DETALHE+=("${ESPERADO}|${ENCONTRADO}")
    fi
done

# Passe 2: agrupa por causa e imprime. Contagens primeiro (cabeçalho), texto depois.
total=${#R_SERVICO[@]}
em_dia=0; sem_carimbo=0; erros=0
deriva_nucleo=0; deriva_svc=0; deriva_ambos=0

i=0
while [ "$i" -lt "$total" ]; do
    case "${R_ESTADO[$i]}" in
        em-dia) em_dia=$((em_dia + 1)) ;;
        sem-carimbo) sem_carimbo=$((sem_carimbo + 1)) ;;
        erro) erros=$((erros + 1)) ;;
        deriva-nucleo) deriva_nucleo=$((deriva_nucleo + 1)) ;;
        deriva-svc) deriva_svc=$((deriva_svc + 1)) ;;
        deriva-ambos) deriva_ambos=$((deriva_ambos + 1)) ;;
    esac
    i=$((i + 1))
done

nucleo_grupo=$((deriva_nucleo + deriva_ambos))
deriva_total=$((deriva_nucleo + deriva_svc + deriva_ambos))

echo "Deriva de servicos Cloud Run — ${total} alvos, ${deriva_total} em deriva, ${sem_carimbo} sem carimbo, ${erros} erro(s) de leitura"
echo

if [ "$nucleo_grupo" -gt 0 ]; then
    echo "  ● nucleo compartilhado derivou — ${nucleo_grupo} servico(s)"
    echo "    remedio: ./scripts/deploy_cloud_run.sh          (~60 min)"
fi

i=0
while [ "$i" -lt "$total" ]; do
    svc="${R_SERVICO[$i]}"
    esperado_svc="${R_DETALHE[$i]%%|*}"
    encontrado_svc="${R_DETALHE[$i]#*|}"
    case "${R_ESTADO[$i]}" in
        deriva-svc)
            echo "  ● ${svc}: modulo proprio derivou (nucleo em dia)"
            echo "    no servico: ${encontrado_svc}"
            echo "    no repo:    ${esperado_svc}"
            echo "    remedio: ./scripts/deploy_cloud_run.sh ${svc}"
            ;;
        deriva-ambos)
            echo "  ● ${svc}: modulo proprio tambem derivou"
            echo "    no servico: ${encontrado_svc}"
            echo "    no repo:    ${esperado_svc}"
            ;;
    esac
    i=$((i + 1))
done

i=0
while [ "$i" -lt "$total" ]; do
    svc="${R_SERVICO[$i]}"
    if [ "${R_ESTADO[$i]}" = "sem-carimbo" ]; then
        echo "  ● ${svc}: SEM CARIMBO (nunca redeployado com o script novo)"
        echo "    remedio: ./scripts/deploy_cloud_run.sh ${svc}"
    fi
    i=$((i + 1))
done

i=0
while [ "$i" -lt "$total" ]; do
    svc="${R_SERVICO[$i]}"
    if [ "${R_ESTADO[$i]}" = "erro" ]; then
        echo "  ● ${svc}: erro de leitura"
        echo "${R_DETALHE[$i]}" | sed 's/^/      /'
    fi
    i=$((i + 1))
done

echo
if [ "$deriva_total" -gt 0 ] || [ "$sem_carimbo" -gt 0 ] || [ "$erros" -gt 0 ]; then
    echo "RESULTADO: ${deriva_total} em deriva, ${sem_carimbo} sem carimbo, ${erros} erro(s) de leitura."
    echo
    echo "Deriva ou carimbo ausente significam que producao pode estar rodando codigo"
    echo "diferente do master, sem que nada mais acuse — a mesma classe de bug que os"
    echo "13 servicos de NBA tiveram por dois meses (25/06 a 24/08/2026) antes deste"
    echo "detector existir."
    exit 1
fi

echo "RESULTADO: todos os alvos em dia."
