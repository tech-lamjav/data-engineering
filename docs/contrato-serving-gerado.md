# Mapa gerado: RPCs de serving × tabelas sincronizadas

<!-- GERADO por scripts/gera_contrato_serving.py a partir do pg_proc do PRD.
     NÃO editar à mão: o CI regenera e compara. -->

Quais funções `public.*` leem cada tabela da allowlist do sync, e quais colunas dessa
tabela aparecem no corpo. Serve para responder "quem quebra se esta coluna sair?" antes
de mexer no mart.

**Limites conhecidos.** A associação coluna→tabela é por nome: uma função que lê duas
tabelas com uma coluna homônima (`season`, `fixture_id`) lista a coluna nas duas. E
referência em literal de texto (`'linha_subindo'` entre aspas) não conta como leitura —
é a diferença entre quebrar e não quebrar num `DROP COLUMN`.

⚠️ **O limite que erra para o lado perigoso:** só contam referências QUALIFICADAS
(`alias.coluna`). Uma função de tabela única pode escrever `select linha_subindo from
futebol.int_futebol_premissas_ou` sem alias — legal mesmo com `search_path` vazio, que
obriga a qualificar *tabelas*, não *colunas* — e aparecer aqui como leitora sem nenhuma
coluna. Ler "_nenhuma coluna nomeada_" como "não lê nada" é o erro que este doc existe
para impedir: quando aparecer, abrir a função. Hoje não há nenhuma ocorrência.

A suposição de **grão** não está aqui; mora em
`analytics-engineering/dbt_futebol/docs/contrato-serving-rpcs.md`.


## `dim_teams`

Lida por 6 RPC(s):

- `get_futebol_fixture_numeros(bigint)` — `team_id`, `team_name`
- `get_futebol_fixtures_by_day(date,text[])` — `team_id`, `team_logo_url`
- `get_futebol_fixtures(text,bigint,text)` — `team_id`, `team_logo_url`
- `get_futebol_standings(text,bigint)` — `team_id`, `team_logo_url`, `team_name`
- `get_futebol_team_profile(bigint,text,bigint)` — `team_id`, `team_logo_url`, `team_name`
- `get_futebol_teams()` — `team_id`, `team_logo_url`, `team_name`

## `fact_fixture_events`

Lida por 2 RPC(s):

- `get_futebol_fixture_extras(bigint)` — `assist_player_name`, `competition`, `date_utc`, `event_detail`, `event_order`, `event_type`, `fixture_id`, `minute`, `minute_extra`, `player_id`, `player_name`, `season`, `team_id`, `team_name`, `team_side`
- `get_futebol_leaders(text,bigint)` — `competition`, `event_detail`, `event_type`, `player_id`, `player_name`, `season`, `team_name`

## `fact_fixture_lineups`

Lida por 1 RPC(s):

- `get_futebol_fixture_extras(bigint)` — `coach_name`, `competition`, `date_utc`, `fixture_id`, `formation`, `lineup_phase`, `season`, `team_id`, `team_name`, `team_side`

## `fact_fixture_lineups_players`

Lida por 1 RPC(s):

- `get_futebol_fixture_extras(bigint)` — `competition`, `date_utc`, `fixture_id`, `grid`, `is_starter`, `lineup_phase`, `player_id`, `player_name`, `player_slot`, `position`, `season`, `shirt_number`, `team_id`, `team_name`, `team_side`

## `fact_fixture_player_stats`

Lida por 1 RPC(s):

- `get_futebol_fixture_extras(bigint)` — `assists`, `competition`, `date_utc`, `fixture_id`, `goals_total`, `is_substitute`, `minutes`, `passes_key`, `player_id`, `player_name`, `position`, `rating`, `season`, `shirt_number`, `shots_on`, `shots_total`, `tackles_total`, `team_id`, `team_name`, `team_side`

## `fact_fixture_stats`

Lida por 3 RPC(s):

- `get_futebol_fixture_detail(bigint)` — `ball_possession`, `blocked_shots`, `competition`, `corner_kicks`, `date_utc`, `expected_goals`, `fixture_id`, `fouls`, `goalkeeper_saves`, `goals_prevented`, `offsides`, `passes_accurate`, `passes_pct`, `red_cards`, `season`, `shots_insidebox`, `shots_off_goal`, `shots_on_goal`, `shots_outsidebox`, `team_id`, `team_name`, `team_side`, `total_passes`, `total_shots`, `yellow_cards`
- `get_futebol_fixture_historico(bigint,integer)` — `competition`, `expected_goals`, `fixture_id`, `season`, `team_id`, `team_name`
- `get_futebol_team_profile(bigint,text,bigint)` — `ball_possession`, `competition`, `corner_kicks`, `expected_goals`, `fixture_id`, `season`, `shots_on_goal`, `team_id`, `team_name`, `team_side`, `total_shots`, `yellow_cards`

## `fact_fixtures`

Lida por 16 RPC(s):

- `_futebol_team_form(bigint,text,bigint,date)` — `away_team_id`, `away_team_name`, `competition`, `date_utc`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `home_team_name`, `season`, `status_short`
- `get_futebol_competitions()` — `competition`, `kickoff_utc`, `season`
- `get_futebol_fixture_days(date,date)` — `competition`, `kickoff_utc`
- `get_futebol_fixture_detail(bigint)` — `away_team_id`, `away_team_name`, `competition`, `date_utc`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `home_team_name`, `kickoff_utc`, `round`, `score_halftime_away`, `score_halftime_home`, `season`, `status_elapsed`, `status_long`, `status_short`, `venue_city`, `venue_name`
- `get_futebol_fixture_extras(bigint)` — `away_team_id`, `competition`, `date_utc`, `fixture_id`, `home_team_id`, `kickoff_utc`, `season`
- `get_futebol_fixture_historico(bigint,integer)` — `away_team_id`, `away_team_name`, `competition`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `home_team_name`, `kickoff_utc`, `season`, `status_short`
- `get_futebol_fixture_numeros(bigint)` — `away_team_id`, `competition`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `season`
- `get_futebol_fixture_value(bigint)` — `fixture_id`, `kickoff_utc`
- `get_futebol_fixtures_by_day(date,text[])` — `away_team_id`, `away_team_name`, `competition`, `date_utc`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `home_team_name`, `kickoff_utc`, `round`, `season`, `status_long`, `status_short`
- `get_futebol_fixtures(text,bigint,text)` — `away_team_id`, `away_team_name`, `competition`, `date_utc`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `home_team_name`, `kickoff_utc`, `round`, `season`, `status_long`, `status_short`
- `get_futebol_matchup_markets(bigint,bigint,text,bigint)` — `away_team_id`, `competition`, `goals_away`, `goals_home`, `home_team_id`, `season`, `status_short`
- `get_futebol_odds_board()` — `away_team_id`, `away_team_name`, `competition`, `fixture_id`, `home_team_id`, `home_team_name`, `kickoff_utc`, `status_short`
- `get_futebol_standings(text,bigint)` — `away_team_id`, `away_team_name`, `competition`, `goals_away`, `goals_home`, `home_team_id`, `home_team_name`, `season`, `status_short`
- `get_futebol_team_profile(bigint,text,bigint)` — `away_team_id`, `competition`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `season`, `status_short`
- `get_futebol_value_board()` — `away_team_id`, `away_team_name`, `competition`, `fixture_id`, `home_team_id`, `home_team_name`, `kickoff_utc`, `status_short`
- `get_futebol_value_history(date,date)` — `away_team_id`, `away_team_name`, `competition`, `fixture_id`, `home_team_id`, `home_team_name`, `kickoff_utc`, `status_short`

## `fact_h2h`

Lida por 2 RPC(s):

- `get_futebol_fixture_numeros(bigint)` — `away_team_id`, `competition`, `fixture_id`, `goals_away`, `goals_home`, `home_team_id`, `season`
- `get_futebol_h2h(bigint,bigint)` — `away_team_id`, `away_team_name`, `away_team_winner`, `competition`, `date_utc`, `fixture_id`, `goals_away`, `goals_home`, `h2h_pair_key`, `home_team_id`, `home_team_name`, `home_team_winner`, `season`, `status_short`

## `fact_injuries_snapshot`

Lida por 1 RPC(s):

- `get_futebol_fixture_injuries(bigint)` — `fixture_id`, `injury_reason`, `injury_type`, `player_id`, `player_name`, `snapshot_date`, `team_id`

## `fact_odds_snapshot`

Lida por 2 RPC(s):

- `get_futebol_fixture_odds(bigint)` — `bookmaker_name`, `collection_window`, `fixture_id`, `line_value`, `market_name`, `odd_decimal`, `outcome_label`
- `get_futebol_odds_board()` — `bookmaker_name`, `collection_window`, `competition`, `fixture_id`, `kickoff_utc`, `line_value`, `market_name`, `odd_decimal`, `outcome_label`

## `fact_predictions_api`

Lida por 1 RPC(s):

- `get_futebol_fixture_prediction(bigint)` — `advice`, `collection_window`, `comparison_att_away`, `comparison_att_home`, `comparison_def_away`, `comparison_def_home`, `comparison_form_away`, `comparison_form_home`, `comparison_goals_away`, `comparison_goals_home`, `comparison_h2h_away`, `comparison_h2h_home`, `comparison_poisson_away`, `comparison_poisson_home`, `comparison_total_away`, `comparison_total_home`, `fixture_id`, `predicted_winner_name`, `prob_away_pct`, `prob_draw_pct`, `prob_home_pct`

## `fact_standings_snapshot`

Lida por 1 RPC(s):

- `get_futebol_standings_official(text,bigint)` — `competition`, `draws_total`, `goals_against_total`, `goals_diff`, `goals_for_total`, `group_name`, `loses_total`, `played_total`, `points`, `rank`, `rank_description`, `season`, `snapshot_date`, `team_id`, `team_name`, `wins_total`

## `fact_team_season_stats`

Lida por 2 RPC(s):

- `get_futebol_fixture_numeros(bigint)` — `clean_sheet_total`, `competition`, `draws_away`, `draws_home`, `failed_to_score_total`, `form`, `goals_against_avg_away`, `goals_against_avg_home`, `goals_against_avg_total`, `goals_for_avg_away`, `goals_for_avg_home`, `goals_for_avg_total`, `loses_away`, `loses_home`, `played_away`, `played_home`, `played_total`, `season`, `snapshot_date`, `team_id`, `team_name`, `wins_away`, `wins_home`
- `get_futebol_team_season(bigint,text,bigint)` — `biggest_streak_loses`, `biggest_streak_wins`, `clean_sheet_away`, `clean_sheet_home`, `clean_sheet_total`, `competition`, `draws_away`, `draws_home`, `draws_total`, `failed_to_score_total`, `form`, `goals_against_avg_away`, `goals_against_avg_home`, `goals_against_avg_total`, `goals_for_avg_away`, `goals_for_avg_home`, `goals_for_avg_total`, `loses_away`, `loses_home`, `loses_total`, `penalty_scored_pct`, `penalty_total`, `played_away`, `played_home`, `played_total`, `season`, `snapshot_date`, `team_id`, `wins_away`, `wins_home`, `wins_total`

## `fact_value_opportunities`

Lida por 2 RPC(s):

- `get_futebol_fixture_value(bigint)` — `avg_odd`, `best_book`, `best_odd`, `edge`, `faixa`, `fixture_id`, `janela_usada`, `line_value`, `linha_sharp_confirma`, `market`, `modelo_api_concorda`, `n_casas`, `outcome`, `pen_odd_juice`, `pen_odd_longshot`, `pen_odd_outlier`, `pen_poucas_casas`, `penalidades`, `penalidades_especificas_pts`, `penalidades_globais_pts`, `premissas_sem_dado`, `prob_justa_fechamento`, `pts_corroboracao`, `pts_premissas`, `pts_valor`, `score`
- `get_futebol_value_board()` — `avg_odd`, `best_book`, `best_odd`, `competition`, `edge`, `faixa`, `fixture_id`, `janela_usada`, `line_value`, `linha_sharp_confirma`, `market`, `modelo_api_concorda`, `n_casas`, `outcome`, `penalidades`, `premissas_sem_dado`, `prob_justa_fechamento`, `pts_corroboracao`, `pts_premissas`, `pts_valor`, `score`

## `fact_value_opportunities_hist`

Lida por 2 RPC(s):

- `get_futebol_fixture_value(bigint)` — `avg_odd`, `best_book`, `best_odd`, `dbt_valid_from`, `dbt_valid_to`, `edge`, `faixa`, `fixture_id`, `janela_usada`, `line_value`, `linha_sharp_confirma`, `market`, `modelo_api_concorda`, `n_casas`, `outcome`, `pen_odd_juice`, `pen_odd_longshot`, `pen_odd_outlier`, `pen_poucas_casas`, `penalidades`, `penalidades_especificas_pts`, `penalidades_globais_pts`, `premissas_sem_dado`, `prob_justa_fechamento`, `pts_corroboracao`, `pts_premissas`, `pts_valor`, `score`
- `get_futebol_value_history(date,date)` — `avg_odd`, `best_book`, `best_odd`, `competition`, `dbt_valid_from`, `dbt_valid_to`, `edge`, `faixa`, `fixture_id`, `janela_usada`, `line_value`, `linha_sharp_confirma`, `market`, `modelo_api_concorda`, `n_casas`, `opportunity_key`, `outcome`, `penalidades`, `premissas_sem_dado`, `prob_justa_fechamento`, `pts_corroboracao`, `pts_premissas`, `pts_valor`, `score`

## `int_futebol_premissas_1x2`

Lida por 4 RPC(s):

- `get_futebol_fixture_premissas(bigint)` — `desfalque_adversario`, `desfalque_proprio`, `fixture_id`, `forca_mismatch`, `forma`, `h2h_favoravel`, `mando`, `outcome`, `penalidades_1x2_pts`, `pick_empate`, `pts_premissas`, `superioridade_tabela`, `superioridade_xg`
- `get_futebol_fixture_value(bigint)` — `desfalque_adversario`, `desfalque_proprio`, `fixture_id`, `forca_mismatch`, `forma`, `h2h_favoravel`, `mando`, `outcome`, `pick_empate`, `premissas_sem_dado`, `pts_premissas`, `superioridade_tabela`, `superioridade_xg`
- `get_futebol_value_board()` — `competition`, `desfalque_adversario`, `fixture_id`, `forca_mismatch`, `forma`, `h2h_favoravel`, `mando`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `superioridade_tabela`, `superioridade_xg`
- `get_futebol_value_history(date,date)` — `competition`, `desfalque_adversario`, `fixture_id`, `forca_mismatch`, `forma`, `h2h_favoravel`, `mando`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `superioridade_tabela`, `superioridade_xg`

## `int_futebol_premissas_ah`

Lida por 4 RPC(s):

- `get_futebol_fixture_premissas(bigint)` — `adversario_fragil_fora`, `defesa_fora_solida`, `favorito_irregular`, `fixture_id`, `handicap_alto`, `line_value`, `mando_forte`, `outcome`, `penalidades_ah_pts`, `pts_premissas`, `raramente_perde_por_2`, `sem_rodizio`, `supremacia`, `tende_golear`
- `get_futebol_fixture_value(bigint)` — `adversario_fragil_fora`, `defesa_fora_solida`, `favorito_irregular`, `fixture_id`, `handicap_alto`, `is_azarao`, `is_favorito`, `line_value`, `mando_forte`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `raramente_perde_por_2`, `sem_rodizio`, `supremacia`, `tende_golear`
- `get_futebol_value_board()` — `adversario_fragil_fora`, `competition`, `defesa_fora_solida`, `favorito_irregular`, `fixture_id`, `line_value`, `mando_forte`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `raramente_perde_por_2`, `sem_rodizio`, `supremacia`, `tende_golear`
- `get_futebol_value_history(date,date)` — `adversario_fragil_fora`, `competition`, `defesa_fora_solida`, `favorito_irregular`, `fixture_id`, `line_value`, `mando_forte`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `raramente_perde_por_2`, `sem_rodizio`, `supremacia`, `tende_golear`

## `int_futebol_premissas_btts`

Lida por 4 RPC(s):

- `get_futebol_fixture_premissas(bigint)` — `ambos_marcam`, `ataque_dos_dois`, `ataque_trava`, `defesa_forte`, `defesas_vazaveis`, `fixture_id`, `historico_btts`, `historico_seco`, `outcome`, `penalidades_btts_pts`, `pts_premissas`
- `get_futebol_fixture_value(bigint)` — `ambos_marcam`, `ataque_dos_dois`, `ataque_trava`, `defesa_forte`, `defesas_vazaveis`, `fixture_id`, `historico_btts`, `historico_seco`, `outcome`, `premissas_sem_dado`, `pts_premissas`
- `get_futebol_value_board()` — `ambos_marcam`, `ataque_dos_dois`, `ataque_trava`, `competition`, `defesa_forte`, `defesas_vazaveis`, `fixture_id`, `historico_btts`, `historico_seco`, `outcome`, `premissas_sem_dado`, `pts_premissas`
- `get_futebol_value_history(date,date)` — `ambos_marcam`, `ataque_dos_dois`, `ataque_trava`, `competition`, `defesa_forte`, `defesas_vazaveis`, `fixture_id`, `historico_btts`, `historico_seco`, `outcome`, `premissas_sem_dado`, `pts_premissas`

## `int_futebol_premissas_dc`

Lida por 4 RPC(s):

- `get_futebol_fixture_premissas(bigint)` — `adversario_limitado`, `equilibrio_defensivo`, `fixture_id`, `invicto_recente`, `lado_coberto_forte`, `outcome`, `penalidades_dc_pts`, `pts_premissas`
- `get_futebol_fixture_value(bigint)` — `adversario_limitado`, `equilibrio_defensivo`, `fixture_id`, `invicto_recente`, `lado_coberto_forte`, `outcome`, `premissas_sem_dado`, `pts_premissas`
- `get_futebol_value_board()` — `adversario_limitado`, `competition`, `equilibrio_defensivo`, `fixture_id`, `invicto_recente`, `lado_coberto_forte`, `outcome`, `premissas_sem_dado`, `pts_premissas`
- `get_futebol_value_history(date,date)` — `adversario_limitado`, `competition`, `equilibrio_defensivo`, `fixture_id`, `invicto_recente`, `lado_coberto_forte`, `outcome`, `premissas_sem_dado`, `pts_premissas`

## `int_futebol_premissas_ou`

Lida por 4 RPC(s):

- `get_futebol_fixture_premissas(bigint)` — `ambos_vazam`, `ataque_combinado`, `ataques_fracos`, `clean_sheets_altos`, `defesas_firmes`, `defesas_vazaveis`, `fixture_id`, `historico_over`, `historico_under`, `line_value`, `linha_extrema`, `outcome`, `penalidades_ou_pts`, `pts_premissas`, `ritmo_alto`, `xg_baixo_combinado`, `xg_combinado_alto`
- `get_futebol_fixture_value(bigint)` — `ambos_vazam`, `ataque_combinado`, `ataques_fracos`, `clean_sheets_altos`, `defesas_firmes`, `defesas_vazaveis`, `fixture_id`, `historico_over`, `historico_under`, `line_value`, `linha_extrema`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `ritmo_alto`, `xg_baixo_combinado`, `xg_combinado_alto`
- `get_futebol_value_board()` — `ambos_vazam`, `ataque_combinado`, `ataques_fracos`, `clean_sheets_altos`, `competition`, `defesas_firmes`, `defesas_vazaveis`, `fixture_id`, `historico_over`, `historico_under`, `line_value`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `ritmo_alto`, `xg_baixo_combinado`, `xg_combinado_alto`
- `get_futebol_value_history(date,date)` — `ambos_vazam`, `ataque_combinado`, `ataques_fracos`, `clean_sheets_altos`, `competition`, `defesas_firmes`, `defesas_vazaveis`, `fixture_id`, `historico_over`, `historico_under`, `line_value`, `outcome`, `premissas_sem_dado`, `pts_premissas`, `ritmo_alto`, `xg_baixo_combinado`, `xg_combinado_alto`

## Sem leitor nenhum

Sincronizadas para o Postgres mas não lidas por nenhuma função `public.*`. Ou o app as consome por outro caminho, ou estão sendo copiadas à toa:

- `dim_leagues`
- `int_futebol_odds_devig`
