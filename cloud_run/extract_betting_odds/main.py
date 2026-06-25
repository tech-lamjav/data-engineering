import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# No Cloud Run, após o deploy, a estrutura é:
# current_dir/
#   ├── main.py
#   ├── requirements.txt
#   ├── src/
#   └── scripts/
project_root = current_dir
sys.path.insert(0, project_root)

scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)

from extract_betting_odds import main as extract_main


@functions_framework.http
def extract_betting_odds(request):
    """NBA Betting Odds Pipeline"""
    try:
        result_code = extract_main()
        if result_code == 0:
            return {"status": "success", "message": "Pipeline executed successfully"}, 200
        else:
            return {"status": "error", "message": "Pipeline execution failed"}, 500
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
