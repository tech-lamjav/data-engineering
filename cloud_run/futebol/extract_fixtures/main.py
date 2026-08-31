import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Cloud Run: src/ e scripts/ são copiados pra cá no deploy.
# Scripts de futebol vivem em scripts/futebol/ — adicionar essa subpasta ao path
# pra manter o import `from extract_fixtures import main` simples.
project_root = current_dir
sys.path.insert(0, project_root)
scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)
sys.path.insert(0, os.path.join(scripts_dir, "futebol"))

from extract_fixtures import main as extract_main
# DE#60: mode=live NÃO passa pelo main() do script (que só ecoa status/mode, sem contagem)
# — chama o extractor direto, como extract_fixture_lineups/main.py, porque o workflow novo
# (poll de alta frequência) precisa de saved_count para decidir se vale rodar o dbt.
from src.extractors.fixtures_extractor import FixturesExtractor


@functions_framework.http
def extract_fixtures(request):
    """API-Football /fixtures pipeline.

    Query params:
        mode: "current" (default, ano corrente) | "backfill" (anos anteriores) |
              "live" (DE#60 — poll de alta frequência, sem varrer a temporada; devolve
              saved_count)
    """
    mode = request.args.get("mode", "current")

    if mode == "live":
        try:
            extractor = FixturesExtractor(mode="live")
            extractor.extract_and_save()
            return {
                "status": "success",
                "mode": mode,
                "saved_count": extractor.last_fresh_count,
                "message": "Pipeline executed successfully",
            }, 200
        except Exception as e:
            return {"status": "error", "mode": mode, "error": str(e)}, 500

    try:
        # O script lê o modo de FIXTURES_MODE dentro do main(). Setamos a env var
        # imediatamente antes da chamada e restauramos no finally, evitando que o
        # valor vaze para requisições subsequentes na mesma instância (warm).
        # (Eliminar a mutação por completo exigiria main() aceitar `mode` — fora deste escopo.)
        _prev_mode = os.environ.get("FIXTURES_MODE")
        os.environ["FIXTURES_MODE"] = mode
        try:
            result_code = extract_main()
        finally:
            if _prev_mode is None:
                os.environ.pop("FIXTURES_MODE", None)
            else:
                os.environ["FIXTURES_MODE"] = _prev_mode
        if result_code == 0:
            return {"status": "success", "mode": mode, "message": "Pipeline executed successfully"}, 200
        else:
            return {"status": "error", "mode": mode, "message": "Pipeline execution failed"}, 500
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
