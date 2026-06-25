import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Cloud Run: src/ e scripts/ são copiados pra cá no deploy.
# Scripts de futebol vivem em scripts/futebol/ — adicionar essa subpasta ao path
# pra manter o import `from extract_fixture_statistics import main` simples.
project_root = current_dir
sys.path.insert(0, project_root)
scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)
sys.path.insert(0, os.path.join(scripts_dir, "futebol"))

from extract_fixture_statistics import main as extract_main


@functions_framework.http
def extract_fixture_statistics(request):
    """API-Football /fixtures/statistics pipeline.

    Query params:
        mode: "current" (default, ano corrente) | "backfill" (anos anteriores)
    """
    try:
        mode = request.args.get("mode", "current")
        # O script lê o modo de FIXTURE_STATISTICS_MODE dentro do main(). Setamos a env var
        # imediatamente antes da chamada e restauramos no finally, evitando que o
        # valor vaze para requisições subsequentes na mesma instância (warm).
        # (Eliminar a mutação por completo exigiria main() aceitar `mode` — fora deste escopo.)
        _prev_mode = os.environ.get("FIXTURE_STATISTICS_MODE")
        os.environ["FIXTURE_STATISTICS_MODE"] = mode
        try:
            result_code = extract_main()
        finally:
            if _prev_mode is None:
                os.environ.pop("FIXTURE_STATISTICS_MODE", None)
            else:
                os.environ["FIXTURE_STATISTICS_MODE"] = _prev_mode
        if result_code == 0:
            return {"status": "success", "mode": mode, "message": "Pipeline executed successfully"}, 200
        else:
            return {"status": "error", "mode": mode, "message": "Pipeline execution failed"}, 500
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
