import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Cloud Run: src/ e scripts/ são copiados pra cá no deploy.
# Scripts de futebol vivem em scripts/futebol/ — adicionar essa subpasta ao path
# pra manter o import `from extract_injuries import main` simples.
project_root = current_dir
sys.path.insert(0, project_root)
scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)
sys.path.insert(0, os.path.join(scripts_dir, "futebol"))

from extract_injuries import main as extract_main
# Modo pregame chama o extractor direto p/ devolver `saved_count` (gate do
# workflow_futebol_injuries), como o handler de predictions/odds. A lógica de negócio
# continua 100% em src/ (InjuriesExtractor).
from src.extractors.injuries_extractor import InjuriesExtractor


@functions_framework.http
def extract_injuries(request):
    """API-Football /injuries pipeline.

    Query params:
        mode: "current" (default, season-log diário) | "backfill" (anos anteriores)
              | "pregame" (coleta FORWARD-ONLY por fixture dos jogos NS futuros, S7)

    pregame retorna `saved_count` (nº de snapshots gravados) p/ o gate do workflow;
    current/backfill retornam o status do script (season-log date-stampado).
    """
    try:
        mode = request.args.get("mode", "current")

        if mode == "pregame":
            paths = InjuriesExtractor(mode="pregame").extract_and_save()
            return {
                "status": "success",
                "mode": mode,
                "saved_count": len(paths),
                "message": "Pipeline executed successfully",
            }, 200

        # current/backfill: o script lê o modo de INJURIES_MODE dentro do main(). Setamos a env var
        # imediatamente antes da chamada e restauramos no finally, evitando que o
        # valor vaze para requisições subsequentes na mesma instância (warm).
        # (Eliminar a mutação por completo exigiria main() aceitar `mode` — fora deste escopo.)
        _prev_mode = os.environ.get("INJURIES_MODE")
        os.environ["INJURIES_MODE"] = mode
        try:
            result_code = extract_main()
        finally:
            if _prev_mode is None:
                os.environ.pop("INJURIES_MODE", None)
            else:
                os.environ["INJURIES_MODE"] = _prev_mode
        if result_code == 0:
            return {"status": "success", "mode": mode, "message": "Pipeline executed successfully"}, 200
        else:
            return {"status": "error", "mode": mode, "message": "Pipeline execution failed"}, 500
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
