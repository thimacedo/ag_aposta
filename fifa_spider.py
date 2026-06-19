#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
FIFA World Cup 2026 - Spider de Captura Periódica
================================================================================

Captura, a cada 3 horas, o conteúdo da página de scores & fixtures da
Copa do Mundo 2026 (Canada/Mexico/USA) no site da FIFA.

Para cada execução:
  1. Abre a página com Playwright (headless Chromium)
  2. Aguarda o carregamento dinâmico (React com hidratação)
  3. Faz scroll progressivo para garantir que todas as partidas carreguem
  4. Extrai as partidas via parser DOM específico (classes match-row_*)
  5. Salva:
       - JSON estruturado em  captures/capture_YYYY-MM-DD_HHMMSS.json
       - HTML bruto em      raw_html/raw_YYYY-MM-DD_HHMMSS.html  (backup)
  6. Compara com a última execução e registra mudanças em logs/changes.log
  7. Atualiza logs/spider.log com execução geral

Uso:
    python fifa_spider.py            # Modo daemon (roda a cada 3h)
    python fifa_spider.py --once     # Executa uma única vez e sai
    python fifa_spider.py --test     # Modo teste: roda 1x a cada 30s (debug)
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import signal
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Dependências
# --------------------------------------------------------------------------- #
try:
    import schedule  # type: ignore
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore
except ImportError as e:
    print(f"Dependência ausente: {e}", file=sys.stderr)
    print("Instale com: pip install schedule playwright beautifulsoup4",
          file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
BASE_URL = (
    "https://www.fifa.com/pt/tournaments/mens/worldcup/"
    "canadamexicousa2026/scores-fixtures"
)
QUERY_PARAMS = "?country=BR&wtw-filter=ALL"
TARGET_URL = f"{BASE_URL}{QUERY_PARAMS}"

# Caminhos (relativos ao diretório deste script)
SPIDER_ROOT = Path(__file__).resolve().parent / "fifa_spider"
CAPTURES_DIR = SPIDER_ROOT / "captures"
RAW_HTML_DIR = SPIDER_ROOT / "raw_html"
LOGS_DIR = SPIDER_ROOT / "logs"
STATE_FILE = SPIDER_ROOT / "last_state.json"

for d in (CAPTURES_DIR, RAW_HTML_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

BRT = timezone(timedelta(hours=-3))
INTERVAL_HOURS = 3

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def setup_logging() -> logging.Logger:
    log_file = LOGS_DIR / "spider.log"
    logger = logging.getLogger("fifa-spider")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.propagate = False  # evita duplicação de logs no worker_ingest

    return logger


log = setup_logging()


# --------------------------------------------------------------------------- #
# 1. Captura da página (Playwright)
# --------------------------------------------------------------------------- #
def fetch_page_html(url: str = TARGET_URL) -> str:
    log.info("→ Abrindo página: %s", url)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        # Bloqueia recursos pesados que não afetam o conteúdo de jogos
        context.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,mp4}",
            lambda route: route.abort(),
        )

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            log.warning("Timeout no goto; tentando continuar mesmo assim")

        # Espera seletor genérico de match/fixture aparecer
        for selector in [
            "div[class*='match-row_matchRowContainer']",
            "[class*='match-row']",
            "[class*='fixture']",
            "#root",
        ]:
            try:
                page.wait_for_selector(selector, timeout=10000)
                log.info("Seletor encontrado: %s", selector)
                break
            except PWTimeout:
                continue

        # Tempo extra para hidratação do React
        page.wait_for_timeout(2500)

        # Scroll progressivo para carregar jogos lazy-loaded
        last_height = 0
        for _ in range(10):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(700)
            curr = page.evaluate("document.body.scrollHeight")
            if curr == last_height:
                break
            last_height = curr
        log.info("Scroll finalizado. Altura da página: %d px", last_height)

        page.wait_for_timeout(1500)

        html = page.content()
        browser.close()
        log.info("HTML capturado: %d bytes", len(html))
        return html


# --------------------------------------------------------------------------- #
# 2. Parser DOM específico para FIFA.com
# --------------------------------------------------------------------------- #
# Estrutura descoberta por inspeção do HTML real:
#
#   <div class="match-row_matchRowContainer__NoCRI">
#     <div class="match-row_matchRowBody__yc8mV">
#       <div class="match-row_team__y5Rva justify-content-end">
#         <div class="team-abbreviations_container__wWtDG">MEX</div>
#         <span class="d-none d-md-block">México</span>
#         <div class="match-centre-logo-component_TeamLogoWrapper__P3lPN">
#           <img srcset="...flags-sq-1/MEX 1x" />
#         </div>
#       </div>
#       <div class="match-row_matchRowStatus__AJE7s">
#         <!-- Partida passada: -->
#         <span class="match-row_score__wfcQP match-row_scoreWinner__KB4p-">2</span>
#         <div class="match-row_status__kFtCL">
#           <span class="match-row_statusLabel__AiSA3">FIM</span>
#         </div>
#         <span class="match-row_score__wfcQP match-row_scoreLoser__vNbgU">0</span>
#         <!-- OU partida futura: -->
#         <span class="match-row_matchTime__9QJXJ">16:00</span>
#       </div>
#       <div class="match-row_team__y5Rva">
#         <!-- time visitante (mesma estrutura) -->
#       </div>
#     </div>
#     <div class="match-row_bottomLabelWrapper__9iAmu">
#       <span class="match-row_bottomLabel__ni63b justify-content-end">Primeira fase</span>
#       <span>·</span>
#       <div class="match-row_statiumCityWrapper__G8ygZ">
#         <span class="match-row_bottomLabel__ni63b">Grupo A</span>
#         <span>·</span>
#         <div class="match-row_stadiumCityLabels__zjXUq">
#           <span>Estádio da Cidade do México</span>
#           <span>(Cidade do México)</span>
#         </div>
#       </div>
#     </div>
#   </div>

_DATE_RE = re.compile(
    r"^(domingo|segunda-feira|terça-feira|quarta-feira|"
    r"quinta-feira|sexta-feira|sábado)\s+"
    r"(\d{1,2})\s+"
    r"(janeiro|fevereiro|março|abril|maio|junho|"
    r"julho|agosto|setembro|outubro|novembro|dezembro)\s+"
    r"(2026)\s*",
    re.IGNORECASE,
)

_PT_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

_KNOCKOUT_PHASES = {
    "oitavas de final", "quartas de final", "semifinal",
    "decisão do 3º lugar", "final", "oitavas-de-final",
}


def _find_date_for_match(match_element) -> Optional[str]:
    """Sobe na árvore DOM até encontrar um container com a data."""
    node = match_element
    for _ in range(15):
        if node is None or not hasattr(node, "name") or node.name is None:
            return None
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else ""
        m = _DATE_RE.match(text)
        if m:
            day = int(m.group(2))
            month = _PT_MONTHS.get(m.group(3).lower())
            year = int(m.group(4))
            if month:
                return f"{year:04d}-{month:02d}-{day:02d}"
        for child in node.find_all(["div", "span", "h2", "h3", "p"], limit=30):
            child_text = child.get_text(" ", strip=True)
            m = _DATE_RE.match(child_text)
            if m:
                day = int(m.group(2))
                month = _PT_MONTHS.get(m.group(3).lower())
                year = int(m.group(4))
                if month:
                    return f"{year:04d}-{month:02d}-{day:02d}"
        node = node.parent
    return None


def _extract_team(team_div) -> dict:
    """Extrai {code, name, flag_url} de um div.match-row_team__y5Rva."""
    out = {"code": None, "name": None, "flag_url": None}
    if team_div is None:
        return out

    # Código (3 letras ou W95/RU101)
    code_el = team_div.find(
        "div", class_=re.compile(r"team-abbreviations_container")
    )
    if code_el:
        out["code"] = code_el.get_text(strip=True)

    # Nome: dentro de span.d-none.d-md-block
    name_el = team_div.find("span", class_=re.compile(r"d-none.*d-md-block"))
    if name_el:
        out["name"] = name_el.get_text(strip=True)
    elif out["code"]:
        # Fallback para placeholders (W95, RU101, etc.)
        out["name"] = out["code"]

    # Flag URL: srcset do <img>
    img = team_div.find("img")
    if img:
        srcset = img.get("srcset", "") or img.get("src", "")
        if srcset:
            m = re.search(r"(https?://\S+?)(?:\s+\d+x)?\s*$", srcset.strip())
            if m:
                out["flag_url"] = m.group(1)
            else:
                out["flag_url"] = srcset.split()[0]

    return out


def _extract_status_and_score(status_div) -> tuple:
    """
    Extrai (status, score_home, score_away, kick_off_time) do
    div.match-row_matchRowStatus__AJE7s.
    """
    if status_div is None:
        return None, None, None, None

    score_home = None
    score_away = None
    status = None
    kick_off = None

    # 1) Partida futura: span.match-row_matchTime__
    time_span = status_div.find(
        "span", class_=re.compile(r"match-row_matchTime")
    )
    if time_span:
        kick_off = time_span.get_text(strip=True)
        return None, None, None, kick_off

    # 2) Partida passada: spans.match-row_score__
    score_spans = status_div.find_all(
        "span", class_=re.compile(r"match-row_score")
    )
    if len(score_spans) >= 1:
        score_home = score_spans[0].get_text(strip=True)
    if len(score_spans) >= 2:
        score_away = score_spans[1].get_text(strip=True)

    # 3) Status (FIM, AO VIVO, etc.)
    status_label = status_div.find(
        "div", class_=re.compile(r"match-row_status__")
    )
    if status_label:
        status = status_label.get_text(strip=True)

    # 4) Heurística de segurança: se score_home é HH:MM, é kick_off
    if score_home and re.fullmatch(r"\d{1,2}:\d{2}", score_home):
        kick_off = score_home
        score_home = None
        score_away = None
        status = None

    return status, score_home, score_away, kick_off


def _extract_phase_group_stadium(bottom_div) -> tuple:
    """Extrai (phase, group, stadium, city) do div.match-row_bottomLabelWrapper__."""
    if bottom_div is None:
        return None, None, None, None

    phase = None
    group = None
    stadium = None
    city = None

    bottom_labels = bottom_div.find_all(
        "span", class_=re.compile(r"match-row_bottomLabel")
    )
    if len(bottom_labels) >= 1:
        phase = bottom_labels[0].get_text(strip=True)
    if len(bottom_labels) >= 2:
        group = bottom_labels[1].get_text(strip=True)

    sc_div = bottom_div.find(
        "div", class_=re.compile(r"match-row_stadiumCityLabels")
    )
    if sc_div:
        spans = sc_div.find_all("span")
        if len(spans) >= 1:
            stadium = spans[0].get_text(strip=True)
        if len(spans) >= 2:
            city = spans[1].get_text(strip=True).strip("()")

    # Se 'group' é na verdade uma fase mata-mata, move para phase
    if group and group.lower() in _KNOCKOUT_PHASES:
        phase = group
        group = None
    if phase and phase.lower().startswith("grupo "):
        group = phase
        phase = None

    return phase, group, stadium, city


def parse_matches(html: str) -> tuple:
    """
    Parser principal baseado na estrutura DOM da FIFA.com.
    Retorna (matches, next_data_or_none).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Containers de partida — classe tem hash dinâmico (ex: match-row_matchRowContainer__NoCRI)
    containers = soup.find_all(
        "div", class_=re.compile(r"^match-row_matchRowContainer__")
    )
    log.info("Containers de partida encontrados: %d", len(containers))

    matches = []
    for idx, c in enumerate(containers, start=1):
        bottom = c.find(
            "div", class_=re.compile(r"^match-row_bottomLabelWrapper__")
        )

        # Teams: dois divs.match-row_team__
        team_divs = c.find_all(
            "div", class_=re.compile(r"^match-row_team__")
        )
        home_team_div = team_divs[0] if len(team_divs) >= 1 else None
        away_team_div = team_divs[1] if len(team_divs) >= 2 else None

        home_team = _extract_team(home_team_div)
        away_team = _extract_team(away_team_div)

        # Status / scores / kick-off
        status_div = c.find(
            "div", class_=re.compile(r"^match-row_matchRowStatus__")
        )
        status, score_home, score_away, kick_off = (
            _extract_status_and_score(status_div)
        )

        phase, group, stadium, city = _extract_phase_group_stadium(bottom)

        date_iso = _find_date_for_match(c)

        # Match ID estável: baseado em data + times + índice
        mid_seed = (
            f"{date_iso}|{home_team.get('code')}|"
            f"{away_team.get('code')}|{idx}"
        )
        match_id = hashlib.md5(mid_seed.encode("utf-8")).hexdigest()[:16]

        match = {
            "match_id": match_id,
            "match_index": idx,
            "home_team": home_team,
            "away_team": away_team,
            "score_home": score_home,
            "score_away": score_away,
            "status": status,            # 'FIM', 'AO VIVO', None
            "kick_off_time": kick_off,   # 'HH:MM' para partidas futuras
            "phase": phase,              # 'Primeira fase', 'Quartas de final'
            "group": group,              # 'Grupo A', None para mata-mata
            "stadium": stadium,
            "city": city,
            "date": date_iso,            # 'YYYY-MM-DD'
            "source_url": TARGET_URL,
        }
        matches.append(match)

    log.info("Partidas parseadas com sucesso: %d", len(matches))

    # Tenta extrair __NEXT_DATA__ apenas como anexo (debug)
    next_data = None
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            next_data = json.loads(script.string)
        except json.JSONDecodeError:
            pass

    return matches, next_data


# --------------------------------------------------------------------------- #
# 3. Persistência (JSON + raw HTML)
# --------------------------------------------------------------------------- #
def save_capture(matches: list, next_data: Optional[dict],
                 raw_html: str) -> Path:
    """Salva um snapshot completo da execução."""
    now = datetime.now(BRT)
    ts_filename = now.strftime("%Y-%m-%d_%H%M%S")
    ts_iso = now.isoformat()

    played = sum(1 for m in matches if m.get("status") == "FIM")
    upcoming = sum(1 for m in matches if m.get("kick_off_time"))
    live = sum(1 for m in matches
               if m.get("status") and m.get("status") != "FIM"
               and m.get("kick_off_time") is None)

    capture = {
        "capture_timestamp": ts_iso,
        "source_url": TARGET_URL,
        "capture_count_total": _total_capture_count() + 1,
        "stats": {
            "matches_found": len(matches),
            "matches_played": played,
            "matches_upcoming": upcoming,
            "matches_live": live,
            "html_size_bytes": len(raw_html),
            "next_data_available": next_data is not None,
        },
        "matches": matches,
    }
    json_path = CAPTURES_DIR / f"capture_{ts_filename}.json"
    json_path.write_text(
        json.dumps(capture, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("JSON salvo: %s (%d partidas | %d jogadas | %d futuras)",
             json_path, len(matches), played, upcoming)

    html_path = RAW_HTML_DIR / f"raw_{ts_filename}.html"
    html_path.write_text(raw_html, encoding="utf-8")
    log.info("HTML bruto salvo: %s", html_path)

    if next_data:
        nd_path = RAW_HTML_DIR / f"next_data_{ts_filename}.json"
        nd_path.write_text(
            json.dumps(next_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    return json_path


def _total_capture_count() -> int:
    try:
        return len(list(CAPTURES_DIR.glob("capture_*.json")))
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# 4. Detecção de mudanças (diff com última execução)
# --------------------------------------------------------------------------- #
def load_last_state() -> Optional[dict]:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Erro ao ler last_state.json: %s", e)
        return None


def save_current_state(matches: list) -> None:
    STATE_FILE.write_text(
        json.dumps({"matches": matches, "saved_at": datetime.now(BRT).isoformat()},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def diff_matches(old: list, new: list) -> list:
    old_by_id = {m.get("match_id"): m for m in (old or [])}
    new_by_id = {m.get("match_id"): m for m in (new or [])}

    changes = []

    for mid, m in new_by_id.items():
        if mid not in old_by_id:
            changes.append({"type": "NEW_MATCH", "match_id": mid,
                            "details": _summarize_match(m)})

    for mid, m in old_by_id.items():
        if mid not in new_by_id:
            changes.append({"type": "MATCH_REMOVED", "match_id": mid,
                            "details": _summarize_match(m)})

    for mid, m_new in new_by_id.items():
        m_old = old_by_id.get(mid)
        if m_old is None:
            continue
        diffs = _diff_match(m_old, m_new)
        if diffs:
            changes.append({"type": "MATCH_UPDATED", "match_id": mid,
                            "summary": _summarize_match(m_new), "details": diffs})

    return changes


def _summarize_match(m: dict) -> dict:
    home = m.get("home_team", {}) or {}
    away = m.get("away_team", {}) or {}
    return {
        "home_code": home.get("code"), "home_name": home.get("name"),
        "away_code": away.get("code"), "away_name": away.get("name"),
        "date": m.get("date"), "kick_off_time": m.get("kick_off_time"),
        "score_home": m.get("score_home"), "score_away": m.get("score_away"),
        "status": m.get("status"), "phase": m.get("phase"),
        "group": m.get("group"), "stadium": m.get("stadium"), "city": m.get("city"),
    }


def _diff_match(old: dict, new: dict) -> dict:
    fields = ["score_home", "score_away", "status", "kick_off_time",
              "phase", "group", "stadium", "city", "date"]
    out = {}
    for f in fields:
        o_v = old.get(f)
        n_v = new.get(f)
        if o_v != n_v:
            out[f] = {"old": o_v, "new": n_v}
    for side in ("home_team", "away_team"):
        o_t = old.get(side, {}) or {}
        n_t = new.get(side, {}) or {}
        if isinstance(o_t, dict) and isinstance(n_t, dict):
            if o_t.get("code") != n_t.get("code"):
                out[side] = {"old_code": o_t.get("code"), "new_code": n_t.get("code"),
                             "old_name": o_t.get("name"), "new_name": n_t.get("name")}
    return out


def log_changes(changes: list) -> None:
    changes_log = LOGS_DIR / "changes.log"
    now = datetime.now(BRT).isoformat()
    with changes_log.open("a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 78}\n")
        f.write(f"# Captura em {now}\n")
        f.write(f"# Total de mudanças: {len(changes)}\n")
        f.write(f"{'=' * 78}\n")
        for c in changes:
            f.write(json.dumps(c, ensure_ascii=False, default=str) + "\n")
    if changes:
        log.info("📊 %d mudança(s) registrada(s) em %s",
                 len(changes), changes_log.name)
        for c in changes:
            t = c.get("type")
            if t == "NEW_MATCH":
                s = c.get("details", {})
                log.info("  + NOVA: %s %s vs %s %s",
                         s.get("home_code"), s.get("home_name"),
                         s.get("away_code"), s.get("away_name"))
            elif t == "MATCH_REMOVED":
                s = c.get("details", {})
                log.info("  - REMOVIDA: %s vs %s",
                         s.get("home_name"), s.get("away_name"))
            elif t == "MATCH_UPDATED":
                s = c.get("summary", {})
                d = c.get("details", {})
                log.info("  * ATUALIZADA: %s vs %s | campos: %s",
                         s.get("home_name"), s.get("away_name"),
                         ", ".join(d.keys()))
    else:
        log.info("✓ Nenhuma mudança desde a última execução")


# --------------------------------------------------------------------------- #
# 5. Job principal
# --------------------------------------------------------------------------- #
def run_job() -> None:
    """Executa uma captura completa."""
    log.info("=" * 70)
    log.info("INÍCIO DA CAPTURA #%d", _total_capture_count() + 1)
    log.info("=" * 70)

    start = time.time()
    try:
        html = fetch_page_html()
        matches, next_data = parse_matches(html)
        json_path = save_capture(matches, next_data, html)

        last_state = load_last_state()
        if last_state and last_state.get("matches"):
            changes = diff_matches(last_state["matches"], matches)
            log_changes(changes)
        else:
            log.info("Primeira execução: estado salvo, sem comparação")
            log_changes([])

        save_current_state(matches)

        elapsed = time.time() - start
        log.info("Captura concluída em %.1fs | Arquivo: %s",
                 elapsed, json_path.name)

    except Exception as e:
        log.error("ERRO na captura: %s", e)
        log.error(traceback.format_exc())


# --------------------------------------------------------------------------- #
# 6. Scheduler
# --------------------------------------------------------------------------- #
def run_daemon(interval_hours: int = INTERVAL_HOURS) -> None:
    def handle_sig(signum, frame):
        log.info("Sinal %s recebido. Encerrando scheduler...", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    log.info("🚀 Spider iniciado em modo DAEMON (a cada %d horas)", interval_hours)
    log.info("Diretório base: %s", SPIDER_ROOT)
    log.info("URL alvo: %s", TARGET_URL)

    run_job()

    schedule.every(interval_hours).hours.do(run_job)

    log.info("Próxima execução automática em %d horas. Aguardando...", interval_hours)
    while True:
        schedule.run_pending()
        time.sleep(30)


def run_test_mode() -> None:
    log.info("🧪 MODO TESTE: execução a cada 30 segundos")
    run_job()
    schedule.every(30).seconds.do(run_job)
    while True:
        schedule.run_pending()
        time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spider FIFA World Cup 2026 - captura periódica de jogos",
    )
    parser.add_argument("--once", action="store_true",
                        help="Executa uma única captura e sai (sem scheduler)")
    parser.add_argument("--test", action="store_true",
                        help="Modo teste: executa a cada 30 segundos (debug)")
    parser.add_argument("--interval", type=int, default=INTERVAL_HOURS,
                        help=f"Intervalo em horas (padrão: {INTERVAL_HOURS})")
    args = parser.parse_args()

    if args.once:
        log.info("Modo --once: única execução")
        run_job()
    elif args.test:
        run_test_mode()
    else:
        run_daemon(interval_hours=args.interval)


if __name__ == "__main__":
    main()
