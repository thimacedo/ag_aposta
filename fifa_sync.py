"""
fifa_sync.py
Adaptador entre o spider FIFA e o banco SQLite do Quant-Agent.

Responsabilidades
=================
1. Ler a captura JSON mais recente produzida pelo `fifa_spider.py`.
2. Registrar metadados da captura em `capturas_fifa`.
3. Para cada partida da captura:
   - Fazer UPSERT em `partidas_agenda` (incluindo status, scores, etc.)
   - Inserir linha em `partidas_fifa_log` (histórico versionado)
   - Atualizar `times_fifa_codes` com códigos novos (placeholders W95 etc.)
4. Atualizar `historico_analises.resultado_partida` para partidas
   que foram finalizadas desde a última execução.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fifa_sync")

SPIDER_ROOT = Path(__file__).resolve().parent / "fifa_spider"
CAPTURES_DIR = SPIDER_ROOT / "captures"
RAW_HTML_DIR = SPIDER_ROOT / "raw_html"

BRT = timezone(timedelta(hours=-3))
LIGA_COPA_2026 = "Copa do Mundo 2026"


def encontrar_ultima_captura() -> Optional[Path]:
    if not CAPTURES_DIR.exists():
        return None
    files = sorted(CAPTURES_DIR.glob("capture_*.json"))
    return files[-1] if files else None


def carregar_captura(json_path: Path) -> dict:
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "matches" not in data:
        raise ValueError(f"JSON inválido (sem 'matches'): {json_path}")
    return data


def sincronizar(json_path: Optional[Path] = None) -> dict:
    """Sincroniza uma captura do spider com o banco de dados."""
    start = time.time()
    db.init_db()

    if json_path is None:
        json_path = encontrar_ultima_captura()
    if json_path is None:
        logger.error("Nenhuma captura encontrada em %s", CAPTURES_DIR)
        return {"capture_id": None, "matches_processed": 0,
                "matches_inserted": 0, "matches_updated": 0,
                "log_rows_inserted": 0, "fifa_codes_upserted": 0,
                "historico_atualizado": 0, "elapsed_sec": 0.0}

    logger.info("Sincronizando captura: %s", json_path.name)
    captura = carregar_captura(json_path)

    stats = captura.get("stats", {})
    matches = captura.get("matches", [])

    ts_match = json_path.stem.replace("capture_", "")
    html_path = RAW_HTML_DIR / f"raw_{ts_match}.html"

    changes_detected = _contar_mudanças_ultima_captura()

    with db.get_connection() as conn:
        capture_row = {
            "capture_timestamp": captura.get("capture_timestamp"),
            "source_url": captura.get("source_url"),
            "json_path": str(json_path),
            "html_path": str(html_path) if html_path.exists() else None,
            "matches_found": stats.get("matches_found", len(matches)),
            "matches_played": stats.get("matches_played"),
            "matches_upcoming": stats.get("matches_upcoming"),
            "matches_live": stats.get("matches_live"),
            "html_size_bytes": stats.get("html_size_bytes"),
            "changes_detected": changes_detected,
            "execution_time_sec": None,
            "success": 1,
            "error_message": None,
        }
        capture_id = db.registrar_captura_fifa(conn, capture_row)
        logger.info("Captura registrada com id=%d", capture_id)

        inserted = 0
        updated = 0
        fifa_codes = set()
        for m in matches:
            home_team = m.get("home_team") or {}
            away_team = m.get("away_team") or {}
            home_code = home_team.get("code")
            away_code = away_team.get("code")

            # Resolve time_ids via mapeamento
            time_home_id = _resolver_time_id_por_nome(
                home_team.get("name"), home_code,
            )
            time_away_id = _resolver_time_id_por_nome(
                away_team.get("name"), away_code,
            )

            # Garante que o time existe em times_performance (FK)
            if time_home_id:
                _garantir_time_copa(conn, time_home_id, home_team.get("name"))
            if time_away_id:
                _garantir_time_copa(conn, time_away_id, away_team.get("name"))

            # Registra o código FIFA em times_fifa_codes
            for code, t in ((home_code, home_team), (away_code, away_team)):
                if code and code not in fifa_codes:
                    fifa_codes.add(code)
                    _registrar_fifa_code(conn, code, t)

            # Constrói payload para UPSERT em partidas_agenda
            data_evento = m.get("date") or "2026-06-01"
            if m.get("kick_off_time"):
                data_evento = f"{data_evento} {m['kick_off_time']}:00"

            partida_payload = {
                "spider_match_id": m.get("match_id"),
                "match_index": m.get("match_index"),
                "liga": LIGA_COPA_2026,
                "data_evento": data_evento,
                "time_mandante_id": time_home_id,
                "time_visitante_id": time_away_id,
                "codigo_fifa_home": home_code,
                "codigo_fifa_away": away_code,
                "nome_mandante": home_team.get("name"),
                "nome_visitante": away_team.get("name"),
                "fase": m.get("phase"),
                "grupo": m.get("group"),
                "estadio": m.get("stadium"),
                "cidade": m.get("city"),
                "horario_kickoff": m.get("kick_off_time"),
                "status_fifa": m.get("status"),
                "score_home": _to_int_or_none(m.get("score_home")),
                "score_away": _to_int_or_none(m.get("score_away")),
                "flag_url_home": home_team.get("flag_url"),
                "flag_url_away": away_team.get("flag_url"),
            }

            existing = conn.execute(
                "SELECT 1 FROM partidas_agenda WHERE spider_match_id = ?",
                (m.get("match_id"),),
            ).fetchone()
            db.upsert_partida_fifa(conn, partida_payload)
            if existing:
                updated += 1
            else:
                inserted += 1

            db.inserir_partida_fifa_log(conn, capture_id, m)

        historico_atualizado = db.atualizar_resultados_historico_por_fifa(conn)
        db.marcar_captura_importada(conn, capture_id)

    elapsed = time.time() - start
    result = {
        "capture_id": capture_id,
        "matches_processed": len(matches),
        "matches_inserted": inserted,
        "matches_updated": updated,
        "log_rows_inserted": len(matches),
        "fifa_codes_upserted": len(fifa_codes),
        "historico_atualizado": historico_atualizado,
        "elapsed_sec": round(elapsed, 2),
    }
    logger.info(
        "Sincronização concluída em %.2fs | "
        "%d partidas (%d novas, %d atualizadas) | "
        "%d códigos FIFA | %d análises atualizadas",
        elapsed, len(matches), inserted, updated,
        len(fifa_codes), historico_atualizado,
    )
    return result


def _registrar_fifa_code(conn, code: str, team_dict: dict) -> None:
    is_placeholder = 1 if _is_placeholder_code(code) else 0
    time_id = None
    if not is_placeholder:
        time_id = _resolver_time_id_por_nome(team_dict.get("name"), code)

    db.upsert_fifa_code(conn, {
        "fifa_code": code,
        "time_id": time_id,
        "nome_pt": team_dict.get("name"),
        "nome_en": None,
        "flag_url": team_dict.get("flag_url"),
        "is_placeholder": is_placeholder,
    })


def _is_placeholder_code(code: str) -> bool:
    """Placeholders: W95, RU101, L93, 1A, 2B, etc."""
    if not code:
        return False
    # Códigos reais FIFA têm exatamente 3 letras (BRA, ARG, MEX)
    if len(code) == 3 and code.isalpha() and code.isupper():
        return False
    return True


_FIFA_ID_MAP_BY_NAME: dict[str, int] = {
    "Argentina": 2, "Brasil": 3, "França": 4, "Alemanha": 5,
    "Espanha": 8, "Inglaterra": 9, "Itália": 10, "Portugal": 12,
    "Holanda": 13, "Bélgica": 14, "Croácia": 15, "Uruguai": 16,
    "México": 23, "Estados Unidos": 24, "EUA": 24, "Canadá": 25,
    "Sérvia": 27, "Suíça": 29, "Polônia": 32, "Colômbia": 33,
    "Suécia": 34, "Ucrânia": 35, "Japão": 37, "Coreia do Sul": 38,
    "República da Coreia": 38, "Austrália": 39, "Camarões": 40,
    "Costa Rica": 42, "Marrocos": 45, "Irã": 46, "Nigéria": 47,
    "Gana": 48, "Senegal": 49, "Egito": 55, "Chile": 60, "Peru": 63,
    "Paraguai": 65, "Equador": 67, "Arábia Saudita": 68, "Qatar": 75,
    "Catar": 75, "Nova Zelândia": 107, "Panamá": 114, "Jamaica": 121,
    "Honduras": 137, "Bolívia": 140, "Venezuela": 141, "Índia": 151,
    "Tunísia": 155, "Argélia": 156, "Costa do Marfim": 157,
    "Áustria": 158, "Dinamarca": 159, "Noruega": 161, "Escócia": 162,
    "Gales": 163, "Hungria": 164, "Romênia": 165,
    "República Tcheca": 166, "Tchéquia": 166, "Eslováquia": 167,
    "Grécia": 168, "Albânia": 169, "Islândia": 170, "Irlanda": 171,
    "Bósnia e Herzegovina": 172, "Iraque": 173, "Cuba": 175,
    "Coreia do Norte": 179, "Haiti": 180, "Turquia": 181,
    "Burkina Fasso": 182, "Burkina Faso": 182, "Burundi": 183,
    "Butão": 184, "Benim": 185, "Barbados": 186, "Bangladesh": 187,
    "Bulgária": 188,
}

_FIFA_ID_MAP_BY_CODE: dict[str, int] = {
    "ARG": 2, "BRA": 3, "FRA": 4, "GER": 5,
    "ESP": 8, "ENG": 9, "ITA": 10, "POR": 12,
    "NED": 13, "BEL": 14, "CRO": 15, "URU": 16,
    "MEX": 23, "USA": 24, "CAN": 25,
    "SRB": 27, "SUI": 29, "POL": 32, "COL": 33,
    "SWE": 34, "UKR": 35, "JPN": 37, "KOR": 38,
    "AUS": 39, "CMR": 40, "CRC": 42, "MAR": 45,
    "IRN": 46, "NGA": 47, "GHA": 48, "SEN": 49,
    "EGY": 55, "CHI": 60, "PER": 63, "PAR": 65,
    "ECU": 67, "KSA": 68, "QAT": 75, "NZL": 107,
    "PAN": 114, "JAM": 121, "HON": 137, "BOL": 140,
    "VEN": 141, "IND": 151, "TUN": 155, "ALG": 156,
    "CIV": 157, "AUT": 158, "DEN": 159, "NOR": 161,
    "SCO": 162, "WAL": 163, "HUN": 164, "ROU": 165,
    "CZE": 166, "SVK": 167, "GRE": 168, "ALB": 169,
    "ISL": 170, "IRL": 171, "BIH": 172, "IRQ": 173,
    "CUB": 175, "PRK": 179, "HAI": 180, "TUR": 181,
    "BFA": 182, "BDI": 183, "BTN": 184, "BEN": 185,
    "BRB": 186, "BGD": 187, "BUL": 188,
}


def _resolver_time_id_por_nome(nome: Optional[str],
                                fifa_code: Optional[str]) -> Optional[int]:
    """Tenta resolver o time_id pelo código FIFA primeiro, depois pelo nome."""
    if fifa_code:
        tid = _FIFA_ID_MAP_BY_CODE.get(fifa_code)
        if tid:
            return tid
    if nome:
        return _FIFA_ID_MAP_BY_NAME.get(nome)
    return None


def _garantir_time_copa(conn, time_id: int, nome: str) -> None:
    """Garante que um time existe em times_performance."""
    existing = conn.execute(
        "SELECT 1 FROM times_performance WHERE time_id = ?", (time_id,)
    ).fetchone()
    if existing:
        return

    try:
        import core_math
        ref = core_math.MEDIA_LIGA_COPA_REFERENCIA
    except ImportError:
        ref = 1.25

    conn.execute(
        """INSERT OR IGNORE INTO times_performance
           (time_id, nome, liga,
            xg_marcado_casa, xg_sofrido_casa,
            xg_marcado_fora, xg_sofrido_fora,
            jogos_casa, jogos_fora)
           VALUES (?, ?, 'Copa do Mundo 2026', ?, ?, ?, ?, 0, 0)""",
        (time_id, nome, ref, ref, ref, ref),
    )
    conn.commit()


def _contar_mudanças_ultima_captura() -> int:
    changes_log = SPIDER_ROOT / "logs" / "changes.log"
    if not changes_log.exists():
        return 0
    try:
        content = changes_log.read_text(encoding="utf-8")
        blocks = content.split("=" * 78)
        if len(blocks) < 2:
            return 0
        last_block = None
        for b in reversed(blocks):
            if b.strip() and "# Captura em" in b:
                last_block = b
                break
        if not last_block:
            return 0
        return sum(1 for line in last_block.split("\n")
                   if line.strip().startswith("{"))
    except Exception:
        return 0


def _to_int_or_none(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Sincroniza a última captura do spider FIFA com o banco SQLite"
    )
    parser.add_argument("--json", type=Path, default=None,
                        help="Caminho específico de um JSON de captura (default: última)")
    args = parser.parse_args()
    result = sincronizar(json_path=args.json)
    print("\n=== Resultado da sincronização ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
