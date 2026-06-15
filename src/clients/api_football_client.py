"""Cliente para API-Football v3 (https://www.api-football.com)."""
import time
from typing import Dict, Any
from src.clients.base_client import BaseClient
from src.config import API_FOOTBALL_BASE_URL, API_FOOTBALL_KEY
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class ApiFootballClient(BaseClient):
    """Cliente para API-Football v3.

    Auth via header `x-apisports-key` (provider direto, não RapidAPI).
    Envelope de resposta: {get, parameters, errors, results, paging, response}.
    """

    def __init__(self):
        super().__init__(
            base_url=API_FOOTBALL_BASE_URL,
            auth_headers={"x-apisports-key": API_FOOTBALL_KEY},
        )

    def get_league(self, league_id: int, season: int) -> Dict[str, Any]:
        """GET /leagues?id={league_id}&season={season}.

        Returns:
            Envelope cru da API: {response: [{league, country, seasons:[...]}], errors, ...}
        """
        response = self._make_request(
            "GET",
            "leagues",
            params={"id": league_id, "season": season},
        )
        return response.json()

    def get_teams(self, league_id: int, season: int) -> Dict[str, Any]:
        """GET /teams?league={league_id}&season={season}.

        Returns:
            Envelope cru: {response: [{team:{...}, venue:{...}}], errors, ...}
        """
        response = self._make_request(
            "GET",
            "teams",
            params={"league": league_id, "season": season},
        )
        return response.json()

    def get_standings(self, league_id: int, season: int) -> Dict[str, Any]:
        """GET /standings?league={league_id}&season={season}.

        Tabela do campeonato (classificação) da liga/temporada — snapshot do momento
        da chamada. `response[0].league.standings` é um ARRAY DE ARRAYS: 1 array
        interno por grupo (Brasileirão tem 1; Copa do Mundo tem N grupos), cada item
        com rank/team/points/goalsDiff/group/form/all/home/away/update.

        Returns:
            Envelope cru: {response: [{league: {..., standings: [[...]]}}], errors, ...}
        """
        response = self._make_request(
            "GET",
            "standings",
            params={"league": league_id, "season": season},
        )
        return response.json()

    def get_injuries(self, league_id: int, season: int) -> Dict[str, Any]:
        """GET /injuries?league={league_id}&season={season} (NÃO paginado).

        Lesionados/suspensos ligados aos fixtures da liga/temporada — snapshot do
        momento da chamada. Cada item: {player, team, fixture, league, type, reason}.
        type ∈ {"Missing Fixture","Questionable"}; reason é texto livre (inclui
        suspensões: "Suspended"/"Red Card"...).

        Como /fixtures, o endpoint REJEITA o parâmetro `page` ("The Page field do not
        exist.") — retorna a temporada inteira numa única resposta, então é uma chamada
        simples (sem paginação), igual a get_standings.

        ⚠️ Só ligas com coverage.injuries=TRUE retornam dados (Brasileirão sim; Copa
        do Mundo não — validado em dim_leagues).

        Returns:
            Envelope cru: {response: [{player, team, fixture, league, type, reason}], errors, ...}
        """
        response = self._make_request(
            "GET",
            "injuries",
            params={"league": league_id, "season": season},
        )
        return response.json()

    def get_team_season_stats(
        self, league_id: int, season: int, team_id: int
    ) -> Dict[str, Any]:
        """GET /teams/statistics?league={league_id}&season={season}&team={team_id}.

        Agregados de temporada de UM time numa liga/temporada — base direta do modelo
        Poisson (força ataque/defesa, médias casa/fora). 1 chamada por (time, liga, season).

        ⚠️ Diferente dos outros endpoints, o `response` aqui é um OBJETO ÚNICO (não uma
        lista): {league, team, form, fixtures, goals, biggest, clean_sheet,
        failed_to_score, penalty, lineups, cards}. Não iterar sobre ele.

        Returns:
            Envelope cru: {response: {league, team, fixtures, goals, ...}, errors, ...}
        """
        response = self._make_request(
            "GET",
            "teams/statistics",
            params={"league": league_id, "season": season, "team": team_id},
        )
        return response.json()

    def get_players(self, league_id: int, season: int) -> Dict[str, Any]:
        """GET /players?league={league_id}&season={season} (paginado).

        O envelope da API-Football pagina via {paging:{current,total}}, diferente
        da balldontlie ({data,meta}), então BaseClient.get_paginated() não serve.
        Aqui iteramos page=1..paging.total concatenando response[].

        Returns:
            Envelope sintético {response: [todos os itens], errors, paging:{total}}
            no mesmo formato single-page dos outros métodos, p/ a lógica do
            extractor (errors/response) ficar idêntica à de get_teams.
        """
        all_items = []
        first_errors = None
        page = 1
        total_pages = 1

        while page <= total_pages:
            envelope = self._make_request(
                "GET",
                "players",
                params={"league": league_id, "season": season, "page": page},
            ).json()

            if page == 1:
                first_errors = envelope.get("errors")
                total_pages = (envelope.get("paging") or {}).get("total", 1) or 1

            all_items.extend(envelope.get("response", []) or [])
            page += 1

            if page <= total_pages:
                time.sleep(0.5)  # cortesia entre páginas (mesmo delay do get_paginated)

        return {
            "errors": first_errors,
            "paging": {"total": total_pages},
            "response": all_items,
        }

    def get_fixtures(self, league_id: int, season: int) -> Dict[str, Any]:
        """GET /fixtures?league={league_id}&season={season} (NÃO paginado).

        Tabela mãe: 1 fixture por jogo da temporada (passados, em curso, futuros).
        Cada item: {fixture, league, teams, goals, score}. Stats/events/lineups
        vêm de endpoints separados (subtasks 5-8).

        Diferente de /players, o /fixtures retorna a temporada inteira numa única
        resposta e **rejeita** o parâmetro `page` ("The Page field do not exist."),
        então é uma chamada simples como get_teams.

        Returns:
            Envelope cru: {response: [{fixture, league, teams, goals, score}], errors, ...}
        """
        response = self._make_request(
            "GET",
            "fixtures",
            params={"league": league_id, "season": season},
        )
        return response.json()

    def get_fixture_statistics(self, fixture_id: int) -> Dict[str, Any]:
        """GET /fixtures/statistics?fixture={fixture_id}. 1 chamada por jogo.

        Estatística agregada por time — 2 blocos por jogo (mandante e visitante),
        cada um {team:{...}, statistics:[{type, value}, ...]}. Bater apenas após
        status FT: para jogos não finalizados o `response` vem vazio.

        Returns:
            Envelope cru: {response: [{team, statistics}, ...], errors, ...}
        """
        response = self._make_request(
            "GET",
            "fixtures/statistics",
            params={"fixture": fixture_id},
        )
        return response.json()

    def get_fixture_events(self, fixture_id: int) -> Dict[str, Any]:
        """GET /fixtures/events?fixture={fixture_id}. 1 chamada por jogo.

        Linha do tempo do jogo — N eventos por jogo (gols, cartões, substituições,
        VAR), em ordem cronológica no array `response`. Cada item:
        {time:{elapsed,extra}, team:{...}, player:{...}, assist:{...}, type, detail,
        comments}. A API NÃO traz id nem campo de ordem — a posição no array é a
        ordem (o extractor carimba event_order). Bater apenas após status FT: para
        jogos não finalizados o `response` vem vazio.

        Returns:
            Envelope cru: {response: [{time, team, player, assist, type, detail, comments}, ...], errors, ...}
        """
        response = self._make_request(
            "GET",
            "fixtures/events",
            params={"fixture": fixture_id},
        )
        return response.json()

    def get_fixture_lineups(self, fixture_id: int) -> Dict[str, Any]:
        """GET /fixtures/lineups?fixture={fixture_id}. 1 chamada por jogo.

        Escalações dos dois times — 2 blocos por jogo (mandante e visitante), cada um
        {team:{...}, coach:{...}, formation, startXI:[{player}], substitutes:[{player}]}.
        Disponível ~20-40min antes do kickoff (escalação confirmada) e persiste pós-jogo
        (escalação real). Para jogos sem escalação publicada o `response` vem vazio.

        Returns:
            Envelope cru: {response: [{team, coach, formation, startXI, substitutes}, ...], errors, ...}
        """
        response = self._make_request(
            "GET",
            "fixtures/lineups",
            params={"fixture": fixture_id},
        )
        return response.json()

    def get_fixture_player_stats(self, fixture_id: int) -> Dict[str, Any]:
        """GET /fixtures/players?fixture={fixture_id}. 1 chamada por jogo.

        Estatística por jogador por time — 2 blocos por jogo (mandante e visitante),
        cada um {team:{...}, players:[{player:{id,name,photo}, statistics:[{...}]}]}.
        Diferente do /fixtures/statistics: `statistics` aqui NÃO é [{type, value}], e
        sim um array de tamanho 1 com um struct aninhado (games/shots/goals/passes/
        tackles/duels/dribbles/fouls/cards/penalty). Bater apenas após status FT: para
        jogos não finalizados o `response` vem vazio. Endpoint não pagina (paging.total=1).

        Returns:
            Envelope cru: {response: [{team, players:[{player, statistics}]}, ...], errors, ...}
        """
        response = self._make_request(
            "GET",
            "fixtures/players",
            params={"fixture": fixture_id},
        )
        return response.json()
