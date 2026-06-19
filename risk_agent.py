"""
risk_agent.py
Agente 3 — Gestor de Risco (Risk & Kelly Agent).

Copa do Mundo 2026 — Campo Neutro
==================================
CORREÇÃO PRINCIPAL: calcular_lambda_esperado() é chamado com
is_mandante=False para AMBOS os times, pois a Copa é disputada em
campos neutros (EUA/Canadá/México). Não há vantagem de "casa".

O parâmetro tournament="copa" garante que core_math também não aplique
nenhum home bonus mesmo que is_mandante fosse passado como True.

CORREÇÕES APLICADAS (vs versão anterior):
  - Definida constante JANELA_ANALISE_HORAS (substitui janela_horas indefinida)
  - diagnostico_partida() agora separa row_mandante e row_visitante
  - _val() usa .get() em dict e .keys() para checar existência em sqlite3.Row
  - Verificação por mercado ao evitar duplicatas (permite múltiplos EV+)
"""

from __future__ import annotations

import logging

import core_math
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MEDIA_LIGA_COPA = core_math.MEDIA_LIGA_COPA_REFERENCIA   # 1.25 gols/jogo
EV_MINIMO_PADRAO = 0.02   # 2% de EV

# CORREÇÃO: janela definida como constante
JANELA_ANALISE_HORAS = 48  # analisa jogos das próximas 48h


def avaliar_oportunidade(
    prob_modelo: dict,
    odds_mercado: dict,
    fracao_kelly: float = core_math.KELLY_FRACAO_PADRAO,
) -> list[dict]:
    """Compara probabilidades do modelo Poisson com o mercado após devigging."""
    odd_1 = odds_mercado.get("MANDANTE", 0.0)
    odd_x = odds_mercado.get("EMPATE", 0.0)
    odd_2 = odds_mercado.get("VISITANTE", 0.0)

    if odd_1 <= 1.0 or odd_x <= 1.0 or odd_2 <= 1.0:
        logger.debug("Odds inválidas ou incompletas — pulando partida.")
        return []

    overround = core_math.calcular_overround(odd_1, odd_x, odd_2)
    probs_mercado = core_math.remover_margem(odds_mercado, overround)

    oportunidades = []
    for mercado in ("MANDANTE", "EMPATE", "VISITANTE"):
        p_modelo = prob_modelo.get(mercado, 0.0)
        odd = odds_mercado.get(mercado, 0.0)

        stake, ev = core_math.calcular_kelly_fracionado(p_modelo, odd, fracao=fracao_kelly)

        if ev > 0.0:
            oportunidades.append({
                "mercado": mercado,
                "prob_modelo": round(p_modelo, 4),
                "prob_mercado_sem_margem": round(probs_mercado.get(mercado, 0.0), 4),
                "odd": round(odd, 3),
                "ev": round(ev, 5),
                "stake_sugerido": round(stake, 5),
                "overround": round(overround, 4),
            })

    return oportunidades


def filtrar_recomendacoes(
    oportunidades: list[dict],
    ev_minimo: float = EV_MINIMO_PADRAO,
) -> list[dict]:
    """Filtra oportunidades com EV acima do limiar mínimo."""
    return [op for op in oportunidades if op["ev"] >= ev_minimo]


def calcular_lambdas_copa(
    row_mandante,
    row_visitante,
    media_liga: float = MEDIA_LIGA_COPA,
) -> tuple[float, float]:
    """Calcula os lambdas esperados para ambos os times em campo neutro."""

    def _val(row, col, fallback=MEDIA_LIGA_COPA):
        # Suporta tanto dict quanto sqlite3.Row
        if isinstance(row, dict):
            v = row.get(col)
        else:
            # sqlite3.Row: checa se a chave existe
            keys = row.keys() if hasattr(row, "keys") else []
            v = row[col] if col in keys else None
        try:
            fv = float(v) if v is not None else None
            return fv if fv and fv > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    xg_m_a = _val(row_mandante, "xg_marcado_casa")
    xg_s_a = _val(row_mandante, "xg_sofrido_casa")
    xg_m_b = _val(row_visitante, "xg_marcado_fora")
    xg_s_b = _val(row_visitante, "xg_sofrido_fora")

    atk_a = core_math.calcular_forca_ataque(xg_m_a, media_liga)
    def_b = core_math.calcular_forca_defesa(xg_s_b, media_liga)

    atk_b = core_math.calcular_forca_ataque(xg_m_b, media_liga)
    def_a = core_math.calcular_forca_defesa(xg_s_a, media_liga)

    lambda_a = core_math.calcular_lambda_esperado(
        atk_a, def_b, media_liga,
        is_mandante=False,
        tournament="copa",
    )
    lambda_b = core_math.calcular_lambda_esperado(
        atk_b, def_a, media_liga,
        is_mandante=False,
        tournament="copa",
    )

    return lambda_a, lambda_b


def processar_partidas_pendentes(
    ev_minimo: float = EV_MINIMO_PADRAO,
    fracao_kelly: float = core_math.KELLY_FRACAO_PADRAO,
) -> list[dict]:
    """Orquestra o fluxo completo do Agente de Risco."""
    recomendacoes_geradas = []

    with db.get_connection() as conn:
        # Média dinâmica da liga
        cur_media = conn.execute("""
            SELECT AVG((xg_marcado_casa + xg_marcado_fora) / 2.0) as media_geral
            FROM times_performance
            WHERE jogos_casa + jogos_fora >= 1
        """)
        row_media = cur_media.fetchone()
        media_liga = (
            float(row_media["media_geral"])
            if row_media and row_media["media_geral"]
            else MEDIA_LIGA_COPA
        )
        logger.info(f"Média dinâmica da Copa: {media_liga:.3f} gols/jogo")

        # CORREÇÃO: usar JANELA_ANALISE_HORAS definida
        sql = """
        SELECT
            p.partida_id,
            p.data_evento,
            tm.nome           AS nome_mandante,
            tv.nome           AS nome_visitante,
            tm.time_id        AS id_mandante,
            tv.time_id        AS id_visitante,
            tm.xg_marcado_casa AS tm_xg_marcado_casa,
            tm.xg_sofrido_casa AS tm_xg_sofrido_casa,
            tv.xg_marcado_fora AS tv_xg_marcado_fora,
            tv.xg_sofrido_fora AS tv_xg_sofrido_fora,
            tm.jogos_casa + tm.jogos_fora AS jogos_mandante,
            tv.jogos_casa + tv.jogos_fora AS jogos_visitante,
            MAX(o.odd_mandante)  AS odd_mandante,
            MAX(o.odd_empate)    AS odd_empate,
            MAX(o.odd_visitante) AS odd_visitante
        FROM partidas_agenda p
        JOIN times_performance tm ON p.time_mandante_id = tm.time_id
        JOIN times_performance tv ON p.time_visitante_id = tv.time_id
        JOIN odds_mercado o ON p.partida_id = o.partida_id
        WHERE p.data_evento >= datetime('now', :janela)
        GROUP BY p.partida_id
        ORDER BY p.data_evento ASC
        """

        rows = conn.execute(sql, {"janela": f"-{JANELA_ANALISE_HORAS} hours"}).fetchall()
        logger.info(f"Partidas com odds nas próximas {JANELA_ANALISE_HORAS}h: {len(rows)}")

        for row in rows:
            try:
                # CORREÇÃO: dicts separados para mandante e visitante
                row_mandante = {
                    "xg_marcado_casa": row["tm_xg_marcado_casa"],
                    "xg_sofrido_casa": row["tm_xg_sofrido_casa"],
                }
                row_visitante = {
                    "xg_marcado_fora": row["tv_xg_marcado_fora"],
                    "xg_sofrido_fora": row["tv_xg_sofrido_fora"],
                }

                lambda_a, lambda_b = calcular_lambdas_copa(
                    row_mandante, row_visitante, media_liga
                )

                # Suavização para times com poucos jogos
                jogos_m = row["jogos_mandante"] or 0
                jogos_v = row["jogos_visitante"] or 0
                min_jogos = core_math.MIN_GAMES_FOR_RELIABLE_STATS

                if jogos_m < min_jogos or jogos_v < min_jogos:
                    peso = min(jogos_m, jogos_v) / min_jogos if min_jogos > 0 else 0
                    lambda_a = peso * lambda_a + (1 - peso) * media_liga
                    lambda_b = peso * lambda_b + (1 - peso) * media_liga
                    logger.debug(
                        f"Suavização ({row['nome_mandante']} vs {row['nome_visitante']}): "
                        f"peso={peso:.2f}"
                    )

                prob_modelo = core_math.calcular_probabilidades_partida(lambda_a, lambda_b)

                odds_mercado = {
                    "MANDANTE":  float(row["odd_mandante"] or 0),
                    "EMPATE":    float(row["odd_empate"] or 0),
                    "VISITANTE": float(row["odd_visitante"] or 0),
                }

                oportunidades = avaliar_oportunidade(prob_modelo, odds_mercado, fracao_kelly)
                oportunidades_filtradas = filtrar_recomendacoes(oportunidades, ev_minimo)

                # CORREÇÃO: verifica por mercado para permitir múltiplos EV+
                for op in oportunidades_filtradas:
                    existe = conn.execute("""
                        SELECT 1 FROM historico_analises
                        WHERE partida_id = ? AND mercado_sugerido = ? AND resultado_partida = 'PENDENTE'
                    """, (row["partida_id"], op["mercado"])).fetchone()

                    if existe:
                        logger.debug(f"Análise já existe: {row['partida_id']} - {op['mercado']}")
                        continue

                    analise = {
                        "partida_id": row["partida_id"],
                        "prob_mandante_real": prob_modelo["MANDANTE"],
                        "prob_empate_real":   prob_modelo["EMPATE"],
                        "prob_visitante_real": prob_modelo["VISITANTE"],
                        "mercado_sugerido": op["mercado"],
                        "odd_disponivel":   op["odd"],
                        "ev_calculado":     op["ev"],
                        "stake_kelly":      op["stake_sugerido"],
                        "resultado_partida": "PENDENTE",
                    }
                    db.registrar_analise_historico(conn, analise)
                    recomendacoes_geradas.append({
                        **analise,
                        "mandante": row["nome_mandante"],
                        "visitante": row["nome_visitante"],
                        "lambda_a": round(lambda_a, 3),
                        "lambda_b": round(lambda_b, 3),
                        "prob_mercado_sem_margem": op["prob_mercado_sem_margem"],
                        "overround": op["overround"],
                    })

                if not oportunidades_filtradas:
                    logger.debug(
                        f"Sem EV+ (≥{ev_minimo:.0%}): {row['nome_mandante']} vs {row['nome_visitante']}"
                    )

            except Exception as e:
                logger.error(
                    f"Erro processando {row.get('nome_mandante','?')} vs "
                    f"{row.get('nome_visitante','?')}: {e}",
                    exc_info=True
                )

    logger.info(f"{len(recomendacoes_geradas)} recomendação(ões) EV+ gerada(s).")
    return recomendacoes_geradas


def diagnostico_partida(partida_id: int) -> dict:
    """Retorna diagnóstico completo de uma partida."""
    with db.get_connection() as conn:
        row = conn.execute("""
            SELECT p.partida_id, p.data_evento,
                   tm.nome AS nome_mandante, tv.nome AS nome_visitante,
                   tm.xg_marcado_casa, tm.xg_sofrido_casa,
                   tv.xg_marcado_fora, tv.xg_sofrido_fora,
                   tm.jogos_casa + tm.jogos_fora AS jogos_m,
                   tv.jogos_casa + tv.jogos_fora AS jogos_v,
                   o.odd_mandante, o.odd_empate, o.odd_visitante,
                   o.casa_aposta
            FROM partidas_agenda p
            JOIN times_performance tm ON p.time_mandante_id = tm.time_id
            JOIN times_performance tv ON p.time_visitante_id = tv.time_id
            LEFT JOIN odds_mercado o ON p.partida_id = o.partida_id
            WHERE p.partida_id = ?
            LIMIT 1
        """, (partida_id,)).fetchone()

        if not row:
            return {"erro": f"Partida {partida_id} não encontrada."}

        # CORREÇÃO: dicts separados para mandante e visitante
        row_mandante = {
            "xg_marcado_casa": row["xg_marcado_casa"],
            "xg_sofrido_casa": row["xg_sofrido_casa"],
        }
        row_visitante = {
            "xg_marcado_fora": row["xg_marcado_fora"],
            "xg_sofrido_fora": row["xg_sofrido_fora"],
        }
        lambda_a, lambda_b = calcular_lambdas_copa(
            row_mandante, row_visitante, MEDIA_LIGA_COPA
        )
        probs = core_math.calcular_probabilidades_partida(lambda_a, lambda_b)
        mc = core_math.simular_distribuicao_gols(lambda_a, lambda_b)

        odds = {
            "MANDANTE":  float(row["odd_mandante"] or 0),
            "EMPATE":    float(row["odd_empate"] or 0),
            "VISITANTE": float(row["odd_visitante"] or 0),
        }
        oportunidades = (
            avaliar_oportunidade(probs, odds)
            if all(v > 0 for v in odds.values())
            else []
        )

        return {
            "partida_id": partida_id,
            "mandante": row["nome_mandante"],
            "visitante": row["nome_visitante"],
            "data": row["data_evento"],
            "lambda_mandante": round(lambda_a, 3),
            "lambda_visitante": round(lambda_b, 3),
            "probabilidades_poisson": {k: round(v, 4) for k, v in probs.items()},
            "probabilidades_monte_carlo": {
                k: mc[k] for k in ("MANDANTE", "EMPATE", "VISITANTE")
            },
            "odds_mercado": odds,
            "casa_aposta": row["casa_aposta"],
            "oportunidades_ev": oportunidades,
            "jogos_mandante": row["jogos_m"],
            "jogos_visitante": row["jogos_v"],
            "campo_neutro": True,
        }


if __name__ == "__main__":
    db.init_db()
    print("=== Agente de Risco — Copa do Mundo 2026 ===")
    print(f"Campo neutro: HOME_ADVANTAGE = {core_math.HOME_ADVANTAGE_COPA}")
    print(f"EV mínimo: {EV_MINIMO_PADRAO:.0%} | Kelly fração: {core_math.KELLY_FRACAO_PADRAO:.0%}\n")

    recomendacoes = processar_partidas_pendentes()

    if recomendacoes:
        print(f"\n{len(recomendacoes)} oportunidade(s) EV+ encontrada(s):\n")
        for r in sorted(recomendacoes, key=lambda x: x["ev_calculado"], reverse=True):
            print(
                f"  {r['mandante']} vs {r['visitante']} | "
                f"{r['mercado_sugerido']} @ {r['odd_disponivel']:.2f} | "
                f"EV={r['ev_calculado']*100:.2f}% | "
                f"Stake={r['stake_kelly']*100:.2f}% | "
                f"λ=({r['lambda_a']:.2f}, {r['lambda_b']:.2f})"
            )
    else:
        print("Sem oportunidades EV+ no momento. Verifique se há odds no banco.")
        print("Execute: python worker_ingest.py")
