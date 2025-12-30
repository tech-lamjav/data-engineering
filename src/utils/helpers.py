"""Funções auxiliares."""
from datetime import datetime
from typing import Optional


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

