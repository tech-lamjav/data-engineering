"""Extractor para props de jogadores."""
from typing import Dict, Any, Optional, List
from datetime import datetime
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PlayerPropsExtractor(BaseExtractor):
    """Extractor para props de jogadores."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de props."""
        super().__init__("player_props", season)
        self.market = self.config.get("market", "draftkings")
    
    def extract(
        self,
        prop_types: Optional[List[str]] = None,
        date: Optional[str] = None,
        fetch_all_prop_types: bool = True,
    ) -> Dict[str, Any]:
        """
        Extrai props de jogadores.
        
        Args:
            prop_types: Lista de tipos de props (opcional)
            date: Data no formato YYYY-MM-DD (opcional)
            fetch_all_prop_types: Se True, busca todas as prop types disponíveis
        
        Returns:
            Dicionário com os dados extraídos
        """
        dates = [date] if date else None
        
        # Se fetch_all_prop_types e prop_types não fornecido, busca todas
        if fetch_all_prop_types and not prop_types:
            logger.info(f"Buscando todos os tipos de props disponíveis para {self.market}...")
            all_prop_types = self.client.get_player_prop_types(market=self.market)
            logger.info(f"Encontrados {len(all_prop_types)} tipos de props: {all_prop_types}")
            prop_types = all_prop_types
        
        logger.info(f"Extraindo props de jogadores (market: {self.market})...")
        
        all_props = []
        
        if prop_types:
            # Busca props para cada tipo
            for prop_type in prop_types:
                logger.info(f"Buscando props do tipo: {prop_type}")
                props = self.client.get_player_props(
                    market=self.market,
                    prop_types=[prop_type],
                    dates=dates,
                    per_page=self.config.get("per_page", 100),
                )
                all_props.extend(props)
        else:
            # Busca todas as props sem filtrar por tipo
            props = self.client.get_player_props(
                market=self.market,
                prop_types=None,
                dates=dates,
                per_page=self.config.get("per_page", 100),
            )
            all_props.extend(props)
        
        return {
            "season": self.season,
            "market": self.market,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "prop_types": prop_types if prop_types else "all",
            "total_props": len(all_props),
            "props": all_props,
        }

