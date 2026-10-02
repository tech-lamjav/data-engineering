import functions_framework
import logging
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

project_root = current_dir
sys.path.insert(0, project_root)

scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)

from src.sync.bq_to_postgres import run_sync
from src.sync.trava import STATUS_OCUPADO
from src.sync.troca import STATUS_TROCA_FALHOU


@functions_framework.http
def sync_bq_to_postgres(request):
    """Sync BigQuery marts -> Supabase Postgres.

    Query params:
        env:    'prd' (default) ou 'dev'. Seleciona qual Supabase project.
        sport:  'nba' (default) ou 'futebol'. Resolve dataset BQ + schema
                Postgres + allowlist (sync.alvo.resolve_alvo_sync).
        tables: 'all' (default) ou CSV de nomes, ex:
                ?sport=futebol&tables=fact_value_opportunities,fact_fixtures
        troca:  CSV de tabelas habilitadas na CARGA POR TROCA (DE#108, ADR 0005). Vazio ou
                ausente (default) = nenhuma: carga no lugar, como antes. Só futebol; nunca
                `fact_odds_snapshot` (até a DE#109). É o workflow quem liga, tabela a tabela
                e por ambiente; desligar = reverter o workflow, sem build de imagem.
        staged: CSV de tabelas habilitadas no caminho STAGED (tabelas com dependente, como as
                `int_futebol_premissas_*`).

    Respostas: 200 sucesso; 409 já há sync do mesmo (sport, env) em andamento (trava de
    sessão no Postgres, DE#107); 500 schema drift, erro ou TROCA FALHA (alguma tabela
    habilitada não conseguiu trocar: o corpo traz `falhas` com os nomes das tabelas e um
    código de motivo; as demais tabelas foram sincronizadas e as falhas mantêm a vigente
    intacta e o estado sem avançar).
    """
    env = request.args.get("env", default="prd")
    sport = request.args.get("sport", default="nba")
    tables = request.args.get("tables", default="all")
    troca = request.args.get("troca", default="")
    staged = request.args.get("staged", default="")
    try:
        result = run_sync(tables=tables, env=env, sport=sport, troca=troca, staged=staged)
        if result["status"] == STATUS_OCUPADO:
            # Trava por (sport, env) com outro sync (DE#107): 409, não 5xx. O workflow
            # trata 409 como "em andamento" (WARNING, fora de failed_services) e o retry
            # padrão do Workflows NÃO repete 409 (só 429/502/503/504/timeout).
            return {
                "status": STATUS_OCUPADO,
                "sport": result["sport"],
                "env": result["env"],
                "message": "já em andamento",
            }, 409
        if result["status"] == "aborted_schema_drift":
            return {
                "status": "aborted_schema_drift",
                "sport": result["sport"],
                "env": result["env"],
                "drift": result["drift"],
            }, 500
        if result["status"] == STATUS_TROCA_FALHOU:
            # Status parcial (DE#108): o corpo carrega os NOMES das tabelas que falharam (e um
            # código de motivo, sem texto do banco). 500: o workflow marca PARTIAL_FAILURE; o
            # retry padrão não repete 500, e uma repetição só gastaria o orçamento de novo.
            return {
                "status": STATUS_TROCA_FALHOU,
                "sport": result["sport"],
                "env": result["env"],
                "falhas": result["falhas"],
                "summary": result.get("summary", {}),
                "synced": result["synced"],
                "avisos": result.get("avisos", []),
            }, 500
        return {
            "status": "success",
            "sport": result["sport"],
            "env": result["env"],
            "summary": result.get("summary", {}),
            "synced": result["synced"],
            "avisos": result.get("avisos", []),
            # DE#106: tamanho do DEV em MiB, medido ao fim do passe DEV. Aditivo: o workflow
            # o lê com map.get (chave ausente não quebra); nulo em PRD ou se a medição falhou.
            "dev_size_mb": result.get("dev_size_mb"),
        }, 200
    except Exception as e:
        # Não ecoar str(e) ao chamador (pode vazar DSN/host/schema do Postgres).
        # O traceback completo fica no Cloud Logging.
        logging.getLogger("sync_bq_to_postgres").error(
            f"Falha no sync (sport={sport}, env={env}): {e}", exc_info=True
        )
        return {"status": "error", "sport": sport, "env": env}, 500
