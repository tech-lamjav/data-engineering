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
# Então o project_root é o próprio current_dir
project_root = current_dir
sys.path.insert(0, project_root)

# Adicionar diretório scripts ao path
scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)

# Importar a função main do script original
from extract_player_injuries import main as extract_main


@functions_framework.http
def extract_player_injuries(request):
    """NBA Player Injuries Pipeline"""
    try:
        result_code = extract_main()
        if result_code == 0:
            return {"status": "success", "message": "Pipeline executed successfully"}
        else:
            return {"status": "error", "message": "Pipeline execution failed"}, 500
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500


