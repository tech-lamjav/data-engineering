"""Cliente específico para a API balldontlie.io."""
from typing import Dict, Any, Optional, List
from src.clients.base_client import BaseClient
from src.config import API_BASE_URL, API_KEY
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class BallDontLieClient(BaseClient):
    """Cliente para interagir com a API balldontlie.io."""
    
    def __init__(self):
        """Inicializa o cliente da API balldontlie.io."""
        super().__init__(base_url=API_BASE_URL, api_key=API_KEY)
    
    def get_games(
        self,
        season: int,
        team_ids: Optional[List[int]] = None,
        dates: Optional[List[str]] = None,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca jogos da temporada.
        
        Args:
            season: Ano da temporada
            team_ids: Lista de IDs de times (opcional)
            dates: Lista de datas no formato YYYY-MM-DD (opcional)
            per_page: Itens por página
        
        Returns:
            Lista de jogos
        """
        params = {"seasons[]": season}
        
        if team_ids:
            params["team_ids[]"] = team_ids
        
        if dates:
            params["dates[]"] = dates
        
        return self.get_paginated("games", params=params, per_page=per_page)
    
    def get_game_player_stats(
        self,
        season: Optional[int] = None,
        game_ids: Optional[List[int]] = None,
        player_ids: Optional[List[int]] = None,
        dates: Optional[List[str]] = None,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca estatísticas de jogadores por jogo.
        
        Args:
            season: Ano da temporada (opcional)
            game_ids: Lista de IDs de jogos (opcional)
            player_ids: Lista de IDs de jogadores (opcional)
            dates: Lista de datas no formato YYYY-MM-DD (opcional)
            per_page: Itens por página
        
        Returns:
            Lista de estatísticas
        """
        params = {}
        
        if season:
            params["seasons[]"] = season
        
        if game_ids:
            params["game_ids[]"] = game_ids
        
        if player_ids:
            params["player_ids[]"] = player_ids
        
        if dates:
            params["dates[]"] = dates
        
        return self.get_paginated("stats", params=params, per_page=per_page)
    
    def get_season_averages(
        self,
        season: int,
        player_ids: Optional[List[int]] = None,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca médias da temporada.
        
        Args:
            season: Ano da temporada
            player_ids: Lista de IDs de jogadores (opcional)
            per_page: Itens por página
        
        Returns:
            Lista de médias
        """
        params = {"season": season}
        
        if player_ids:
            params["player_ids[]"] = player_ids
        
        return self.get_paginated("season_averages", params=params, per_page=per_page)
    
    def get_active_players(
        self,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca jogadores ativos.
        
        Args:
            per_page: Itens por página
        
        Returns:
            Lista de jogadores ativos
        """
        # Limite de segurança: NBA tem ~500 jogadores ativos, mas vamos permitir até 1000
        # para cobrir variações e jogadores em diferentes ligas/status
        return self.get_paginated("players/active", params={}, per_page=per_page, max_items=1000)
    
    def get_player_injuries(
        self,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca lesões de jogadores.
        
        Args:
            per_page: Itens por página
        
        Returns:
            Lista de lesões
        """
        return self.get_paginated("injuries", params={}, per_page=per_page)
    
    def get_team_standings(
        self,
        season: int,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca classificação de times.
        
        Args:
            season: Ano da temporada
            per_page: Itens por página
        
        Returns:
            Lista de classificações
        """
        params = {"season": season}
        return self.get_paginated("standings", params=params, per_page=per_page)
    
    def get_player_props(
        self,
        market: str = "draftkings",
        prop_types: Optional[List[str]] = None,
        dates: Optional[List[str]] = None,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca props de jogadores.
        
        Args:
            market: Mercado (default: draftkings)
            prop_types: Lista de tipos de props (opcional, busca todas se None)
            dates: Lista de datas no formato YYYY-MM-DD (opcional)
            per_page: Itens por página
        
        Returns:
            Lista de props
        """
        params = {"market": market}
        
        if prop_types:
            params["prop_types[]"] = prop_types
        
        if dates:
            params["dates[]"] = dates
        
        return self.get_paginated("player_props", params=params, per_page=per_page)
    
    def get_player_prop_types(
        self,
        market: str = "draftkings",
    ) -> List[str]:
        """
        Busca todos os tipos de props disponíveis para um mercado.
        
        Args:
            market: Mercado (default: draftkings)
        
        Returns:
            Lista de tipos de props únicos
        """
        # Busca uma amostra para identificar os tipos disponíveis
        params = {"market": market, "per_page": 100}
        response = self.get("player_props", params=params)
        data = response.json()
        
        items = data.get("data", [])
        prop_types = set()
        
        for item in items:
            prop_type = item.get("prop_type")
            if prop_type:
                prop_types.add(prop_type)
        
        return sorted(list(prop_types))

