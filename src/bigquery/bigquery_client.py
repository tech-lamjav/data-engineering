"""Gerenciamento de external tables no BigQuery."""
from typing import Optional, List, Dict
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from src.config import (
    GCS_BUCKET_NAME,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_DATASET,
    BIGQUERY_LOCATION,
    ENDPOINT_CONFIGS,
    SEASON_AVERAGES_COMBINATIONS,
    TEAM_SEASON_AVERAGES_COMBINATIONS,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class BigQueryClient:
    """Classe para gerenciar external tables no BigQuery."""
    
    def __init__(self, project_id: Optional[str] = None, dataset_id: Optional[str] = None):
        """
        Inicializa o cliente do BigQuery.
        
        Args:
            project_id: ID do projeto (usa BIGQUERY_PROJECT_ID do config se None)
            dataset_id: ID do dataset (usa BIGQUERY_DATASET do config se None)
        """
        self.project_id = project_id or BIGQUERY_PROJECT_ID
        self.dataset_id = dataset_id or BIGQUERY_DATASET
        
        if not self.project_id:
            raise ValueError("BIGQUERY_PROJECT_ID não está configurado")
        
        # Inicializa cliente do BigQuery
        self.client = bigquery.Client(project=self.project_id)
        logger.info(f"Cliente BigQuery inicializado para projeto: {self.project_id}")
    
    @staticmethod
    def get_gcs_prefix(endpoint: str, season: int) -> str:
        """Diretório-base (sem nome de arquivo) de um endpoint NBA no GCS.

        FONTE ÚNICA da estrutura de DIRETÓRIO de leitura, espelhando o prefixo que
        get_gcs_path (config) usa na ESCRITA: nba/{endpoint}/{season}. Mantê-lo num
        único helper evita que escrita e leitura divirjam (antes get_external_table_uri
        e get_player_props_uris remontavam o caminho manualmente, à mão).

        Returns:
            Prefixo gs:// SEM barra final (ex.: gs://bucket/nba/games/2025).
        """
        return f"gs://{GCS_BUCKET_NAME}/nba/{endpoint}/{season}"

    def get_player_props_uris(self, season: int, vendors: list) -> list:
        """
        Gera lista de URIs para player_props (um por vendor).
        Necessário porque o BigQuery não suporta múltiplos wildcards em um único URI.

        Args:
            season: Temporada
            vendors: Lista de vendors/markets

        Returns:
            Lista de URIs do GCS
        """
        # Estrutura de escrita (get_gcs_path): nba/player_props/{season}/{market}/...json
        return [f"{self.get_gcs_prefix('player_props', season)}/{vendor}/*.json" for vendor in vendors]

    def get_external_table_uri(self, endpoint: str, season: int, has_date: bool = False, category: str = None, type: str = None, market: str = None) -> str:
        """
        Gera o URI do GCS (glob de LEITURA) para a external table.

        O prefixo de diretório vem de get_gcs_prefix (fonte única, espelha a escrita
        de get_gcs_path); aqui só se decide o padrão de wildcard/arquivo por endpoint.

        Args:
            endpoint: Nome do endpoint
            season: Temporada
            has_date: Se o endpoint tem data (usa wildcard)
            category: Categoria para season_averages (opcional)
            type: Tipo para season_averages (opcional)
            market: Market para player_props (ex: 'draftkings')

        Returns:
            URI do GCS (gs://bucket/path)
        """
        prefix = self.get_gcs_prefix(endpoint, season)
        if endpoint == "player_props":
            # Estrutura: nba/player_props/{season}/{market}/*.json
            uri = f"{prefix}/{market}/*.json"
        elif has_date:
            # Endpoints por data: um único wildcard cobre os arquivos por data E os que
            # get_gcs_path grava em subdiretório q{period}/ (game_player_stats_period) —
            # o `*` do BigQuery casa inclusive a barra, então {season}/*.json também
            # captura {season}/q{period}/...json. Antes este glob omitia o q{period}/.
            uri = f"{prefix}/*.json"
        elif endpoint in ("season_averages", "team_season_averages") and category and type:
            # Para season_averages e team_season_averages, cria URI específico para cada combinação category-type
            # Formato: raw_nba_{endpoint}_{season}-{category}-{type}.json
            uri = f"{prefix}/raw_nba_{endpoint}_{season}-{category}-{type}-*.json"
        else:
            # Para endpoints sem data, lê um arquivo específico
            uri = f"{prefix}/raw_nba_{endpoint}_{season}.json"

        return uri
    
    def create_dataset_if_not_exists(
        self,
        description: str = "Dataset para external tables dos dados brutos da NBA",
    ) -> bigquery.Dataset:
        """
        Cria o dataset se não existir.

        Args:
            description: Descrição do dataset (default mantém compatibilidade NBA)

        Returns:
            Objeto Dataset criado ou existente
        """
        dataset_ref = bigquery.DatasetReference(self.client.project, self.dataset_id)

        try:
            dataset = self.client.get_dataset(dataset_ref)
            logger.info(f"Dataset {self.dataset_id} já existe")
            return dataset
        except NotFound:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = BIGQUERY_LOCATION
            dataset.description = description
            dataset = self.client.create_dataset(dataset)
            logger.info(f"✓ Dataset {self.dataset_id} criado na localização {BIGQUERY_LOCATION}")
            return dataset
    
    def create_external_table(
        self,
        table_id: str,
        uri,
        description: str = "",
        schema: Optional[List[bigquery.SchemaField]] = None,
    ) -> bigquery.Table:
        """
        Cria ou atualiza uma external table no BigQuery.
        Usa autodetect quando schema=None, ou schema explícito quando fornecido.

        Args:
            table_id: ID da tabela
            uri: URI do GCS (str) ou lista de URIs (list)
            description: Descrição da tabela
            schema: Schema explícito (usa autodetect se None)

        Returns:
            Objeto Table criado ou atualizado
        """
        source_uris = uri if isinstance(uri, list) else [uri]

        dataset_ref = bigquery.DatasetReference(self.client.project, self.dataset_id)
        table_ref = dataset_ref.table(table_id)

        def _build_table(ref) -> bigquery.Table:
            """Monta o objeto Table (external config) para um dado table_ref."""
            tbl = bigquery.Table(ref)
            external_config = bigquery.ExternalConfig(bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
            external_config.source_uris = source_uris
            external_config.ignore_unknown_values = True
            if schema:
                external_config.schema = schema
            else:
                external_config.autodetect = True
            tbl.external_data_configuration = external_config
            tbl.description = description
            return tbl

        # External tables precisam ser RECRIADAS (não basta UPDATE): com autodetect o
        # UPDATE não força re-detecção e mantém schema desatualizado. Antes fazíamos
        # delete + create direto, mas se o create falhasse (URI sem arquivos, schema
        # inválido, erro transitório/IAM) a tabela ficava AUSENTE até o próximo run,
        # quebrando dbt/sync downstream.
        #
        # Swap quase-atômico: cria primeiro numa tabela temporária (valida URI/schema
        # SEM tocar na tabela boa); só então remove a antiga e cria a definitiva. Se o
        # create temporário falhar, a tabela original permanece intacta e a exceção
        # propaga (o agregador a marca como False / o script retorna exit 1).
        tmp_table_id = f"{table_id}__tmp_swap"
        tmp_ref = dataset_ref.table(tmp_table_id)

        # Garante que não sobrou um temp de uma execução anterior interrompida.
        try:
            self.client.delete_table(tmp_ref)
        except NotFound:
            pass

        # Passo 1: cria a temporária. Falha aqui NÃO afeta a tabela boa existente.
        self.client.create_table(_build_table(tmp_ref))

        try:
            # Passo 2: remove a antiga (se existir) e cria a definitiva. A janela em que
            # a tabela "real" fica ausente é mínima (entre o delete e o create abaixo).
            try:
                self.client.delete_table(table_ref)
                logger.info(f"Tabela {self.dataset_id}.{table_id} removida para recriação")
            except NotFound:
                pass

            table = self.client.create_table(_build_table(table_ref))
            logger.info(f"✓ Tabela {self.dataset_id}.{table_id} criada")
            return table
        finally:
            # Limpa a temporária em qualquer caso (sucesso ou falha do passo 2).
            try:
                self.client.delete_table(tmp_ref)
            except NotFound:
                pass
    
    def create_all_external_tables(
        self,
        season: int,
        endpoints: Optional[List[str]] = None,
    ) -> Dict[str, bool]:
        """
        Cria todas as external tables para os endpoints configurados.
        
        Args:
            season: Temporada
            endpoints: Lista de endpoints para criar (cria todos se None)
        
        Returns:
            Dicionário com status de criação de cada tabela
        """
        # Cria dataset se não existir
        self.create_dataset_if_not_exists()
        
        # Endpoints para processar
        endpoints_to_process = endpoints or list(ENDPOINT_CONFIGS.keys())
        
        results = {}

        # Combos de (team_)season_averages: FONTE ÚNICA em src.config (importados no
        # topo). A extração (scripts) e a criação das external tables consomem a mesma
        # lista — antes estavam duplicadas aqui, exigindo sincronização manual.

        for endpoint in endpoints_to_process:
            if endpoint not in ENDPOINT_CONFIGS:
                logger.warning(f"Endpoint {endpoint} não encontrado na configuração. Pulando...")
                results[endpoint] = False
                continue
            
            try:
                config = ENDPOINT_CONFIGS[endpoint]
                has_date = config.get("has_date", False)
                
                # Tratamento especial para season_averages: cria 3 external tables
                if endpoint == "season_averages":
                    for combo in SEASON_AVERAGES_COMBINATIONS:
                        category = combo["category"]
                        type_param = combo["type"]
                        result_key = f"{endpoint}_{category}_{type_param}"
                        # try/except POR combo: falha em um não aborta os demais nem os
                        # marca como False (antes, 1 falha derrubava o restante do endpoint).
                        try:
                            uri = self.get_external_table_uri(
                                endpoint,
                                season,
                                has_date,
                                category=category,
                                type=type_param,
                            )
                            table_id = f"raw_{endpoint}_{category}_{type_param}"
                            description = f"External table para dados brutos de season_averages (category={category}, type={type_param})"
                            self.create_external_table(
                                table_id=table_id,
                                uri=uri,
                                description=description,
                            )
                            results[result_key] = True
                            logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                            logger.info(f"  URI: {uri}")
                        except Exception as e:
                            results[result_key] = False
                            logger.error(
                                f"✗ Falha ao criar external table do combo {result_key}: {e}",
                                exc_info=True,
                            )
                elif endpoint == "team_season_averages":
                    # Schemas explícitos para shooting opponent: autodetect infere alguns
                    # campos (ex.: _40_ft_opp_fga) como INTEGER na regular, mas playoffs
                    # contém valores FLOAT — força todos os stats como FLOAT64 para evitar
                    # erro "Could not convert value '0.4' to integer".
                    team_struct = bigquery.SchemaField("team", "RECORD", fields=[
                        bigquery.SchemaField("id", "INTEGER"),
                        bigquery.SchemaField("abbreviation", "STRING"),
                        bigquery.SchemaField("city", "STRING"),
                        bigquery.SchemaField("conference", "STRING"),
                        bigquery.SchemaField("division", "STRING"),
                        bigquery.SchemaField("full_name", "STRING"),
                        bigquery.SchemaField("name", "STRING"),
                    ])
                    by_zone_opp_stat_fields = [
                        "restricted_area_opp_fga", "restricted_area_opp_fgm", "restricted_area_opp_fg_pct",
                        "in_the_paint_non_ra_opp_fga", "in_the_paint_non_ra_opp_fgm", "in_the_paint_non_ra_opp_fg_pct",
                        "mid_range_opp_fga", "mid_range_opp_fgm", "mid_range_opp_fg_pct",
                        "left_corner_3_opp_fga", "left_corner_3_opp_fgm", "left_corner_3_opp_fg_pct",
                        "right_corner_3_opp_fga", "right_corner_3_opp_fgm", "right_corner_3_opp_fg_pct",
                        "corner_3_opp_fga", "corner_3_opp_fgm", "corner_3_opp_fg_pct",
                        "above_the_break_3_opp_fga", "above_the_break_3_opp_fgm", "above_the_break_3_opp_fg_pct",
                        "backcourt_opp_fga", "backcourt_opp_fgm", "backcourt_opp_fg_pct",
                    ]
                    range_5ft_opp_stat_fields = [
                        "less_than_5_ft_opp_fga", "less_than_5_ft_opp_fgm", "less_than_5_ft_opp_fg_pct",
                        "_5_9_ft_opp_fga", "_5_9_ft_opp_fgm", "_5_9_ft_opp_fg_pct",
                        "_10_14_ft_opp_fga", "_10_14_ft_opp_fgm", "_10_14_ft_opp_fg_pct",
                        "_15_19_ft_opp_fga", "_15_19_ft_opp_fgm", "_15_19_ft_opp_fg_pct",
                        "_20_24_ft_opp_fga", "_20_24_ft_opp_fgm", "_20_24_ft_opp_fg_pct",
                        "_25_29_ft_opp_fga", "_25_29_ft_opp_fgm", "_25_29_ft_opp_fg_pct",
                        "_30_34_ft_opp_fga", "_30_34_ft_opp_fgm", "_30_34_ft_opp_fg_pct",
                        "_35_39_ft_opp_fga", "_35_39_ft_opp_fgm", "_35_39_ft_opp_fg_pct",
                        "_40_ft_opp_fga", "_40_ft_opp_fgm", "_40_ft_opp_fg_pct",
                    ]
                    def shooting_opp_schema(stat_fields):
                        return [
                            bigquery.SchemaField("season", "INTEGER"),
                            bigquery.SchemaField("season_type", "STRING"),
                            bigquery.SchemaField("category", "STRING"),
                            bigquery.SchemaField("type", "STRING"),
                            team_struct,
                            bigquery.SchemaField("stats", "RECORD", fields=[
                                bigquery.SchemaField(name, "FLOAT") for name in stat_fields
                            ]),
                        ]

                    for combo in TEAM_SEASON_AVERAGES_COMBINATIONS:
                        category = combo["category"]
                        type_param = combo["type"]
                        result_key = f"{endpoint}_{category}_{type_param}"
                        # try/except POR combo: isola falhas (uma não derruba as demais).
                        try:
                            uri = self.get_external_table_uri(
                                endpoint,
                                season,
                                has_date,
                                category=category,
                                type=type_param,
                            )
                            table_id = f"raw_{endpoint}_{category}_{type_param}"
                            description = f"External table para dados brutos de team_season_averages (category={category}, type={type_param})"

                            explicit_schema = None
                            if category == "shooting" and type_param == "by_zone_opponent":
                                explicit_schema = shooting_opp_schema(by_zone_opp_stat_fields)
                            elif category == "shooting" and type_param == "5ft_range_opponent":
                                explicit_schema = shooting_opp_schema(range_5ft_opp_stat_fields)

                            self.create_external_table(
                                table_id=table_id,
                                uri=uri,
                                description=description,
                                schema=explicit_schema,
                            )
                            results[result_key] = True
                            logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                            logger.info(f"  URI: {uri}")
                        except Exception as e:
                            results[result_key] = False
                            logger.error(
                                f"✗ Falha ao criar external table do combo {result_key}: {e}",
                                exc_info=True,
                            )
                elif endpoint == "betting_odds":
                    # betting_odds: todos os vendors num único wildcard por game_id
                    uri = f"{self.get_gcs_prefix('betting_odds', season)}/*.json"
                    table_id = "raw_betting_odds"
                    description = "External table para dados brutos de betting odds (spread, moneyline, total) - todos os vendors"
                    self.create_external_table(
                        table_id=table_id,
                        uri=uri,
                        description=description,
                    )
                    results[endpoint] = True
                    logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                    logger.info(f"  URI: {uri}")
                elif endpoint == "player_props":
                    # player_props: uma tabela por vendor ativo (DraftKings, Caesars, BetRivers).
                    # BetRivers usa schema explícito porque seus arquivos têm duas variantes do STRUCT
                    # market: {type, odds} (milestone) e {type, over_odds, under_odds} (over_under).
                    # Autodetect amostral pode gerar schema incompleto, causando "No such field" em runtime.
                    player_props_market_schema = bigquery.SchemaField("market", "RECORD", fields=[
                        bigquery.SchemaField("type", "STRING"),
                        bigquery.SchemaField("odds", "INTEGER"),
                        bigquery.SchemaField("over_odds", "INTEGER"),
                        bigquery.SchemaField("under_odds", "INTEGER"),
                    ])
                    player_props_schema = [
                        bigquery.SchemaField("id", "INTEGER"),
                        bigquery.SchemaField("player_id", "INTEGER"),
                        bigquery.SchemaField("game_id", "INTEGER"),
                        bigquery.SchemaField("season", "INTEGER"),
                        bigquery.SchemaField("vendor", "STRING"),
                        bigquery.SchemaField("prop_type", "STRING"),
                        bigquery.SchemaField("line_value", "FLOAT"),
                        player_props_market_schema,
                        bigquery.SchemaField("total_props", "INTEGER"),
                        bigquery.SchemaField("updated_at", "TIMESTAMP"),
                    ]

                    vendors_config = [
                        {"market": "draftkings", "table_id": "raw_player_props",          "schema": None},
                        {"market": "caesars",    "table_id": "raw_player_props_caesars",  "schema": None},
                        {"market": "betrivers",  "table_id": "raw_player_props_betrivers","schema": player_props_schema},
                    ]
                    for vc in vendors_config:
                        uri = self.get_external_table_uri(endpoint, season, has_date, market=vc["market"])
                        self.create_external_table(
                            table_id=vc["table_id"],
                            uri=uri,
                            description=f"External table para props de jogadores - {vc['market']}",
                            schema=vc["schema"],
                        )
                        logger.info(f"✓ External table criada: {self.dataset_id}.{vc['table_id']}")
                        logger.info(f"  URI: {uri}")
                    results[endpoint] = True
                elif endpoint == "game_player_advanced_stats":
                    # Schema explícito: muitos campos rate/percentage do response /nba/v2/stats/advanced
                    # voltam como INT quando valor=0 e FLOAT quando >0 (ex.: pct_assisted_2pt, contested_fg_pct).
                    # Autodetect amostral pinaria como INTEGER e quebraria em arquivos posteriores.
                    # Force FLOAT em todos os numéricos. Strings: matchup_minutes (formato MM:SS), season_type.
                    advanced_numeric_fields = [
                        "pie", "assist_percentage", "assist_ratio", "assist_to_turnover",
                        "defensive_rating", "defensive_rebound_percentage",
                        "effective_field_goal_percentage", "estimated_defensive_rating",
                        "estimated_net_rating", "estimated_offensive_rating", "estimated_pace",
                        "estimated_usage_percentage", "net_rating", "offensive_rating",
                        "offensive_rebound_percentage", "pace", "pace_per_40", "possessions",
                        "rebound_percentage", "true_shooting_percentage", "turnover_ratio",
                        "usage_percentage", "blocks_against", "fouls_drawn",
                        "points_fast_break", "points_off_turnovers", "points_paint",
                        "points_second_chance", "opp_points_fast_break",
                        "opp_points_off_turnovers", "opp_points_paint",
                        "opp_points_second_chance", "pct_assisted_2pt", "pct_assisted_3pt",
                        "pct_assisted_fgm", "pct_fga_2pt", "pct_fga_3pt", "pct_pts_2pt",
                        "pct_pts_3pt", "pct_pts_fast_break", "pct_pts_free_throw",
                        "pct_pts_midrange_2pt", "pct_pts_off_turnovers", "pct_pts_paint",
                        "pct_unassisted_2pt", "pct_unassisted_3pt", "pct_unassisted_fgm",
                        "four_factors_efg_pct", "free_throw_attempt_rate",
                        "four_factors_oreb_pct", "opp_efg_pct",
                        "opp_free_throw_attempt_rate", "opp_oreb_pct", "opp_turnover_pct",
                        "team_turnover_pct", "box_outs", "box_out_player_rebounds",
                        "box_out_player_team_rebounds", "defensive_box_outs",
                        "offensive_box_outs", "charges_drawn", "contested_shots",
                        "contested_shots_2pt", "contested_shots_3pt", "deflections",
                        "loose_balls_recovered_def", "loose_balls_recovered_off",
                        "loose_balls_recovered_total", "screen_assists",
                        "screen_assist_points", "matchup_fg_pct", "matchup_fga",
                        "matchup_fgm", "matchup_3pt_pct", "matchup_3pa", "matchup_3pm",
                        "matchup_assists", "matchup_turnovers", "matchup_player_points",
                        "switches_on", "partial_possessions", "speed", "distance",
                        "touches", "passes", "secondary_assists", "free_throw_assists",
                        "contested_fga", "contested_fgm", "contested_fg_pct",
                        "uncontested_fga", "uncontested_fgm", "uncontested_fg_pct",
                        "defended_at_rim_fga", "defended_at_rim_fgm",
                        "defended_at_rim_fg_pct", "rebound_chances_def",
                        "rebound_chances_off", "rebound_chances_total", "pct_blocks",
                        "pct_blocks_allowed", "pct_fga", "pct_fgm", "pct_fta", "pct_ftm",
                        "pct_personal_fouls", "pct_personal_fouls_drawn", "pct_points",
                        "pct_rebounds_def", "pct_rebounds_off", "pct_rebounds_total",
                        "pct_steals", "pct_3pa", "pct_3pm", "pct_turnovers",
                    ]
                    stat_struct_fields = [
                        bigquery.SchemaField("id", "INTEGER"),
                        bigquery.SchemaField("period", "INTEGER"),
                        bigquery.SchemaField("season_type", "STRING"),
                        bigquery.SchemaField("matchup_minutes", "STRING"),
                        bigquery.SchemaField("player", "RECORD", fields=[
                            bigquery.SchemaField("id", "INTEGER"),
                        ]),
                        bigquery.SchemaField("team", "RECORD", fields=[
                            bigquery.SchemaField("id", "INTEGER"),
                        ]),
                        bigquery.SchemaField("game", "RECORD", fields=[
                            bigquery.SchemaField("id", "INTEGER"),
                            bigquery.SchemaField("date", "STRING"),
                            bigquery.SchemaField("season", "INTEGER"),
                        ]),
                    ] + [bigquery.SchemaField(name, "FLOAT") for name in advanced_numeric_fields]

                    advanced_schema = [
                        bigquery.SchemaField("season", "INTEGER"),
                        bigquery.SchemaField("date", "STRING"),
                        bigquery.SchemaField("total_stats", "INTEGER"),
                        bigquery.SchemaField("stats", "RECORD", mode="REPEATED", fields=stat_struct_fields),
                    ]

                    uri = self.get_external_table_uri(endpoint, season, has_date)
                    table_id = f"raw_{endpoint}"
                    description = "External table para advanced stats game-by-game (/nba/v2/stats/advanced) com schema explícito FLOAT para evitar conversão int→float entre arquivos"
                    self.create_external_table(
                        table_id=table_id,
                        uri=uri,
                        description=description,
                        schema=advanced_schema,
                    )
                    results[endpoint] = True
                    logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                    logger.info(f"  URI: {uri}")
                else:
                    # Gera URI do GCS
                    uri = self.get_external_table_uri(endpoint, season, has_date)

                    # Nome da tabela (sem o ano)
                    table_id = f"raw_{endpoint}"

                    # Descrição
                    description = f"External table para dados brutos do endpoint {endpoint}"
                    if has_date:
                        description += " (inclui múltiplos arquivos por data)"

                    # Cria a tabela
                    self.create_external_table(
                        table_id=table_id,
                        uri=uri,
                        description=description,
                    )

                    results[endpoint] = True
                    logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                    logger.info(f"  URI: {uri}")
                
            except Exception as e:
                logger.error(f"✗ Erro ao criar external table para {endpoint}: {str(e)}", exc_info=True)
                if endpoint == "season_averages":
                    for combo in SEASON_AVERAGES_COMBINATIONS:
                        result_key = f"{endpoint}_{combo['category']}_{combo['type']}"
                        results[result_key] = False
                elif endpoint == "team_season_averages":
                    for combo in TEAM_SEASON_AVERAGES_COMBINATIONS:
                        result_key = f"{endpoint}_{combo['category']}_{combo['type']}"
                        results[result_key] = False
                else:
                    results[endpoint] = False
        
        return results
