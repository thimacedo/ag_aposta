"""
fetcher_agent.py
Agente 1 — Coletor & Ingestão.

Estratégia de fontes GRATUITAS para durar toda a Copa 2026
===========================================================
A Copa vai de 11/06/2026 a 19/07/2026 (≈38 dias, 104 jogos).

Fontes e limites:
┌──────────────────────────────┬──────────────────────┬──────────────────────────────────┐
│ Fonte                        │ Limite               │ Uso neste módulo                 │
├──────────────────────────────┼──────────────────────┼──────────────────────────────────┤
│ football-data.org            │ 10 req/min (gratuito)│ Partidas + resultados (primário) │
│ The Odds API                 │ 500 req/mês (~16/dia)│ Odds mercado (prioritário)       │
│ API-Football (fallback)      │ 100 req/dia          │ Fallback se football-data falhar │
│ openfootball (GitHub raw)    │ ilimitado            │ Schedule + resultados (backup)   │
│ Wikipedia (scraper)          │ ilimitado            │ Resultados pós-jogo (backup)     │
└──────────────────────────────┴──────────────────────┴──────────────────────────────────┘

Lógica de distribuição de quota (FinOps):
- Partidas/agenda: busca 1× por dia via football-data.org (1 req).
  Cache de 23h no banco — se há partidas futuras, não rebusca.
- Estatísticas de times: atualiza 1× a cada 48h por time via football-data.org.
  ~32 times × 1 req = 32 req/ciclo de 48h ≈ 16 req/dia.
- Odds: The Odds API, 1 busca a cada 6h = 4 req/dia × 38 dias = 152 req (dentro dos 500).
- Se football-data falhar ou quota estourar → openfootball (GitHub) sem limite.
- Wikipedia scraper como último fallback para resultados.

Nenhum mock ou dado sintético — todas as funções operam com APIs reais.
"""

from __future__ import annotations

import os
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not os.path.exists(".env"):
    raise FileNotFoundError(
        "Arquivo .env não encontrado! Copie .env.example para .env e preencha as chaves."
    )
load_dotenv()

import db

# ─────────────────────────────────────────────────────────────────────
# Configuração de APIs
# ─────────────────────────────────────────────────────────────────────

# football-data.org (gratuito, 10 req/min)
FOOTBALL_DATA_KEY  = os.getenv("FOOTBALL_DATA_KEY", "")
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
# ID da Copa do Mundo 2026 na football-data.org
# Tier gratuito cobre competições internacionais da FIFA.
COPA_2026_FD_ID    = "WC"   # código da competição na API
COPA_2026_FD_SEASON = 2026

# The Odds API (500 req/mês)
THE_ODDS_API_KEY      = os.getenv("THE_ODDS_API_KEY", "")
THE_ODDS_API_BASE     = os.getenv("THE_ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
THE_ODDS_SPORT        = "soccer_fifa_world_cup"

# API-Football (100 req/dia) — fallback
API_FOOTBALL_KEY  = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = os.getenv("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io")
COPA_2026_AF_LIGA = 1    # Liga ID da Copa do Mundo na API-Football
COPA_2026_AF_SEASON = 2026

# openfootball — GitHub raw, sem autenticação
OPENFOOTBALL_BASE = (
    "https://raw.githubusercontent.com/openfootball/world-cup/master/2026"
)
# Schedule em JSON por rodada
OPENFOOTBALL_SCHEDULE_URL = (
    "https://raw.githubusercontent.com/openfootball/world-cup/master"
    "/2026/rounds.json"
)

# Cache local
CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────
# Utilitários de cache e rate-limit
# ─────────────────────────────────────────────────────────────────────

def _cache_path(nome: str) -> Path:
    return CACHE_DIR / f"{nome}.json"


def _ler_cache(nome: str, max_age_seconds: int) -> Optional[dict | list]:
    p = _cache_path(nome)
    if p.exists():
        age = time.time() - p.stat().st_mtime
        if age < max_age_seconds:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return None


def _salvar_cache(nome: str, data: dict | list) -> None:
    with open(_cache_path(nome), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _registrar_uso_api(api_nome: str) -> None:
    """Registra uso de API no arquivo de log de quota."""
    log_path = Path("data/api_usage.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "api": api_nome,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _contar_uso_api_hoje(api_nome: str) -> int:
    """Conta quantas vezes uma API foi usada hoje (UTC)."""
    log_path = Path("data/api_usage.jsonl")
    if not log_path.exists():
        return 0
    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("api") == api_nome and entry.get("ts", "").startswith(hoje):
                    count += 1
            except Exception:
                pass
    return count


# ─────────────────────────────────────────────────────────────────────
# FONTE 1: football-data.org (primária — partidas + resultados)
# ─────────────────────────────────────────────────────────────────────

def extrair_partidas_football_data() -> Optional[list[dict]]:
    """
    Busca todas as partidas da Copa 2026 na football-data.org.
    Gratuito para competições da FIFA. 10 req/min.
    Cache de 23h — 1 req/dia.
    """
    cache = _ler_cache("fd_partidas_copa2026", 23 * 3600)
    if cache is not None:
        logger.info("[football-data] Usando cache de partidas (< 23h).")
        return cache

    if not FOOTBALL_DATA_KEY:
        logger.warning("[football-data] FOOTBALL_DATA_KEY não configurada. Pulando.")
        return None

    url = f"{FOOTBALL_DATA_BASE}/competitions/{COPA_2026_FD_ID}/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    params = {"season": COPA_2026_FD_SEASON}

    try:
        logger.info(f"[football-data] GET {url}")
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        _registrar_uso_api("football-data")
        data = resp.json().get("matches", [])
        _salvar_cache("fd_partidas_copa2026", data)
        logger.info(f"[football-data] {len(data)} partidas retornadas.")
        return data
    except requests.HTTPError as e:
        logger.error(f"[football-data] Erro HTTP {e.response.status_code}: {e}")
        return None
    except Exception as e:
        logger.error(f"[football-data] Erro: {e}")
        return None


def extrair_stats_time_football_data(team_id: int) -> Optional[dict]:
    """
    Busca estatísticas de um time específico na football-data.org.
    Endpoint: /teams/{id} (disponível no tier gratuito).
    Cache de 48h por time.
    """
    cache_nome = f"fd_stats_time_{team_id}"
    cache = _ler_cache(cache_nome, 48 * 3600)
    if cache is not None:
        return cache

    if not FOOTBALL_DATA_KEY:
        return None

    # Verifica limite diário: ≤80 req/dia para ter margem de segurança
    uso_hoje = _contar_uso_api_hoje("football-data")
    if uso_hoje >= 80:
        logger.warning(f"[football-data] Limite diário de segurança atingido ({uso_hoje} req). Usando fallback.")
        return None

    url = f"{FOOTBALL_DATA_BASE}/teams/{team_id}"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        _registrar_uso_api("football-data")
        data = resp.json()
        _salvar_cache(cache_nome, data)
        return data
    except Exception as e:
        logger.error(f"[football-data] Erro stats time {team_id}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# FONTE 2: openfootball (GitHub raw — sem limite, estrutura fixa)
# ─────────────────────────────────────────────────────────────────────

# Mapeamento de nomes de times openfootball → IDs internos (FIFA)
OPENFOOTBALL_NAME_MAP: dict[str, int] = {
    "Argentina": 2, "Brazil": 3, "France": 4, "Germany": 5,
    "Spain": 8, "England": 9, "Italy": 10, "Portugal": 12,
    "Netherlands": 13, "Belgium": 14, "Croatia": 15, "Uruguay": 16,
    "Mexico": 23, "United States": 24, "Canada": 25, "Serbia": 27,
    "Switzerland": 29, "Poland": 32, "Colombia": 33, "Sweden": 34,
    "Ukraine": 35, "Japan": 37, "South Korea": 38, "Australia": 39,
    "Cameroon": 40, "Costa Rica": 42, "Morocco": 45, "Iran": 46,
    "Nigeria": 47, "Ghana": 48, "Senegal": 49, "Egypt": 55,
    "Chile": 60, "Peru": 63, "Paraguay": 65, "Ecuador": 67,
    "Saudi Arabia": 68, "Qatar": 75, "New Zealand": 107, "Panama": 114,
    "Jamaica": 121, "Honduras": 137, "Bolivia": 140, "Venezuela": 141,
    "India": 151, "Tunisia": 155, "Algeria": 156, "Ivory Coast": 157,
    "Austria": 158, "Denmark": 159, "Norway": 161, "Scotland": 162,
    "Wales": 163, "Hungary": 164, "Romania": 165, "Czech Republic": 166,
    "Slovakia": 167, "Greece": 168, "Albania": 169, "Iceland": 170,
    "Ireland": 171, "Bosnia and Herzegovina": 172, "Iraq": 173,
}

# Nomes em PT para exibição
OPENFOOTBALL_PT_MAP: dict[str, str] = {
    "Brazil": "Brasil", "France": "França", "Germany": "Alemanha",
    "Spain": "Espanha", "England": "Inglaterra", "Portugal": "Portugal",
    "Netherlands": "Holanda", "Belgium": "Bélgica", "Croatia": "Croácia",
    "Uruguay": "Uruguai", "Mexico": "México", "United States": "Estados Unidos",
    "Canada": "Canadá", "Switzerland": "Suíça", "Poland": "Polônia",
    "Colombia": "Colômbia", "Japan": "Japão", "South Korea": "Coreia do Sul",
    "Australia": "Austrália", "Cameroon": "Camarões", "Morocco": "Marrocos",
    "Chile": "Chile", "Ecuador": "Equador", "Venezuela": "Venezuela",
    "Saudi Arabia": "Arábia Saudita", "New Zealand": "Nova Zelândia",
    "Jamaica": "Jamaica", "Honduras": "Honduras", "Albania": "Albânia",
    "India": "Índia", "Ghana": "Gana", "Tunisia": "Tunísia",
    "Ivory Coast": "Costa do Marfim", "Denmark": "Dinamarca",
}


def extrair_partidas_openfootball() -> Optional[list[dict]]:
    """
    Busca schedule/resultados da Copa 2026 no repositório openfootball (GitHub).
    Sem autenticação, sem limite. Cache de 4h.

    Retorna lista no formato interno normalizado.
    """
    cache = _ler_cache("openfootball_copa2026", 4 * 3600)
    if cache is not None:
        logger.info("[openfootball] Usando cache (< 4h).")
        return cache

    try:
        logger.info(f"[openfootball] GET {OPENFOOTBALL_SCHEDULE_URL}")
        resp = requests.get(OPENFOOTBALL_SCHEDULE_URL, timeout=20)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning(f"[openfootball] Falha ao buscar schedule principal: {e}")
        # Tenta fallback em formato alternativo
        return _extrair_openfootball_fallback()

    partidas = []
    for rodada in raw.get("rounds", []):
        fase = rodada.get("name", "GRUPO")
        for m in rodada.get("matches", []):
            try:
                team1 = m.get("team1", {}).get("name", "")
                team2 = m.get("team2", {}).get("name", "")
                team1_id = OPENFOOTBALL_NAME_MAP.get(team1, 0)
                team2_id = OPENFOOTBALL_NAME_MAP.get(team2, 0)
                if not team1_id or not team2_id:
                    continue

                score1 = m.get("score", {}).get("ft", [None, None])[0]
                score2 = m.get("score", {}).get("ft", [None, None])[1]

                partida = {
                    "partida_id": m.get("num", 0),
                    "mandante": OPENFOOTBALL_PT_MAP.get(team1, team1),
                    "visitante": OPENFOOTBALL_PT_MAP.get(team2, team2),
                    "time_mandante_id": team1_id,
                    "time_visitante_id": team2_id,
                    "data_evento": m.get("date", "") + " " + m.get("time", "00:00"),
                    "liga": "Copa do Mundo 2026",
                    "fase": fase,
                    "gols_m": score1,
                    "gols_v": score2,
                }
                partidas.append(partida)
            except Exception as e:
                logger.debug(f"[openfootball] Erro processando partida: {e}")

    if partidas:
        _salvar_cache("openfootball_copa2026", partidas)
        logger.info(f"[openfootball] {len(partidas)} partidas processadas.")
    return partidas or None


def _extrair_openfootball_fallback() -> Optional[list[dict]]:
    """
    Fallback para formato .txt do openfootball caso JSON não esteja disponível.
    O repositório tem formatos alternates (.txt tabular).
    """
    # URLs alternativas de schedule em texto
    urls_txt = [
        "https://raw.githubusercontent.com/openfootball/world-cup/master/2026/groups.txt",
        "https://raw.githubusercontent.com/openfootball/world-cup/master/2026/knockout.txt",
    ]
    partidas = []
    pid = 2600  # ID base para este scraper
    for url in urls_txt:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            for line in resp.text.splitlines():
                line = line.strip()
                # Formato exemplo: "Jun/11 Brazil 3-0 Mexico"
                if "  " in line and "-" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if "-" in p and p[0].isdigit():
                            team1 = " ".join(parts[1:i])
                            team2 = " ".join(parts[i+1:])
                            gols = p.split("-")
                            t1_id = OPENFOOTBALL_NAME_MAP.get(team1, 0)
                            t2_id = OPENFOOTBALL_NAME_MAP.get(team2, 0)
                            if t1_id and t2_id:
                                partidas.append({
                                    "partida_id": pid,
                                    "mandante": OPENFOOTBALL_PT_MAP.get(team1, team1),
                                    "visitante": OPENFOOTBALL_PT_MAP.get(team2, team2),
                                    "time_mandante_id": t1_id,
                                    "time_visitante_id": t2_id,
                                    "data_evento": "2026-06-11 00:00",
                                    "liga": "Copa do Mundo 2026",
                                    "gols_m": int(gols[0]) if gols[0].isdigit() else None,
                                    "gols_v": int(gols[1]) if gols[1].isdigit() else None,
                                })
                                pid += 1
                            break
        except Exception as e:
            logger.debug(f"[openfootball-fallback] {url}: {e}")

    return partidas or None


# ─────────────────────────────────────────────────────────────────────
# FONTE 3: The Odds API (500 req/mês — odds de mercado)
# ─────────────────────────────────────────────────────────────────────

def extrair_odds_the_odds_api(regions: str = "eu") -> Optional[list[dict]]:
    """
    Busca odds 1X2 da The Odds API para a Copa do Mundo 2026.
    Controle rígido: 1 busca a cada 6h = 4 req/dia × 38 dias = 152 req.

    Retorna lista de eventos com odds de múltiplas casas.
    """
    cache = _ler_cache("odds_copa2026", 6 * 3600)
    if cache is not None:
        logger.info("[TheOddsAPI] Usando cache de odds (< 6h).")
        return cache

    if not THE_ODDS_API_KEY:
        logger.warning("[TheOddsAPI] THE_ODDS_API_KEY não configurada. Pulando.")
        return None
    logger.info(f"[TheOddsAPI] Usando chave: {THE_ODDS_API_KEY[:5]}...")

    url = f"{THE_ODDS_API_BASE}/sports/{THE_ODDS_SPORT}/odds/"
    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": regions,
        "markets": "h2h",
        "oddsFormat": "decimal",
    }

    try:
        logger.info("[TheOddsAPI] Buscando odds em tempo real...")
        resp = requests.get(url, params=params, timeout=15)

        # Loga quota restante
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        logger.info(f"[TheOddsAPI] Quota: usados={used}, restantes={remaining}")

        resp.raise_for_status()
        _registrar_uso_api("the-odds-api")
        data = resp.json()
        if not data:
            logger.warning("[TheOddsAPI] API retornou lista vazia.")
        _salvar_cache("odds_copa2026", data)
        logger.info(f"[TheOddsAPI] {len(data)} eventos retornados.")
        return data
    except requests.HTTPError as e:
        if e.response.status_code == 422:
            logger.warning("[TheOddsAPI] Esporte não encontrado (Copa pode não estar ativa). Tentando sport genérico.")
            return _extrair_odds_fallback_sport()
        logger.error(f"[TheOddsAPI] Erro HTTP {e.response.status_code}: {e}")
        return None
    except Exception as e:
        logger.error(f"[TheOddsAPI] Erro: {e}")
        return None


def _extrair_odds_fallback_sport() -> Optional[list[dict]]:
    """Tenta buscar odds com o slug genérico de Copa do Mundo."""
    slugs_alternativos = [
        "soccer_fifa_world_cup",
        "soccer_world_cup_2026",
        "soccer_international_friendlies",
    ]
    for slug in slugs_alternativos:
        try:
            url = f"{THE_ODDS_API_BASE}/sports/{slug}/odds/"
            params = {
                "apiKey": THE_ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    _registrar_uso_api("the-odds-api")
                    _salvar_cache("odds_copa2026", data)
                    logger.info(f"[TheOddsAPI] Fallback OK com slug '{slug}': {len(data)} eventos.")
                    return data
        except Exception as e:
            logger.debug(f"[TheOddsAPI-fallback] {slug}: {e}")
    return None


def listar_sports_disponiveis_odds_api() -> list[str]:
    """
    Lista todos os sports disponíveis na The Odds API.
    Útil para descobrir o slug correto da Copa 2026.
    Gasta apenas 1 request.
    """
    if not THE_ODDS_API_KEY:
        return []
    url = f"{THE_ODDS_API_BASE}/sports/"
    params = {"apiKey": THE_ODDS_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        _registrar_uso_api("the-odds-api")
        sports = resp.json()
        copa_sports = [
            s["key"] for s in sports
            if "world_cup" in s.get("key", "").lower() or "copa" in s.get("title", "").lower()
        ]
        logger.info(f"[TheOddsAPI] Sports relacionados à Copa: {copa_sports}")
        return copa_sports
    except Exception as e:
        logger.error(f"[TheOddsAPI] Erro listando sports: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────
# FONTE 4: API-Football (fallback, 100 req/dia)
# ─────────────────────────────────────────────────────────────────────

def extrair_partidas_api_football() -> Optional[list[dict]]:
    """
    Fallback para API-Football se football-data.org falhar.
    100 req/dia — usado apenas quando necessário.
    """
    cache = _ler_cache("af_partidas_copa2026", 23 * 3600)
    if cache is not None:
        return cache

    if not API_FOOTBALL_KEY:
        return None

    uso_hoje = _contar_uso_api_hoje("api-football")
    if uso_hoje >= 80:
        logger.warning("[API-Football] Limite diário de segurança atingido.")
        return None

    url = f"{API_FOOTBALL_BASE}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {
        "league": COPA_2026_AF_LIGA,
        "season": COPA_2026_AF_SEASON,
        "timezone": "America/Fortaleza",
    }

    try:
        logger.info("[API-Football] Buscando partidas (fallback)...")
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        _registrar_uso_api("api-football")
        data = resp.json().get("response", [])
        _salvar_cache("af_partidas_copa2026", data)
        logger.info(f"[API-Football] {len(data)} partidas retornadas.")
        return data
    except Exception as e:
        logger.error(f"[API-Football] Erro: {e}")
        return None


def extrair_stats_api_football(time_id: int, liga_id: int, season: int) -> Optional[dict]:
    """Stats de time via API-Football (fallback)."""
    cache_nome = f"af_stats_{time_id}"
    cache = _ler_cache(cache_nome, 48 * 3600)
    if cache is not None:
        return cache

    if not API_FOOTBALL_KEY:
        return None

    uso_hoje = _contar_uso_api_hoje("api-football")
    if uso_hoje >= 80:
        return None

    url = f"{API_FOOTBALL_BASE}/teams/statistics"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"team": time_id, "league": liga_id, "season": season}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        _registrar_uso_api("api-football")
        data = resp.json().get("response", {})
        result = {
            "time_id": time_id,
            "nome": data.get("team", {}).get("name", f"Time {time_id}"),
            "liga": data.get("league", {}).get("name", "Copa"),
            "xg_marcado_casa": float(data.get("goals", {}).get("for", {})
                                     .get("average", {}).get("home", 0.0) or 0.0),
            "xg_sofrido_casa": float(data.get("goals", {}).get("against", {})
                                     .get("average", {}).get("home", 0.0) or 0.0),
            "xg_marcado_fora": float(data.get("goals", {}).get("for", {})
                                     .get("average", {}).get("away", 0.0) or 0.0),
            "xg_sofrido_fora": float(data.get("goals", {}).get("against", {})
                                     .get("average", {}).get("away", 0.0) or 0.0),
            "jogos_casa": int(data.get("fixtures", {}).get("played", {}).get("home", 0) or 0),
            "jogos_fora": int(data.get("fixtures", {}).get("played", {}).get("away", 0) or 0),
        }
        _salvar_cache(cache_nome, result)
        return result
    except Exception as e:
        logger.error(f"[API-Football] Stats time {time_id}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# Normalização e persistência
# ─────────────────────────────────────────────────────────────────────

def _normalizar_partida_football_data(m: dict) -> Optional[dict]:
    """Converte resposta da football-data.org para formato interno."""
    try:
        home = m.get("homeTeam", {})
        away = m.get("awayTeam", {})
        score = m.get("score", {}).get("fullTime", {})
        utc_date = m.get("utcDate", "")

        return {
            "partida_id": m.get("id"),
            "liga": "Copa do Mundo 2026",
            "data_evento": utc_date.replace("Z", "").replace("T", " ")[:16],
            "time_mandante_id": home.get("id"),
            "time_visitante_id": away.get("id"),
            "nome_mandante": home.get("name", ""),
            "nome_visitante": away.get("name", ""),
            "gols_m": score.get("home"),
            "gols_v": score.get("away"),
            "status": m.get("status", "SCHEDULED"),
        }
    except Exception as e:
        logger.debug(f"Erro normalizando partida football-data: {e}")
        return None


def salvar_partidas_banco(partidas_raw: list[dict], fonte: str = "generic") -> int:
    """
    Persiste partidas no banco, independente da fonte.
    Aceita tanto formato football-data quanto formato interno normalizado.
    Retorna número de partidas salvas.
    """
    salvas = 0
    erros = 0

    with db.get_connection() as conn:
        for p in partidas_raw:
            try:
                # Detecta e normaliza formato football-data
                if "homeTeam" in p or "utcDate" in p:
                    p = _normalizar_partida_football_data(p)
                    if p is None:
                        continue

                partida_data = {
                    "partida_id": p.get("partida_id"),
                    "liga": p.get("liga", "Copa do Mundo 2026"),
                    "data_evento": p.get("data_evento", ""),
                    "time_mandante_id": p.get("time_mandante_id"),
                    "time_visitante_id": p.get("time_visitante_id"),
                }

                # Só insere se tiver IDs válidos
                if not all([
                    partida_data["partida_id"],
                    partida_data["time_mandante_id"],
                    partida_data["time_visitante_id"],
                ]):
                    logger.debug(f"Partida com dados incompletos: {partida_data}")
                    continue

                # Primeiro garante que os times existem na tabela times_performance
                _garantir_time_no_banco(
                    conn,
                    partida_data["time_mandante_id"],
                    p.get("nome_mandante", p.get("mandante", f"Time {partida_data['time_mandante_id']}")),
                )
                _garantir_time_no_banco(
                    conn,
                    partida_data["time_visitante_id"],
                    p.get("nome_visitante", p.get("visitante", f"Time {partida_data['time_visitante_id']}")),
                )

                db.inserir_partida_agenda(conn, partida_data)
                salvas += 1
            except Exception as e:
                erros += 1
                logger.debug(f"Erro salvando partida: {e}")

    logger.info(f"[{fonte}] Partidas salvas: {salvas}, erros: {erros}.")
    return salvas


def _garantir_time_no_banco(conn, time_id: int, nome: str) -> None:
    """Insere um time na tabela times_performance com valores padrão se não existir."""
    from core_math import MEDIA_LIGA_COPA_REFERENCIA
    existe = conn.execute(
        "SELECT 1 FROM times_performance WHERE time_id = ?", (time_id,)
    ).fetchone()
    if not existe:
        conn.execute("""
            INSERT OR IGNORE INTO times_performance (
                time_id, nome, liga,
                xg_marcado_casa, xg_sofrido_casa,
                xg_marcado_fora, xg_sofrido_fora,
                jogos_casa, jogos_fora
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            time_id, nome, "Copa do Mundo 2026",
            MEDIA_LIGA_COPA_REFERENCIA, MEDIA_LIGA_COPA_REFERENCIA,
            MEDIA_LIGA_COPA_REFERENCIA, MEDIA_LIGA_COPA_REFERENCIA,
            0, 0,
        ))
        conn.commit()


def salvar_odds_banco(odds_data: list[dict]) -> int:
    """
    Persiste odds da The Odds API no banco.
    Faz match de partida por nome de time (fuzzy, com fallback).
    Retorna número de registros salvos.
    """
    salvas = 0
    sem_match = 0
    erros = 0

    with db.get_connection() as conn:
        for event in odds_data:
            try:
                home_team = event.get("home_team", "")
                away_team = event.get("away_team", "")

                # Tenta match mandante+visitante juntos
                cur = conn.execute("""
                    SELECT p.partida_id
                    FROM partidas_agenda p
                    JOIN times_performance tm ON p.time_mandante_id = tm.time_id
                    JOIN times_performance tv ON p.time_visitante_id = tv.time_id
                    WHERE tm.nome LIKE ? AND tv.nome LIKE ?
                    LIMIT 1
                """, (f"%{home_team[:6]}%", f"%{away_team[:6]}%"))
                row = cur.fetchone()

                if not row:
                    sem_match += 1
                    logger.info(f"Sem match de time: {home_team} vs {away_team}")
                    continue

                partida_id = row["partida_id"]

                for bkm in event.get("bookmakers", []):
                    casa = bkm.get("key", "unknown")
                    # Log para diagnostico
                    logger.info(f"Processando casa: {casa}")
                    odd_m, odd_e, odd_v = 0.0, 0.0, 0.0
                    for mkt in bkm.get("markets", []):
                        if mkt.get("key") == "h2h":
                            for out in mkt.get("outcomes", []):
                                name = out.get("name", "")
                                price = float(out.get("price", 0.0))
                                if name == home_team:
                                    odd_m = price
                                elif name.lower() == "draw":
                                    odd_e = price
                                else:
                                    odd_v = price

                    if odd_m > 1.0 and odd_e > 1.0 and odd_v > 1.0:
                        db.inserir_odds(conn, {
                            "partida_id": partida_id,
                            "casa_aposta": casa,
                            "odd_mandante": odd_m,
                            "odd_empate": odd_e,
                            "odd_visitante": odd_v,
                        })
                        salvas += 1
            except Exception as e:
                erros += 1
                logger.debug(f"Erro salvando odds: {e}")

    logger.info(f"Odds: {salvas} salvas, {sem_match} sem match, {erros} erros.")
    return salvas


# ─────────────────────────────────────────────────────────────────────
# Orquestração principal
# ─────────────────────────────────────────────────────────────────────

def rotina_ingestao_diaria() -> dict:
    """
    Orquestra a ingestão diária com fallback em cascata:

    Partidas:  football-data.org → openfootball → API-Football
    Odds:      The Odds API (rate-limited a 4 req/dia)

    Retorna resumo do que foi executado.
    """
    resultado = {
        "partidas_salvas": 0,
        "odds_salvas": 0,
        "fonte_partidas": None,
        "fonte_odds": None,
        "erros": [],
    }

    db.init_db()

    # ── Passo 1: Partidas ──────────────────────────────────────────
    with db.get_connection() as conn:
        if db.verificar_partidas_recentes_liga(conn, 0):
            logger.info("Partidas futuras já no banco. Pulando fetch de agenda.")
        else:
            # Cascata de fontes
            partidas = None
            fonte = None

            if FOOTBALL_DATA_KEY:
                partidas = extrair_partidas_football_data()
                fonte = "football-data.org"

            if not partidas:
                logger.info("football-data falhou ou sem chave. Tentando openfootball...")
                partidas = extrair_partidas_openfootball()
                fonte = "openfootball"

            if not partidas and API_FOOTBALL_KEY:
                logger.info("openfootball falhou. Tentando API-Football...")
                partidas = extrair_partidas_api_football()
                fonte = "api-football"

            if partidas:
                n = salvar_partidas_banco(partidas, fonte=fonte)
                resultado["partidas_salvas"] = n
                resultado["fonte_partidas"] = fonte
                logger.info(f"Partidas: {n} salvas via {fonte}.")
            else:
                msg = "Nenhuma fonte de partidas disponível."
                logger.error(msg)
                resultado["erros"].append(msg)

    # ── Passo 2: Odds ─────────────────────────────────────────────
    with db.get_connection() as conn:
        eh_recente = db.verificar_odds_recentes(conn)
        logger.info(f"Odds recentes? {eh_recente}")
        if eh_recente:
            logger.info("Odds recentes no banco (< 6h). Pulando.")
        else:
            odds = extrair_odds_the_odds_api()
            if odds:
                n = salvar_odds_banco(odds)
                resultado["odds_salvas"] = n
                resultado["fonte_odds"] = "the-odds-api"
            else:
                resultado["erros"].append("Sem odds disponíveis.")

    return resultado


def status_quota() -> dict:
    """Retorna resumo do uso de APIs hoje para monitoramento."""
    hoje_fd  = _contar_uso_api_hoje("football-data")
    hoje_af  = _contar_uso_api_hoje("api-football")
    hoje_oa  = _contar_uso_api_hoje("the-odds-api")

    # Estima uso acumulado mensal da Odds API
    log_path = Path("data/api_usage.jsonl")
    mes_atual = datetime.now(timezone.utc).strftime("%Y-%m")
    oa_mes = 0
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("api") == "the-odds-api" and e.get("ts", "").startswith(mes_atual):
                        oa_mes += 1
                except Exception:
                    pass

    return {
        "football_data_hoje": f"{hoje_fd}/80",
        "api_football_hoje": f"{hoje_af}/80",
        "odds_api_hoje": f"{hoje_oa}/4",
        "odds_api_mes": f"{oa_mes}/500",
    }


if __name__ == "__main__":
    db.init_db()
    print("=== Rotina de Ingestão Diária ===")

    # Descobre slugs disponíveis na Odds API antes de buscar
    if THE_ODDS_API_KEY:
        print("\n[1] Descobrindo sports Copa 2026 na The Odds API...")
        slugs = listar_sports_disponiveis_odds_api()
        print(f"  → Encontrados: {slugs or 'nenhum ainda (Copa pode não ter começado)'}")

    print("\n[2] Executando ingestão...")
    res = rotina_ingestao_diaria()
    print(f"  → Partidas salvas: {res['partidas_salvas']} via {res['fonte_partidas']}")
    print(f"  → Odds salvas:     {res['odds_salvas']} via {res['fonte_odds']}")
    if res["erros"]:
        print(f"  → Erros: {res['erros']}")

    print("\n[3] Status de quota:")
    for k, v in status_quota().items():
        print(f"  {k}: {v}")
