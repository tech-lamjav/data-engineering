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
#   em dia        hash do serviço bate com o hash local
#   deriva        hash diverge — o serviço existe e RODA código diferente do master
#   sem carimbo   o serviço existe e não tem PROCEDENCIA_HASH nenhum — nunca passou
#                 pelo `deploy_cloud_run.sh` novo. FAIL-CLOSED: conta como vermelho,
#                 mas com remédio e texto diferentes de "deriva" (não é a mesma notícia)
#   erro de leitura   API/IAM falhou (serviço inexistente, sem permissão). NÃO é
#                 deriva — é o detector não sabendo, e reportar como deriva esconderia
#                 um problema de acesso atrás de um problema de código
#
# TRACER BULLET (DE #50): a tabela de alvos abaixo tem só `extract-games` — os outros
# 28 nascem sem cobertura (nem verde nem vermelho: não aparecem) até a DE #51.
#
# Ver docs/adr/0001-carimbo-de-procedencia-dos-servicos-cloud-run.md

set -uo pipefail

GCP_PROJECT_ID="${GCP_PROJECT_ID:-smartbetting-dados}"
GCP_REGION="${GCP_REGION:-us-east1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Tabela declarativa de alvos cobertos por este detector. Acrescentar um alvo aqui só
# faz sentido depois de declará-lo no `procedencia_servicos.sh` — os dois evoluem
# juntos (DE #51).
if [ $# -gt 0 ]; then
    ALVOS=("$@")
else
    ALVOS=(extract-games)
fi

derivas=0
sem_carimbo=0
erros=0

for SERVICO in "${ALVOS[@]}"; do
    echo "── ${SERVICO}"

    ESPERADO=$("$SCRIPT_DIR/procedencia_servicos.sh" "$SERVICO") || {
        echo "   ERRO: nao consegui calcular o hash local de ${SERVICO}"
        erros=$((erros + 1))
        continue
    }

    # `--format=json` inteiro, e não `--format="json(caminho)"`: quando o caminho não
    # resolve, o segundo devolve o literal `null` em vez do esqueleto aninhado e o
    # parser quebra — um detector que acusa deriva porque o próprio parser quebrou é um
    # detector falso. Por isso "erro de leitura" é estado à parte, nunca deriva.
    SVC_JSON=$(gcloud run services describe "$SERVICO" \
        --region="$GCP_REGION" \
        --project="$GCP_PROJECT_ID" \
        --format=json 2>&1) || {
        echo "   ERRO: nao consegui ler o servico ${SERVICO} (inexistente ou IAM negado):"
        echo "$SVC_JSON" | sed 's/^/     /'
        erros=$((erros + 1))
        continue
    }

    # Um nível a menos de aninhamento que o job: serviço não tem o `template` extra do
    # task template. `env` some do JSON quando o serviço nunca recebeu env var nenhuma
    # (o estado inicial dos 29 antes do primeiro deploy carimbado) — ausência da chave é
    # ausência do carimbo, tratada como "sem carimbo" pelo chamador.
    ENCONTRADO=$(echo "$SVC_JSON" | python3 -c '
import json, sys

doc = json.load(sys.stdin)
spec = doc["spec"]["template"]["spec"]
for var in spec["containers"][0].get("env") or []:
    if var.get("name") == "PROCEDENCIA_HASH":
        print(var.get("value") or "")
        break
') || {
        echo "   ERRO: nao consegui interpretar a resposta do servico ${SERVICO}"
        erros=$((erros + 1))
        continue
    }

    if [ -z "$ENCONTRADO" ]; then
        echo "   ❌ SEM CARIMBO: o servico nao tem PROCEDENCIA_HASH."
        echo "      Nunca foi deployado com o script novo (ou o carimbo foi removido)."
        echo "      Rode: ./scripts/deploy_cloud_run.sh ${SERVICO}"
        sem_carimbo=$((sem_carimbo + 1))
    elif [ "$ENCONTRADO" != "$ESPERADO" ]; then
        echo "   ❌ DERIVA: o servico nao corresponde ao master."
        echo "      no servico: ${ENCONTRADO}"
        echo "      no repo:    ${ESPERADO}"
        echo "      Rode: ./scripts/deploy_cloud_run.sh ${SERVICO}"
        derivas=$((derivas + 1))
    else
        echo "   ✅ em dia (${ESPERADO})"
    fi
done

echo
if [ "$derivas" -gt 0 ] || [ "$sem_carimbo" -gt 0 ] || [ "$erros" -gt 0 ]; then
    echo "RESULTADO: ${derivas} em deriva, ${sem_carimbo} sem carimbo, ${erros} erro(s) de leitura."
    echo
    echo "Deriva ou carimbo ausente significam que producao pode estar rodando codigo"
    echo "diferente do master, sem que nada mais acuse — a mesma classe de bug que os"
    echo "13 servicos de NBA tiveram por dois meses (25/06 a 24/08/2026) antes deste"
    echo "detector existir."
    exit 1
fi

echo "RESULTADO: todos os alvos em dia."
