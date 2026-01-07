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
        category: str,
        type: str,
        season_type: str = "regular",
        player_ids: Optional[List[int]] = None,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Busca médias da temporada.
        
        Args:
            season: Ano da temporada
            category: Categoria (general, clutch, defense, shooting)
            type: Tipo de estatística (base, advanced, usage, scoring, defense, misc, etc.)
            season_type: Tipo de temporada (regular, playoffs, ist, playin)
            player_ids: Lista de IDs de jogadores (opcional)
            per_page: Itens por página
        
        Returns:
            Lista de médias
        """
        params = {
            "season": season,
            "season_type": season_type,
            "type": type,
        }
        
        if player_ids:
            params["player_ids[]"] = player_ids
        
        endpoint = f"season_averages/{category}"
        return self.get_paginated(endpoint, params=params, per_page=per_page)
    
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
        return self.get_paginated("player_injuries", params={}, per_page=per_page)
    
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
        game_id: int,
        player_id: Optional[int] = None,
        prop_type: Optional[str] = None,
        vendors: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca props de jogadores para um jogo específico.
        
        Args:
            game_id: ID do jogo (obrigatório)
            player_id: ID do jogador (opcional)
            prop_type: Tipo de prop (opcional)
            vendors: Lista de vendors (opcional, ex: ["draftkings", "betway"])
        
        Returns:
            Lista de props
        """
        from src.config import API_BASE_URL_V2
        
        params = {"game_id": game_id}
        
        if player_id:
            params["player_id"] = player_id
        
        if prop_type:
            params["prop_type"] = prop_type
        
        if vendors:
            params["vendors[]"] = vendors
        
        # Endpoint v2 - usa URL base diferente
        from src.config import API_TIMEOUT
        url = f"{API_BASE_URL_V2}/odds/player_props"
        response = self.session.request(
            method="GET",
            url=url,
            params=params,
            timeout=API_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        
        # A API v2 retorna diretamente uma lista ou um objeto com 'data'
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("data", [])
        else:
            return []

