"""
copa2026_scraper.py
Importador de dados — Copa do Mundo 2026 via CSV (backfill histórico).

Responsabilidades
=================
1. Carregar no banco jogos da Copa 2026 a partir de data/copa2026_resultados.csv.
2. Atualizar `historico_analises.resultado_partida` para jogos concluídos.
3. Calcular xG médio real de cada time e persistir.

Observação: Para going-forward, prefira o fluxo spider+sync:
    python worker_ingest.py --once

Este módulo é útil apenas para popular o banco com xG real de jogos
passados, alimentando o modelo Poisson com estatísticas mais confiáveis.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import db
import core_math

# Mapeamento FIFA ID (mantido consistente com fifa_sync.py)
FIFA_ID_MAP: dict[str, int] = {
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
}


def importar_dados_copa() -> dict:
    """Importa dados da Copa 2026 do CSV."""
    db.init_db()

    jogos = []
    csv_path = Path("data/copa2026_resultados.csv")
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                jogos.append({
                    "partida_id": int(row["partida_id"]),
                    "mandante": row["mandante"],
                    "visitante": row["visitante"],
                    "gols_m": float(row["gols_m"]) if row["gols_m"] else None,
                    "gols_v": float(row["gols_v"]) if row["gols_v"] else None,
                    "xg_m": float(row["xg_m"]) if row["xg_m"] else None,
                    "xg_v": float(row["xg_v"]) if row["xg_v"] else None,
                    "resultado": row["resultado"],
                })
    except FileNotFoundError:
        logger.error(f"Arquivo {csv_path} não encontrado.")
        return {"times": 0, "partidas": 0, "resultados_atualizados": 0}

    stats: dict[str, dict] = defaultdict(lambda: {
        "xg_marcado": [], "xg_sofrido": [],
        "gols_marcados": [], "gols_sofridos": [],
        "jogos": 0,
    })
    partidas_por_id = {j["partida_id"]: j for j in jogos}

    for p in jogos:
        if p["gols_m"] is not None and p["xg_m"] is not None:
            stats[p["mandante"]]["xg_marcado"].append(p["xg_m"])
            stats[p["mandante"]]["xg_sofrido"].append(p["xg_v"])
            stats[p["mandante"]]["gols_marcados"].append(p["gols_m"])
            stats[p["mandante"]]["gols_sofridos"].append(p["gols_v"])
            stats[p["mandante"]]["jogos"] += 1

            stats[p["visitante"]]["xg_marcado"].append(p["xg_v"])
            stats[p["visitante"]]["xg_sofrido"].append(p["xg_m"])
            stats[p["visitante"]]["gols_marcados"].append(p["gols_v"])
            stats[p["visitante"]]["gols_sofridos"].append(p["gols_m"])
            stats[p["visitante"]]["jogos"] += 1

    times_salvos = 0
    with db.get_connection() as conn:
        for nome, s in stats.items():
            time_id = FIFA_ID_MAP.get(nome, 0)
            if not time_id:
                continue

            n = s["jogos"]
            if n == 0:
                continue
            xg_m, xg_s = core_math.calcular_xg_ajustado_por_resultado(
                sum(s["xg_marcado"])/n, sum(s["xg_sofrido"])/n,
                sum(s["gols_marcados"])/n, sum(s["gols_sofridos"])/n
            )

            db.upsert_time_performance(conn, {
                "time_id": time_id, "nome": nome, "liga": "Copa do Mundo 2026",
                "xg_marcado_casa": xg_m, "xg_sofrido_casa": xg_s,
                "xg_marcado_fora": xg_m, "xg_sofrido_fora": xg_s,
                "jogos_casa": n, "jogos_fora": n
            })
            times_salvos += 1

        partidas_salvas = 0
        for pid, p in partidas_por_id.items():
            m_id = FIFA_ID_MAP.get(p["mandante"], 0)
            v_id = FIFA_ID_MAP.get(p["visitante"], 0)
            if not m_id or not v_id:
                continue
            _garantir_time_copa(conn, m_id, p["mandante"])
            _garantir_time_copa(conn, v_id, p["visitante"])
            db.inserir_partida_agenda(conn, {
                "partida_id": pid, "liga": "Copa do Mundo 2026",
                "data_evento": "2026-06-01",
                "time_mandante_id": m_id, "time_visitante_id": v_id,
            })
            partidas_salvas += 1

        atualizados = _atualizar_resultados_historico_csv(conn, jogos)

    return {"times": times_salvos, "partidas": partidas_salvas,
            "resultados_atualizados": atualizados}


def _garantir_time_copa(conn, time_id: int, nome: str) -> None:
    if not conn.execute(
        "SELECT 1 FROM times_performance WHERE time_id = ?", (time_id,)
    ).fetchone():
        ref = core_math.MEDIA_LIGA_COPA_REFERENCIA
        conn.execute(
            "INSERT OR IGNORE INTO times_performance "
            "(time_id, nome, liga, xg_marcado_casa, xg_sofrido_casa, "
            " xg_marcado_fora, xg_sofrido_fora, jogos_casa, jogos_fora) "
            "VALUES (?, ?, 'Copa do Mundo 2026', ?, ?, ?, ?, 0, 0)",
            (time_id, nome, ref, ref, ref, ref)
        )
        conn.commit()


def _atualizar_resultados_historico_csv(conn, jogos) -> int:
    atualizados = 0
    for p in jogos:
        if p["resultado"] == "PENDENTE":
            continue
        rows = conn.execute(
            "SELECT id, mercado_sugerido, odd_disponivel, stake_kelly "
            "FROM historico_analises WHERE partida_id = ? AND resultado_partida = 'PENDENTE'",
            (p["partida_id"],)
        ).fetchall()
        for row in rows:
            acertou = (row["mercado_sugerido"] == p["resultado"])
            lucro = round(
                (row["stake_kelly"] * (row["odd_disponivel"] - 1.0)) if acertou
                else (-row["stake_kelly"]), 5
            )
            conn.execute(
                "UPDATE historico_analises SET resultado_partida = ?, lucro_prejuizo = ? WHERE id = ?",
                (p["resultado"], lucro, row["id"])
            )
            atualizados += 1
    conn.commit()
    return atualizados


if __name__ == "__main__":
    res = importar_dados_copa()
    print(f"Importado: {res}")
