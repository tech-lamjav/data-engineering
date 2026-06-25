import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Cloud Run: src/ e scripts/ são copiados pra cá no deploy.
project_root = current_dir
sys.path.insert(0, project_root)
scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)
sys.path.insert(0, os.path.join(scripts_dir, "futebol"))

# Diferente dos handlers de statistics/events (que chamam o main() do script e só ecoam
# status), aqui chamamos o extractor direto p/ devolver `saved_count` — o workflow
# pré-jogo (workflow_futebol_lineups) usa esse valor pra decidir se vale rodar o dbt.
# A lógica de negócio continua 100% em src/ (FixtureLineupsExtractor).
from src.extractors.fixture_lineups_extractor import FixtureLineupsExtractor


@functions_framework.http
def extract_fixture_lineups(request):
    """API-Football /fixtures/lineups pipeline.

    Query params:
        mode: "current" (default) | "backfill" (ambos pós-jogo, gravam _real) |
              "pregame" (pré-jogo ~T-30min, grava _confirmed)

    Retorna `saved_count` (nº de arquivos gravados) p/ o gate do workflow pré-jogo.
    """
    try:
        mode = request.args.get("mode", "current")
        paths = FixtureLineupsExtractor(mode=mode).extract_and_save()
        return {
            "status": "success",
            "mode": mode,
            "saved_count": len(paths),
            "message": "Pipeline executed successfully",
        }, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
