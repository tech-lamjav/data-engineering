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

# Como o handler de odds pré-jogo, chamamos o extractor direto p/ devolver `saved_count`
# — o poll workflow_futebol_predictions usa esse valor pra decidir se vale rodar o dbt. A
# lógica de negócio continua 100% em src/ (PredictionsExtractor).
from src.extractors.predictions_extractor import PredictionsExtractor


@functions_framework.http
def extract_predictions(request):
    """API-Football /predictions pipeline (snapshot pré-jogo, janela T-2h).

    Sem query params: uma passada cobre a janela (o extractor bucketa cada jogo NS pela
    proximidade do kickoff). Retorna `saved_count` (nº de snapshots gravados) p/ o gate
    do workflow.
    """
    try:
        paths = PredictionsExtractor().extract_and_save()
        return {
            "status": "success",
            "saved_count": len(paths),
            "message": "Pipeline executed successfully",
        }, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
