"""
db.py
Camada de acesso ao banco de dados local (SQLite).

Responsabilidades:
- Inicializar o arquivo de banco e aplicar o schema (schema.sql).
- Aplicar migrations V2 (ALTER TABLE) para integrar o spider FIFA.
- Fornecer uma conexão configurada (row_factory, foreign_keys).
- Funções utilitárias de leitura/escrita (UPSERTs) para os agentes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "quant_bet.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def init_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    """
    Cria o arquivo de banco (se não existir) e aplica o schema DDL.
    Idempotente.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        with open(schema_path, "r", encoding="utf-8") as f:
            ddl = f.read()
        conn.executescript(ddl)
        _apply_schema_v2(conn)
        conn.commit()


def _apply_schema_v2(conn: sqlite3.Connection) -> None:
    """
    Aplica ALTER TABLE em partidas_agenda para adicionar as colunas do
    spider FIFA. SQLite não suporta 'ADD COLUMN IF NOT EXISTS', então
    usamos try/except para cada uma.
    """
    new_columns = [
        ("codigo_fifa_home",        "TEXT"),
        ("codigo_fifa_away",        "TEXT"),
        ("nome_mandante",           "TEXT"),
        ("nome_visitante",          "TEXT"),
        ("fase",                    "TEXT"),
        ("grupo",                   "TEXT"),
        ("estadio",                 "TEXT"),
        ("cidade",                  "TEXT"),
        ("horario_kickoff",         "TEXT"),
        ("status_fifa",             "TEXT"),
        ("score_home",              "INTEGER"),
        ("score_away",              "INTEGER"),
        ("flag_url_home",           "TEXT"),
        ("flag_url_away",           "TEXT"),
        ("spider_match_id",         "TEXT"),
        ("ultima_atualizacao_fifa", "TIMESTAMP"),
    ]
    for col_name, col_type in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE partidas_agenda ADD COLUMN {col_name} {col_type};"
            )
        except sqlite3.OperationalError:
            pass

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_partidas_agenda_spider_match_id "
            "ON partidas_agenda(spider_match_id);"
        )
    except sqlite3.OperationalError:
        pass


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context manager que devolve uma conexão SQLite configurada."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Funções de Leitura/Escrita (Implementadas)
# ---------------------------------------------------------------------

def upsert_time_performance(conn: sqlite3.Connection, time_data: dict) -> None:
    """Insere ou atualiza métricas de xG de um time em `times_performance`."""
    sql = """
    INSERT OR REPLACE INTO times_performance (
        time_id, nome, liga,
        xg_marcado_casa, xg_sofrido_casa,
        xg_marcado_fora, xg_sofrido_fora,
        jogos_casa, jogos_fora, ultima_atualizacao
    ) VALUES (
        :time_id, :nome, :liga,
        :xg_marcado_casa, :xg_sofrido_casa,
        :xg_marcado_fora, :xg_sofrido_fora,
        :jogos_casa, :jogos_fora, CURRENT_TIMESTAMP
    )
    """
    conn.execute(sql, time_data)
    conn.commit()


def inserir_partida_agenda(conn: sqlite3.Connection, partida_data: dict) -> int:
    """Insere ou substitui uma partida agendada em `partidas_agenda`."""
    sql = """
    INSERT OR REPLACE INTO partidas_agenda (
        partida_id, liga, data_evento, time_mandante_id, time_visitante_id
    ) VALUES (
        :partida_id, :liga, :data_evento, :time_mandante_id, :time_visitante_id
    )
    """
    conn.execute(sql, partida_data)
    conn.commit()
    return int(partida_data["partida_id"])


def inserir_odds(conn: sqlite3.Connection, odds_data: dict) -> None:
    """Insere uma cotação capturada em `odds_mercado`."""
    sql = """
    INSERT INTO odds_mercado (
        partida_id, casa_aposta, odd_mandante, odd_empate, odd_visitante
    ) VALUES (
        :partida_id, :casa_aposta, :odd_mandante, :odd_empate, :odd_visitante
    )
    """
    conn.execute(sql, odds_data)
    conn.commit()


def registrar_analise_historico(conn: sqlite3.Connection, analise_data: dict) -> None:
    """Registra uma recomendação gerada em `historico_analises`."""
    sql = """
    INSERT INTO historico_analises (
        partida_id, prob_mandante_real, prob_empate_real, prob_visitante_real,
        mercado_sugerido, odd_disponivel, ev_calculado, stake_kelly, resultado_partida
    ) VALUES (
        :partida_id, :prob_mandante_real, :prob_empate_real, :prob_visitante_real,
        :mercado_sugerido, :odd_disponivel, :ev_calculado, :stake_kelly,
        COALESCE(:resultado_partida, 'PENDENTE')
    )
    """
    if 'resultado_partida' not in analise_data:
        analise_data['resultado_partida'] = 'PENDENTE'
    conn.execute(sql, analise_data)
    conn.commit()


# ---------------------------------------------------------------------
# Controle Inteligente de Cache (FinOps)
# ---------------------------------------------------------------------

def verificar_partidas_recentes_liga(conn: sqlite3.Connection, liga_id: int) -> bool:
    sql = """
    SELECT count(*) as total FROM partidas_agenda
    WHERE liga = ? AND date(data_evento) = date('now')
    """
    row = conn.execute(sql, (str(liga_id),)).fetchone()
    return row["total"] > 0


def verificar_odds_recentes(conn: sqlite3.Connection) -> bool:
    sql = """
    SELECT count(*) FROM odds_mercado
    WHERE timestamp_captura > datetime('now', '-6 hours')
    """
    row = conn.execute(sql).fetchone()
    count = row[0]
    print(f"DEBUG: Odds count in last 6h: {count}")
    return count > 0


# =====================================================================
# V2 — Funções para integração com o spider FIFA
# =====================================================================

def registrar_captura_fifa(conn: sqlite3.Connection, captura: dict) -> int:
    """Registra metadados de uma execução do spider em `capturas_fifa`."""
    sql = """
    INSERT INTO capturas_fifa (
        capture_timestamp, source_url, json_path, html_path,
        matches_found, matches_played, matches_upcoming, matches_live,
        html_size_bytes, changes_detected, execution_time_sec,
        success, error_message
    ) VALUES (
        :capture_timestamp, :source_url, :json_path, :html_path,
        :matches_found, :matches_played, :matches_upcoming, :matches_live,
        :html_size_bytes, :changes_detected, :execution_time_sec,
        :success, :error_message
    )
    """
    cur = conn.execute(sql, captura)
    conn.commit()
    return int(cur.lastrowid)


def marcar_captura_importada(conn: sqlite3.Connection, capture_id: int) -> None:
    conn.execute(
        """UPDATE capturas_fifa
              SET imported_to_db = 1, imported_at = CURRENT_TIMESTAMP
            WHERE id = ?""",
        (capture_id,),
    )
    conn.commit()


def upsert_partida_fifa(conn: sqlite3.Connection, partida: dict) -> int:
    """
    UPSERT de uma partida vinda do spider em `partidas_agenda`.
    Chave de conflito: spider_match_id.
    """
    row = conn.execute(
        "SELECT partida_id FROM partidas_agenda WHERE spider_match_id = ?",
        (partida["spider_match_id"],),
    ).fetchone()

    if row:
        sql = """
        UPDATE partidas_agenda SET
            codigo_fifa_home    = :codigo_fifa_home,
            codigo_fifa_away    = :codigo_fifa_away,
            nome_mandante       = :nome_mandante,
            nome_visitante      = :nome_visitante,
            fase                = :fase,
            grupo               = :grupo,
            estadio             = :estadio,
            cidade              = :cidade,
            horario_kickoff     = :horario_kickoff,
            status_fifa         = :status_fifa,
            score_home          = :score_home,
            score_away          = :score_away,
            flag_url_home       = :flag_url_home,
            flag_url_away       = :flag_url_away,
            data_evento         = :data_evento,
            ultima_atualizacao_fifa = CURRENT_TIMESTAMP
          WHERE spider_match_id = :spider_match_id
        """
        conn.execute(sql, partida)
        partida_id = int(row["partida_id"])
    else:
        partida_id = partida.get("partida_id") or (
            100000 + int(partida.get("match_index", 0))
        )
        sql = """
        INSERT INTO partidas_agenda (
            partida_id, liga, data_evento,
            time_mandante_id, time_visitante_id,
            codigo_fifa_home, codigo_fifa_away,
            nome_mandante, nome_visitante,
            fase, grupo, estadio, cidade,
            horario_kickoff, status_fifa,
            score_home, score_away,
            flag_url_home, flag_url_away,
            spider_match_id, ultima_atualizacao_fifa
        ) VALUES (
            :partida_id, :liga, :data_evento,
            :time_mandante_id, :time_visitante_id,
            :codigo_fifa_home, :codigo_fifa_away,
            :nome_mandante, :nome_visitante,
            :fase, :grupo, :estadio, :cidade,
            :horario_kickoff, :status_fifa,
            :score_home, :score_away,
            :flag_url_home, :flag_url_away,
            :spider_match_id, CURRENT_TIMESTAMP
        )
        """
        partida["partida_id"] = partida_id
        conn.execute(sql, partida)

    conn.commit()
    return partida_id


def inserir_partida_fifa_log(conn: sqlite3.Connection,
                              capture_id: int,
                              match: dict) -> None:
    """Insere uma linha no histórico versionado `partidas_fifa_log`."""
    sql = """
    INSERT INTO partidas_fifa_log (
        capture_id, spider_match_id, match_index,
        home_code, home_name, away_code, away_name,
        score_home, score_away, status, kick_off_time,
        phase, group_name, stadium, city, match_date
    ) VALUES (
        :capture_id, :spider_match_id, :match_index,
        :home_code, :home_name, :away_code, :away_name,
        :score_home, :score_away, :status, :kick_off_time,
        :phase, :group_name, :stadium, :city, :match_date
    )
    """
    params = {
        "capture_id": capture_id,
        "spider_match_id": match.get("match_id"),
        "match_index": match.get("match_index"),
        "home_code": (match.get("home_team") or {}).get("code"),
        "home_name": (match.get("home_team") or {}).get("name"),
        "away_code": (match.get("away_team") or {}).get("code"),
        "away_name": (match.get("away_team") or {}).get("name"),
        "score_home": _to_int_or_none(match.get("score_home")),
        "score_away": _to_int_or_none(match.get("score_away")),
        "status": match.get("status"),
        "kick_off_time": match.get("kick_off_time"),
        "phase": match.get("phase"),
        "group_name": match.get("group"),
        "stadium": match.get("stadium"),
        "city": match.get("city"),
        "match_date": match.get("date"),
    }
    conn.execute(sql, params)
    conn.commit()


def upsert_fifa_code(conn: sqlite3.Connection, code_data: dict) -> None:
    sql = """
    INSERT INTO times_fifa_codes (
        fifa_code, time_id, nome_pt, nome_en, flag_url, is_placeholder
    ) VALUES (
        :fifa_code, :time_id, :nome_pt, :nome_en, :flag_url, :is_placeholder
    )
    ON CONFLICT(fifa_code) DO UPDATE SET
        time_id       = COALESCE(excluded.time_id, times_fifa_codes.time_id),
        nome_pt       = COALESCE(excluded.nome_pt, times_fifa_codes.nome_pt),
        nome_en       = COALESCE(excluded.nome_en, times_fifa_codes.nome_en),
        flag_url      = COALESCE(excluded.flag_url, times_fifa_codes.flag_url),
        is_placeholder = COALESCE(excluded.is_placeholder, times_fifa_codes.is_placeholder)
    """
    conn.execute(sql, code_data)
    conn.commit()


def resolver_time_id_por_fifa_code(conn: sqlite3.Connection,
                                    fifa_code: str) -> Optional[int]:
    row = conn.execute(
        "SELECT time_id FROM times_fifa_codes WHERE fifa_code = ? AND time_id IS NOT NULL",
        (fifa_code,),
    ).fetchone()
    return int(row["time_id"]) if row else None


def atualizar_resultados_historico_por_fifa(conn: sqlite3.Connection) -> int:
    """
    Atualiza `historico_analises.resultado_partida` para partidas que
    foram finalizadas (status_fifa='FIM') mas continuam marcadas como
    'PENDENTE' no histórico.
    """
    rows = conn.execute(
        """
        SELECT h.id, h.mercado_sugerido, h.odd_disponivel, h.stake_kelly,
               p.score_home, p.score_away
          FROM historico_analises h
          JOIN partidas_agenda p ON h.partida_id = p.partida_id
         WHERE h.resultado_partida = 'PENDENTE'
           AND p.status_fifa = 'FIM'
           AND p.score_home IS NOT NULL
           AND p.score_away IS NOT NULL
        """
    ).fetchall()

    atualizados = 0
    for r in rows:
        sh = r["score_home"]
        sa = r["score_away"]
        if sh > sa:
            resultado = "MANDANTE"
        elif sh == sa:
            resultado = "EMPATE"
        else:
            resultado = "VISITANTE"

        acertou = (r["mercado_sugerido"] == resultado)
        stake = r["stake_kelly"] or 0.0
        odd = r["odd_disponivel"] or 0.0
        lucro = round(
            (stake * (odd - 1.0)) if acertou else (-stake),
            5,
        )

        conn.execute(
            """UPDATE historico_analises
                  SET resultado_partida = ?, lucro_prejuizo = ?
                WHERE id = ?""",
            (resultado, lucro, r["id"]),
        )
        atualizados += 1

    conn.commit()
    return atualizados


def listar_capturas_recentes(conn: sqlite3.Connection,
                              limite: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id, capture_timestamp, matches_found, matches_played,
                  matches_upcoming, changes_detected, success,
                  imported_to_db, imported_at
             FROM capturas_fifa
            ORDER BY id DESC LIMIT ?""",
        (limite,),
    ).fetchall()
    return [dict(r) for r in rows]


def _to_int_or_none(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None
