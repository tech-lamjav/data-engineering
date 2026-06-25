"""Cliente base com lógica comum de requisições HTTP."""
import time
import requests
from typing import Dict, Any, Optional, List
from src.config import API_TIMEOUT
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

_RETRY_BACKOFFS = [30, 60, 120, 240, 480]
# Teto (s) p/ Retry-After e backoff: evita dormir minutos/horas numa única chamada
# (Retry-After de cota diária pode vir gigante e estourar o timeout do Cloud Run).
_MAX_RETRY_WAIT = 120
# Status que justificam retry: rate limit (429) + erros transitórios de servidor (5xx).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class BaseClient:
    """Cliente base para requisições HTTP."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        auth_headers: Optional[Dict[str, str]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()

        # Auth via header dict has precedence (API-Football usa x-apisports-key).
        # Fallback: Authorization: Bearer (balldontlie).
        if auth_headers:
            self.session.headers.update(auth_headers)
        elif self.api_key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
            })

        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _execute_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        """Executa requisição HTTP com retry em 429/5xx e erros transitórios de conexão.

        Backoff exponencial com teto (_MAX_RETRY_WAIT); respeita Retry-After (limitado ao teto).
        """
        total_attempts = len(_RETRY_BACKOFFS) + 1
        for attempt in range(1, total_attempts + 1):
            is_last = attempt == total_attempts
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=API_TIMEOUT,
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ) as e:
                if is_last:
                    logger.error(f"Erro transitório persistente para {url}: {str(e)}")
                    raise
                wait = min(_RETRY_BACKOFFS[attempt - 1], _MAX_RETRY_WAIT)
                logger.warning(
                    f"{type(e).__name__} para {url} — aguardando {wait}s antes da tentativa {attempt + 1}/{total_attempts}"
                )
                time.sleep(wait)
                continue

            if response.status_code not in _RETRYABLE_STATUS:
                response.raise_for_status()
                return response

            if is_last:
                # esgotou as tentativas: deixa raise_for_status propagar o erro
                response.raise_for_status()
                return response

            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = min(int(retry_after), _MAX_RETRY_WAIT)
            else:
                wait = min(_RETRY_BACKOFFS[attempt - 1], _MAX_RETRY_WAIT)
            logger.warning(
                f"HTTP {response.status_code} para {url} — aguardando {wait}s antes da tentativa {attempt + 1}/{total_attempts}"
            )
            time.sleep(wait)

        # Inalcançável: o laço sempre retorna ou levanta. Mantém o contrato de tipo.
        raise RuntimeError(f"Retry esgotado para {url}")

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            return self._execute_request(method, url, params=params)
        except requests.exceptions.HTTPError as e:
            logger.error(f"Erro HTTP na requisição para {url}: {str(e)}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro na requisição para {url}: {str(e)}")
            raise

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        return self._make_request("GET", endpoint, params=params)
    
    def get_paginated(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        per_page: int = 100,
        max_items: Optional[int] = None,
        base_url_override: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Faz requisições paginadas e retorna todos os resultados consolidados.
        
        Args:
            endpoint: Endpoint da API
            params: Parâmetros da query string
            per_page: Itens por página
            max_items: Limite máximo de itens a coletar (opcional, para segurança)
            base_url_override: URL base alternativa (opcional, ex: para endpoints nba/v1)
        
        Returns:
            Lista consolidada de todos os itens
        """
        all_data = []
        page = 1
        cursor = None
        use_cursor_pagination = False
        base_url = (base_url_override or self.base_url).rstrip("/")

        # Copia defensiva: não mutar o dict do chamador (acrescentamos per_page/page/cursor).
        params = dict(params or {})
        params["per_page"] = per_page
        
        while True:
            # Remove parâmetros de paginação anteriores
            params.pop("page", None)
            params.pop("cursor", None)
            
            # Usa cursor se disponível, senão usa page
            if cursor:
                params["cursor"] = cursor
                logger.info(f"Buscando próximo cursor do endpoint {endpoint}...")
            else:
                params["page"] = page
                logger.info(f"Buscando página {page} do endpoint {endpoint}...")
            
            url = f"{base_url}/{endpoint.lstrip('/')}"
            response = self._execute_request("GET", url, params=params)
            data = response.json()
            
            # A API retorna dados em formato {'data': [...], 'meta': {...}}
            if isinstance(data, dict):
                items = data.get("data", [])
                meta = data.get("meta", {})
                
                if not items:
                    logger.info(f"Nenhum item encontrado. Finalizando paginação.")
                    break
                
                all_data.extend(items)
                logger.info(f"Coletados {len(items)} itens (total acumulado: {len(all_data)})")
                
                # Verifica limite máximo de segurança
                if max_items and len(all_data) >= max_items:
                    logger.warning(
                        f"Limite máximo de {max_items} itens atingido. "
                        f"Parando paginação para evitar coleta excessiva."
                    )
                    all_data = all_data[:max_items]
                    break
                
                # Log da meta e amostra de dados para debug (apenas na primeira iteração)
                if page == 1 and not cursor:
                    logger.info(f"Meta retornada pela API: {meta}")
                    if items:
                        # Log de amostra do primeiro item para verificar estrutura
                        sample_item = items[0]
                        logger.info(f"Amostra do primeiro item (campos): {list(sample_item.keys())[:10]}")
                        # Verifica se há campo indicando status ativo
                        if 'status' in sample_item or 'active' in sample_item or 'is_active' in sample_item:
                            logger.info(f"Campo de status encontrado no item")
                
                # Verifica tipo de paginação: cursor-based ou page-based
                next_cursor = meta.get("next_cursor")
                total_pages = meta.get("total_pages")
                
                if next_cursor is not None:
                    # API usa cursor-based pagination
                    if not use_cursor_pagination:
                        use_cursor_pagination = True
                        logger.info("API usa paginação por cursor. Mudando para modo cursor-based.")
                    
                    cursor = next_cursor
                    if not cursor:  # cursor vazio ou None indica fim
                        logger.info("Cursor vazio/nulo. Todas as páginas coletadas.")
                        break
                elif total_pages:
                    # API usa page-based pagination com total_pages
                    current_page = meta.get("current_page", page)
                    total_count = meta.get("total_count")
                    logger.info(f"Meta da API: página {current_page}/{total_pages}, total: {total_count}")
                    if page >= total_pages:
                        logger.info(f"Todas as páginas coletadas ({total_pages} páginas)")
                        break
                    page += 1
                else:
                    # Fallback: verifica se a página atual tem menos itens que per_page
                    # Isso indica que é a última página
                    if len(items) < per_page:
                        logger.info(f"Última página detectada (menos de {per_page} itens)")
                        break
                    # Continua paginação por página
                    page += 1
                
            else:
                # Se não for dict, assume que é uma lista
                if not data:
                    break
                all_data.extend(data)
                break
            
            time.sleep(0.5)  # Pequeno delay entre requisições
        
        logger.info(f"Total de {len(all_data)} itens coletados do endpoint {endpoint}")
        return all_data

