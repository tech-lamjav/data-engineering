"""Funções auxiliares."""
import re
from datetime import datetime
from typing import Optional, Dict, Any


def parse_date(date_str: Optional[str]) -> Optional[str]:
    """
    Valida e formata uma data para YYYY-MM-DD.
    
    Args:
        date_str: String de data em vários formatos
    
    Returns:
        Data formatada como YYYY-MM-DD ou None
    """
    if not date_str:
        return None
    
    # Tenta vários formatos comuns
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    raise ValueError(f"Data inválida: {date_str}. Use formato YYYY-MM-DD")


def validate_json_structure(data: dict, required_keys: list) -> bool:
    """
    Valida se um dicionário contém as chaves necessárias.
    
    Args:
        data: Dicionário a ser validado
        required_keys: Lista de chaves obrigatórias
    
    Returns:
        True se todas as chaves estão presentes
    """
    return all(key in data for key in required_keys)


def normalize_bigquery_column_name(name: str) -> str:
    """
    Normaliza um nome de coluna para ser compatível com BigQuery.
    
    BigQuery permite apenas:
    - Letras (a-z, A-Z)
    - Números (0-9)
    - Underscores (_)
    - Deve começar com letra ou underscore
    - Máximo de 300 caracteres
    
    Args:
        name: Nome da coluna original
    
    Returns:
        Nome normalizado compatível com BigQuery
    """
    if not name:
        return "_"
    
    # Substitui caracteres inválidos por underscore
    # Remove ou substitui: parênteses, hífens, espaços, etc.
    normalized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    
    # Remove underscores múltiplos consecutivos
    normalized = re.sub(r'_+', '_', normalized)
    
    # Remove underscores no início e fim
    normalized = normalized.strip('_')
    
    # Se começar com número, adiciona underscore no início
    if normalized and normalized[0].isdigit():
        normalized = '_' + normalized
    
    # Se estiver vazio após normalização, usa valor padrão
    if not normalized:
        normalized = '_'
    
    # Limita a 300 caracteres (limite do BigQuery)
    if len(normalized) > 300:
        normalized = normalized[:300].rstrip('_')
    
    return normalized


def normalize_dict_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza todas as chaves de um dicionário (recursivamente) para serem compatíveis com BigQuery.
    
    Args:
        data: Dicionário com chaves que podem precisar normalização
    
    Returns:
        Dicionário com chaves normalizadas
    """
    if not isinstance(data, dict):
        return data
    
    normalized = {}
    for key, value in data.items():
        # Normaliza a chave
        new_key = normalize_bigquery_column_name(key)
        
        # Se o valor for um dicionário, normaliza recursivamente
        if isinstance(value, dict):
            normalized[new_key] = normalize_dict_keys(value)
        # Se o valor for uma lista, normaliza cada item se for dicionário
        elif isinstance(value, list):
            normalized[new_key] = [
                normalize_dict_keys(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            normalized[new_key] = value
    
    return normalized

