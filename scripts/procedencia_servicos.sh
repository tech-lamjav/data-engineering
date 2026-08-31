#!/bin/bash
# Carimbo de procedência de um serviço Cloud Run.
#
# Imprime um hash do CONTEÚDO EM DISCO das paths que o serviço EXECUTA (não das que o
# deploy COPIA). O `deploy_cloud_run.sh` grava esse hash como env var no serviço; o
# detector (`checa_deriva_servicos.sh`) recalcula a mesma coisa e compara. Divergência =
# deriva: o que roda em produção não é o que está no master.
#
# Uso:
#   scripts/procedencia_servicos.sh <servico>            # hash combinado (a verdade)
#   scripts/procedencia_servicos.sh <servico> --nucleo   # só o núcleo compartilhado
#   scripts/procedencia_servicos.sh <servico> --svc      # só a parte própria do serviço
#   scripts/procedencia_servicos.sh <servico> --paths    # lista as paths declaradas e sai
#   scripts/procedencia_servicos.sh <servico> --todos    # combinado, nucleo e svc (3 linhas), 1 chamada só
#   scripts/procedencia_servicos.sh --list-servicos      # lista os serviços cobertos e sai
#
# `--list-servicos` é o que `checa_deriva_servicos.sh` usa para o default (sem args) e
# o que `deploy_cloud_run.sh` usa para o cross-check fail-closed nos dois sentidos: a
# tabela do deploy (29 alvos) tem de bater exatamente com `SERVICOS_CONHECIDOS` abaixo.
# Pedir o hash de um serviço fora dessa lista sai com exit 3 (escopo, não erro de path
# — esse é o exit 1, abaixo). `cloud_run/extract_player_props/` é o caso deliberado: o
# diretório é órfão (ver comentário em `deploy_cloud_run.sh`) e não entra aqui.
#
# POR QUE O HASH É DO DISCO, e não de `git rev-parse HEAD:<path>`:
# o `gcloud run deploy --source` empacota o diretório temporário que o
# `deploy_cloud_run.sh` monta EM DISCO, não o HEAD do git. Um build feito de um
# `.claude/worktrees/` numa branch com alteração ainda não commitada (é assim que este
# repo trabalha — ver CLAUDE.md) carimbaria o hash do master enquanto o container sobe
# outra coisa. O carimbo mentiria na direção perigosa ("está fresco" quando não está).
# Hasheando o disco, um build sujo aparece como deriva até virar commit — que é a
# verdade que o operador precisa.
#
# POR QUE O CARIMBO MORA NO SERVIÇO (env var), e não em label da imagem:
# o que apodrece não é a tag da imagem no Artifact Registry, é o hash FIXADO NA REVISÃO
# ativa — é essa a pergunta que o detector precisa responder, e a env var é legível por
# `gcloud run services describe` com só `roles/run.viewer`, sem tocar o registry (mesma
# escolha do carimbo dos Cloud Run Jobs dbt, ver ADR 0001 do analytics-engineering).
#
# POR QUE NÚCLEO E SVC SÃO HASHES SEPARADOS (decisão A do ADR 0001 deste repo):
# um serviço deriva sozinho quando só o próprio `main.py` muda (15 commits em
# `cloud_run/<dir>/` em 92 dias); um alvo único cego para isso faria os 29 acenderem
# juntos por qualquer commit em qualquer serviço. O combinado é o que o detector
# compara; os dois componentes existem para o relatório (DE #52) agrupar por causa
# comum sem reduzir a verdade por serviço a uma média.
#
# POR QUE `scripts/<x>.py` NÃO ENTRA NO MESMO SUBCONJUNTO PARA TODO MUNDO (decisão A′):
# o manifesto cobre o que o `main.py` de cada serviço EXECUTA, verificado por leitura
# direta dos 29 `main.py` (não por suposição de convenção) — e a convenção não é
# uniforme dentro do próprio futebol:
#   - os 13 de NBA sempre importam `from extract_X import main` (resolve em
#     `scripts/extract_X.py`, incl. as 3 variantes de player-props que compartilham
#     `scripts/extract_player_props.py`)
#   - 9 dos 13 de futebol seguem o mesmo padrão (leagues, teams, players, fixtures,
#     fixture_statistics, fixture_events, fixture_player_stats, team_season_stats,
#     standings)
#   - odds, predictions e fixture_lineups chamam o extractor de `src/extractors/`
#     DIRETO — `scripts/futebol/extract_{odds,predictions,fixture_lineups}.py` existem
#     no disco (a imagem os copia) mas nenhum `main.py` os importa: não são
#     comportamentais e ficam FORA do manifesto desses três
#   - injuries usa OS DOIS: current/backfill passam por
#     `scripts/futebol/extract_injuries.py`, pregame chama `InjuriesExtractor` direto —
#     os dois caminhos estão em produção (ver `cloud_run/futebol/extract_injuries/main.py`)
# Declarar `scripts/futebol/` inteiro para os 13 (a leitura ingênua do ADR) cobriria
# menos precisamente o que roda e reintroduziria alarme cruzado dentro do próprio
# futebol; declarar de menos (achando que nenhum futebol usa `scripts/`) apagaria
# deriva real dos 9 que usam. A tabela abaixo é o resultado de ler os 29 `main.py`.
#
# Ver docs/adr/0001-carimbo-de-procedencia-dos-servicos-cloud-run.md

set -euo pipefail

# Ordenação e hash precisam ser idênticos entre quem faz o deploy (macOS) e o detector
# (ubuntu-latest, no CI que a DE #54 vai criar). Sem LC_ALL=C a collation difere entre os
# dois, a ordem das linhas muda e o hash combinado muda por causa de locale, não de
# código — parecendo bug misterioso em vez de diferença de ambiente.
export LC_ALL=C

# Fonte da verdade para "quais 29 serviços este manifesto cobre" — consumida por
# `--list-servicos`. Tem de bater 1:1 com os nomes das tabelas NBA_SERVICES /
# FUTEBOL_SERVICES / SHARED_SERVICES do `deploy_cloud_run.sh` (checado em CI por
# `tests/test_procedencia_servicos.py` e, em runtime, pelo cross-check fail-closed do
# próprio `deploy_cloud_run.sh`). Acrescentar um nome aqui sem um `case` correspondente
# em `declarar_paths()` quebra alto (a checagem de consistência do teste), não em
# silêncio.
SERVICOS_CONHECIDOS=(
    extract-active-players
    extract-games
    extract-game-player-stats
    extract-game-player-stats-period
    extract-game-player-advanced-stats
    extract-season-averages
    extract-team-season-averages
    extract-player-injuries
    extract-team-standings
    extract-player-props-draftkings
    extract-player-props-caesars
    extract-player-props-betrivers
    extract-betting-odds
    extract-leagues
    extract-teams
    extract-players
    extract-fixtures
    extract-fixture-statistics
    extract-fixture-events
    extract-fixture-lineups
    extract-fixture-player-stats
    extract-team-season-stats
    extract-standings
    extract-injuries
    extract-odds
    extract-predictions
    notify-execution
    sync-bq-to-postgres
    daily-summary
)

SERVICO="${1:-}"
FLAG="${2:-}"

if [ "$SERVICO" = "--list-servicos" ]; then
    printf '%s\n' "${SERVICOS_CONHECIDOS[@]}"
    exit 0
fi

if [ -z "$SERVICO" ]; then
    echo "ERROR: uso: $0 <servico> [--paths|--nucleo|--svc] | $0 --list-servicos" >&2
    exit 2
fi

# Manifesto declarativo: serviço -> núcleo compartilhado + parte própria do serviço.
# "Parte própria" funde os tiers "módulo" e "serviço" do ADR (extractor/script +
# main.py/Procfile) num só componente, PROCEDENCIA_HASH_SVC — o ADR só grava dois
# componentes por serviço porque o relatório (#52) só precisa distinguir "é o núcleo
# compartilhado" de "não é".
declarar_paths() {
    local servico=$1

    # Núcleo compartilhado (decisão A′ do ADR): o que `BaseExtractor` importa e todo
    # extrator herda. Uniforme para os 29 por decisão do ADR — inclusive
    # `notify-execution`, que hoje não importa nada de `src/` (é só `smtplib`): manter o
    # mecanismo uniforme é mais simples que um quinto caso especial, e over-declarar
    # núcleo não é fail-open (o risco do manifesto é declarar de MENOS, não de mais).
    # Falta só o `requirements.txt`, que é por serviço porque cada imagem builda suas
    # próprias dependências.
    #
    # `src/extractors/base_extractor.py`: achado do auditor de fecho de imports (DE
    # #55) — os 26 serviços com extrator herdam de `BaseExtractor`
    # (`from src.extractors.base_extractor import BaseExtractor`, em CADA
    # `<x>_extractor.py`), e essa path tinha ficado FORA do manifesto desde a DE #51.
    # É a classe de bug que o auditor existe pra pegar, e pegou no próprio manifesto
    # que o introduziu.
    local NUCLEO_COMUM=(
        src/config.py
        src/clients
        src/storage
        src/utils
        src/bigquery
        src/extractors/base_extractor.py
    )

    case "$servico" in
        # ---- NBA (13): scripts/extract_X.py é comportamental (sys.path + `from extract_X import main`) ----
        extract-active-players)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_active_players/requirements.txt)
            SVC_PATHS=(
                src/extractors/active_players_extractor.py
                scripts/extract_active_players.py
                cloud_run/extract_active_players/main.py
            )
            ;;
        extract-games)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_games/requirements.txt)
            SVC_PATHS=(
                src/extractors/games_extractor.py
                scripts/extract_games.py
                cloud_run/extract_games/main.py
            )
            ;;
        extract-game-player-stats)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_game_player_stats/requirements.txt)
            SVC_PATHS=(
                src/extractors/game_player_stats_extractor.py
                scripts/extract_game_player_stats.py
                cloud_run/extract_game_player_stats/main.py
            )
            ;;
        extract-game-player-stats-period)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_game_player_stats_period/requirements.txt)
            SVC_PATHS=(
                src/extractors/game_player_stats_period_extractor.py
                scripts/extract_game_player_stats_period.py
                cloud_run/extract_game_player_stats_period/main.py
            )
            ;;
        extract-game-player-advanced-stats)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_game_player_advanced_stats/requirements.txt)
            SVC_PATHS=(
                src/extractors/game_player_advanced_stats_extractor.py
                scripts/extract_game_player_advanced_stats.py
                cloud_run/extract_game_player_advanced_stats/main.py
            )
            ;;
        extract-season-averages)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_season_averages/requirements.txt)
            SVC_PATHS=(
                src/extractors/season_averages_extractor.py
                scripts/extract_season_averages.py
                cloud_run/extract_season_averages/main.py
            )
            ;;
        extract-team-season-averages)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_team_season_averages/requirements.txt)
            SVC_PATHS=(
                src/extractors/team_season_averages_extractor.py
                scripts/extract_team_season_averages.py
                cloud_run/extract_team_season_averages/main.py
            )
            ;;
        extract-player-injuries)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_player_injuries/requirements.txt)
            SVC_PATHS=(
                src/extractors/player_injuries_extractor.py
                scripts/extract_player_injuries.py
                cloud_run/extract_player_injuries/main.py
            )
            ;;
        extract-team-standings)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_team_standings/requirements.txt)
            SVC_PATHS=(
                src/extractors/team_standings_extractor.py
                scripts/extract_team_standings.py
                cloud_run/extract_team_standings/main.py
            )
            ;;
        extract-player-props-draftkings)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_player_props_draftkings/requirements.txt)
            SVC_PATHS=(
                src/extractors/player_props_extractor.py
                scripts/extract_player_props.py
                cloud_run/extract_player_props_draftkings/main.py
            )
            ;;
        extract-player-props-caesars)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_player_props_caesars/requirements.txt)
            SVC_PATHS=(
                src/extractors/player_props_extractor.py
                scripts/extract_player_props.py
                cloud_run/extract_player_props_caesars/main.py
            )
            ;;
        extract-player-props-betrivers)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_player_props_betrivers/requirements.txt)
            SVC_PATHS=(
                src/extractors/player_props_extractor.py
                scripts/extract_player_props.py
                cloud_run/extract_player_props_betrivers/main.py
            )
            ;;
        extract-betting-odds)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/extract_betting_odds/requirements.txt)
            SVC_PATHS=(
                src/extractors/betting_odds_extractor.py
                scripts/extract_betting_odds.py
                cloud_run/extract_betting_odds/main.py
            )
            ;;

        # ---- Futebol (13) — ver nota "POR QUE scripts/<x>.py NÃO ENTRA..." no cabeçalho ----
        extract-leagues)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_leagues/requirements.txt)
            SVC_PATHS=(
                src/extractors/leagues_extractor.py
                scripts/futebol/extract_leagues.py
                cloud_run/futebol/extract_leagues/main.py
            )
            ;;
        extract-teams)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_teams/requirements.txt)
            SVC_PATHS=(
                src/extractors/teams_extractor.py
                scripts/futebol/extract_teams.py
                cloud_run/futebol/extract_teams/main.py
            )
            ;;
        extract-players)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_players/requirements.txt)
            SVC_PATHS=(
                src/extractors/players_extractor.py
                scripts/futebol/extract_players.py
                cloud_run/futebol/extract_players/main.py
            )
            ;;
        extract-fixtures)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_fixtures/requirements.txt)
            SVC_PATHS=(
                src/extractors/fixtures_extractor.py
                scripts/futebol/extract_fixtures.py
                cloud_run/futebol/extract_fixtures/main.py
            )
            ;;
        extract-fixture-statistics)
            # `fixture_statistics_extractor.py` herda de `PerFixtureExtractor` — base
            # PRÓPRIA (não é a `BaseExtractor` do núcleo), compartilhada só entre os 3
            # extratores "por fixture" (statistics/events/player-stats). Achado do
            # auditor de fecho de imports (DE #55), fora do manifesto desde a DE #51.
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_fixture_statistics/requirements.txt)
            SVC_PATHS=(
                src/extractors/per_fixture_extractor.py
                src/extractors/fixture_statistics_extractor.py
                scripts/futebol/extract_fixture_statistics.py
                cloud_run/futebol/extract_fixture_statistics/main.py
            )
            ;;
        extract-fixture-events)
            # Ver nota do `PerFixtureExtractor` em extract-fixture-statistics acima.
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_fixture_events/requirements.txt)
            SVC_PATHS=(
                src/extractors/per_fixture_extractor.py
                src/extractors/fixture_events_extractor.py
                scripts/futebol/extract_fixture_events.py
                cloud_run/futebol/extract_fixture_events/main.py
            )
            ;;
        extract-fixture-lineups)
            # main.py chama `FixtureLineupsExtractor` de `src/extractors/` DIRETO.
            # `scripts/futebol/extract_fixture_lineups.py` existe no disco (a imagem
            # copia `scripts/` inteiro) mas nenhum import o alcança — fora do manifesto
            # de propósito, não por esquecimento.
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_fixture_lineups/requirements.txt)
            SVC_PATHS=(
                src/extractors/fixture_lineups_extractor.py
                cloud_run/futebol/extract_fixture_lineups/main.py
            )
            ;;
        extract-fixture-player-stats)
            # Ver nota do `PerFixtureExtractor` em extract-fixture-statistics acima.
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_fixture_player_stats/requirements.txt)
            SVC_PATHS=(
                src/extractors/per_fixture_extractor.py
                src/extractors/fixture_player_stats_extractor.py
                scripts/futebol/extract_fixture_player_stats.py
                cloud_run/futebol/extract_fixture_player_stats/main.py
            )
            ;;
        extract-team-season-stats)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_team_season_stats/requirements.txt)
            SVC_PATHS=(
                src/extractors/team_season_stats_extractor.py
                scripts/futebol/extract_team_season_stats.py
                cloud_run/futebol/extract_team_season_stats/main.py
            )
            ;;
        extract-standings)
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_standings/requirements.txt)
            SVC_PATHS=(
                src/extractors/standings_extractor.py
                scripts/futebol/extract_standings.py
                cloud_run/futebol/extract_standings/main.py
            )
            ;;
        extract-injuries)
            # OS DOIS caminhos estão em produção: current/backfill passam por
            # `scripts/futebol/extract_injuries.py`; pregame chama `InjuriesExtractor`
            # direto (ver `cloud_run/futebol/extract_injuries/main.py`).
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_injuries/requirements.txt)
            SVC_PATHS=(
                src/extractors/injuries_extractor.py
                scripts/futebol/extract_injuries.py
                cloud_run/futebol/extract_injuries/main.py
            )
            ;;
        extract-odds)
            # main.py chama `OddsExtractor` de `src/extractors/` DIRETO.
            # `scripts/futebol/extract_odds.py` existe no disco mas não é importado —
            # fora do manifesto de propósito.
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_odds/requirements.txt)
            SVC_PATHS=(
                src/extractors/odds_extractor.py
                cloud_run/futebol/extract_odds/main.py
            )
            ;;
        extract-predictions)
            # main.py chama `PredictionsExtractor` de `src/extractors/` DIRETO.
            # `scripts/futebol/extract_predictions.py` existe no disco mas não é
            # importado — fora do manifesto de propósito.
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/futebol/extract_predictions/requirements.txt)
            SVC_PATHS=(
                src/extractors/predictions_extractor.py
                cloud_run/futebol/extract_predictions/main.py
            )
            ;;

        # ---- Compartilhados (3) ----
        notify-execution)
            # Não importa `src/`/`scripts/` nenhum — a lógica inteira (SMTP) mora no
            # próprio `main.py`. Núcleo continua declarado por uniformidade (ver acima).
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/notify_execution/requirements.txt)
            SVC_PATHS=(
                cloud_run/notify_execution/main.py
                cloud_run/notify_execution/Procfile
            )
            ;;
        sync-bq-to-postgres)
            # `src/sync/` declarado como diretório (não arquivo a arquivo): o serviço
            # importa `src.sync.bq_to_postgres`, e o módulo é pequeno o bastante para o
            # diretório inteiro ser a granularidade certa (ADR: "sync-bq-to-postgres
            # declara src/sync/").
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/sync_bq_to_postgres/requirements.txt)
            SVC_PATHS=(
                src/sync
                cloud_run/sync_bq_to_postgres/main.py
            )
            ;;
        daily-summary)
            # `src/reporting/` declarado como diretório pela mesma razão do sync — e
            # porque é o próprio canal de alarme (ADR, "escopo: 29 alvos, não 26").
            NUCLEO_PATHS=("${NUCLEO_COMUM[@]}" cloud_run/daily_summary/requirements.txt)
            SVC_PATHS=(
                src/reporting
                cloud_run/daily_summary/main.py
                cloud_run/daily_summary/Procfile
            )
            ;;

        extract-player-props)
            # Diretório ÓRFÃO (ver comentário em `deploy_cloud_run.sh`, junto de
            # NBA_SERVICES): substituído pelas 3 variantes por vendor acima. Não é
            # deployado por `deploy_cloud_run.sh` e não entra no manifesto de
            # propósito — DE #51 AC. Confirmar que não há serviço Cloud Run órfão
            # rodando esse código defasado em produção é a DE #53.
            return 3
            ;;

        *)
            return 3
            ;;
    esac
    return 0
}

if ! declarar_paths "$SERVICO"; then
    echo "ERROR: servico '$SERVICO' nao esta no manifesto do procedencia_servicos.sh." >&2
    echo "       Rode '$0 --list-servicos' para ver os 29 cobertos." >&2
    exit 3
fi

if [ "$FLAG" = "--paths" ]; then
    printf '%s\n' "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Fail-closed no inventário: path declarada que sumiu do disco ABORTA em vez de ser
# pulada em silêncio. Um typo no manifesto não pode encolher a cobertura calado — é a
# mesma regra do procedencia.sh dos jobs dbt (analytics-engineering).
#
# Isto cobre arquivo apagado. Diretório que ficou vazio (todo arquivo dentro dele
# apagado, mas a pasta em si continua no disco) passaria no `-e` e o `hash_paths`
# abaixo contribuiria silenciosamente ZERO entradas para aquele componente — a mesma
# classe de encolhimento calado que este bloco existe para barrar, só que por dentro
# de uma path que "existe". Por isso o segundo teste, só para diretório.
for p in "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"; do
    if [ ! -e "$p" ]; then
        echo "ERROR: path declarada para $SERVICO nao existe: $p" >&2
        exit 1
    fi
    if [ -d "$p" ] && [ -z "$(git ls-files -co --exclude-standard -- "$p")" ]; then
        echo "ERROR: path declarada para $SERVICO esta vazia (nenhum arquivo dentro): $p" >&2
        exit 1
    fi
done

# Par `<path> <blob>`, e não só o blob: nome de arquivo é comportamento aqui também —
# renomear `games_extractor.py` sem mudar uma linha quebra o import em
# `scripts/extract_games.py`, e um hash de conteúdo solto não veria isso.
#
# `git ls-files -co --exclude-standard`: rastreados (-c) + não-rastreados que o
# .gitignore não cobre (-o), que é exatamente o conjunto que o `docker build`/`gcloud
# run deploy --source` copiaria. Arquivo rastreado mas apagado do disco é pulado (o
# deploy também não o copiaria) — omiti-lo mantém o hash fiel ao disco.
hash_paths() {
    git ls-files -co --exclude-standard -- "$@" | while IFS= read -r f; do
        [ -f "$f" ] || continue
        printf '%s %s\n' "$f" "$(git hash-object "$f")"
    done | sort | git hash-object --stdin
}

HASH_NUCLEO=$(hash_paths "${NUCLEO_PATHS[@]}")
HASH_SVC=$(hash_paths "${SVC_PATHS[@]}")

case "$FLAG" in
    --nucleo)
        echo "$HASH_NUCLEO"
        ;;
    --svc)
        echo "$HASH_SVC"
        ;;
    --todos)
        # Os três numa chamada só, uma linha cada (combinado / núcleo / svc) — quem
        # precisa dos três (checa_deriva_servicos.sh, para classificar a CAUSA da
        # deriva) evita rodar o processo inteiro 3x por serviço (checagem de
        # existência das paths + `git ls-files`/`hash-object` repetidos à toa).
        hash_paths "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"
        echo "$HASH_NUCLEO"
        echo "$HASH_SVC"
        ;;
    "")
        # Combinado = hash sobre a UNIÃO das duas listas de paths, não sobre a
        # concatenação dos dois hashes já prontos — assim ele reproduz exatamente o
        # valor que hashear tudo de uma vez daria, e "a verdade é o combinado" (decisão A
        # do ADR) fica literal: nenhuma composição extra entre os componentes.
        hash_paths "${NUCLEO_PATHS[@]}" "${SVC_PATHS[@]}"
        ;;
    *)
        echo "ERROR: flag desconhecida: $FLAG (esperado --paths, --nucleo, --svc ou --todos)" >&2
        exit 2
        ;;
esac
