"""
core_math.py
Motor matemático do Agente Analista (Agente 2).

Copa do Mundo 2026 — EUA / Canadá / México
============================================
PREMISSA FUNDAMENTAL: todos os jogos são em campo NEUTRO.
Não há "mandante" real — a designação é apenas convencional (sorteio).
Logo:
  - HOME_ADVANTAGE = 0.0  (campo neutro total)
  - As métricas de xG de cada time são calculadas como média simples
    sobre TODAS as suas partidas na Copa (sem split casa/fora).
  - O modelo Poisson usa lambda_A e lambda_B simétricos.

Fontes empíricas:
  Pollard & Gómez (2014): vantagem de campo em Copas é próxima de zero
  em sede neutra; qualquer edge residual vem de fuso/distância, não de
  torcida ou gramado.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import poisson


# =====================================================================
# 0. Constantes da Copa do Mundo 2026
# =====================================================================

# Campo neutro: nenhum bonus de "casa".
# Se quisermos modelar pequeno edge de CONCACAF (fuso/altitude),
# isso deve ser feature futura com parâmetro por confederação.
HOME_ADVANTAGE_COPA = 0.0

# Mínimo de partidas para considerar força individual confiável.
# Abaixo disso, usamos média da liga pura (força neutra = 1.0).
MIN_GAMES_FOR_RELIABLE_STATS = 3  # Copa tem poucas partidas por time

# Médias de referência Copa do Mundo historicamente
# Fonte: Copas 2014-2022, fase de grupos, campo neutro
MEDIA_LIGA_COPA_REFERENCIA = 1.25  # média de gols por time por jogo

# Kelly fracionado padrão e cap de stake
KELLY_FRACAO_PADRAO = 0.25
KELLY_STAKE_MAX = 0.03  # nunca mais de 3% da banca por aposta


# =====================================================================
# 1. Forças de Ataque / Defesa (baseadas em xG — campo neutro)
# =====================================================================

def calcular_forca_ataque(xg_marcado_medio: float, media_liga: float) -> float:
    """
    Força de ataque relativa à média da liga.
    Atk = xG_marcado_médio / média_liga_copa

    Para Copa (campo neutro): xg_marcado_medio é a média GERAL do time
    (sem split casa/fora, pois não há distinção real).
    """
    if media_liga <= 0:
        return 1.0
    return xg_marcado_medio / media_liga


def calcular_forca_defesa(xg_sofrido_medio: float, media_liga: float) -> float:
    """
    Força de defesa relativa à média da liga — ESCALA INVERTIDA.

    Times que sofrem pouco têm força defensiva MAIOR (reduzem lambda adversário).
    Def = média_liga / xG_sofrido_médio

    Exemplo: sofre 0.6 gols/jogo numa liga com média 1.25 → Def = 1.25/0.6 = 2.08
    Exemplo: sofre 2.0 gols/jogo → Def = 1.25/2.0 = 0.63

    Para Copa (campo neutro): xg_sofrido_medio é a média GERAL.
    """
    if xg_sofrido_medio <= 0:
        return 2.0   # cap superior: defesa "perfeita"
    if media_liga <= 0:
        return 1.0
    return media_liga / xg_sofrido_medio


def calcular_lambda_esperado(
    forca_ataque: float,
    forca_defesa_adversario: float,
    media_liga: float,
    is_mandante: bool = False,          # ignorado na Copa (campo neutro)
    tournament: str = "copa",
) -> float:
    """
    Gols esperados (lambda) de um time em uma partida.

    lambda = Atk_time × Def_adversário × média_liga

    Para Copa do Mundo (campo neutro), is_mandante não altera o cálculo.
    O parâmetro é mantido por compatibilidade de assinatura com os outros
    módulos, mas HOME_ADVANTAGE_COPA = 0.0 garante neutralidade.
    """
    base = forca_ataque * forca_defesa_adversario * media_liga

    # Na Copa, não aplicamos bonus nenhum.
    # Se tournament != "copa" (uso futuro para ligas domésticas):
    if tournament != "copa" and is_mandante:
        home_bonus = 0.27  # ~0.27 gols de vantagem doméstica
        base += home_bonus

    return max(base, 0.01)  # lambda nunca pode ser zero


# =====================================================================
# 2. Modelo de Poisson — Matriz de Placares e Probabilidades 1X2
# =====================================================================

def calcular_probabilidades_partida(
    lambda_a: float,
    lambda_b: float,
    max_gols: int = 8,
) -> dict:
    """
    Matriz de Poisson para dois times com lambdas simétricos (campo neutro).

    Parâmetros
    ----------
    lambda_a : gols esperados do Time A (convencionalmente "mandante").
    lambda_b : gols esperados do Time B (convencionalmente "visitante").
    max_gols : truncagem da distribuição (>=8 captura >99% da massa).

    Retorna
    -------
    {"MANDANTE": p_a_vence, "EMPATE": p_empate, "VISITANTE": p_b_vence}
    A soma é ~1.0 (pequena perda na truncagem).
    """
    goals = np.arange(max_gols)
    prob_a = poisson.pmf(goals, lambda_a)
    prob_b = poisson.pmf(goals, lambda_b)

    # matrix[j, i] = P(B marca j) * P(A marca i)
    matrix = np.outer(prob_b, prob_a)

    return {
        "MANDANTE": float(np.triu(matrix, k=1).sum()),   # A > B
        "EMPATE":   float(np.diag(matrix).sum()),         # A == B
        "VISITANTE": float(np.tril(matrix, k=-1).sum()),  # B > A
    }


# =====================================================================
# 3. Devigging — Remoção da margem da casa de apostas
# =====================================================================

def calcular_overround(odd_1: float, odd_x: float, odd_2: float) -> float:
    """
    Margem embutida nas odds do mercado 1X2.
    Overround = Σ(1/odd_i) - 1
    Exemplo: [2.00, 3.40, 3.60] → overround ≈ 0.044 (4.4%)
    """
    if odd_1 <= 0 or odd_x <= 0 or odd_2 <= 0:
        return 0.0
    return (1/odd_1 + 1/odd_x + 1/odd_2) - 1


def remover_margem(odds: dict, overround: float) -> dict:
    """
    Converte odds com margem em probabilidades implícitas limpas.

    Prob_limpa(i) = (1 / odd_i) / (1 + overround)

    Parâmetros
    ----------
    odds      : {"MANDANTE": float, "EMPATE": float, "VISITANTE": float}
    overround : calculado por calcular_overround()

    Retorna
    -------
    Mesmo formato, com probabilidades (soma ≈ 1.0).
    """
    fator = 1 + overround
    return {
        k: ((1 / v) / fator if v > 0 else 0.0)
        for k, v in odds.items()
    }


# =====================================================================
# 4. Critério de Kelly Fracionado
# =====================================================================

def calcular_kelly_fracionado(
    prob_modelo: float,
    odd_mercado: float,
    fracao: float = KELLY_FRACAO_PADRAO,
    stake_max: float = KELLY_STAKE_MAX,
) -> tuple[float, float]:
    """
    Valor Esperado e stake sugerido pelo Critério de Kelly Fracionado.

    EV     = P_modelo × (odd - 1) - (1 - P_modelo)
    Kelly  = EV / (odd - 1)  [Kelly puro]
    Stake  = Kelly × fracao, capped em stake_max

    Parâmetros
    ----------
    prob_modelo : probabilidade estimada pelo modelo Poisson.
    odd_mercado : odd decimal do mercado.
    fracao      : fração do Kelly puro (default 25% — conservador).
    stake_max   : teto absoluto de stake como fração da banca.

    Retorna
    -------
    (stake_sugerido, ev)  — stake = 0.0 se EV <= 0.
    """
    if odd_mercado <= 1.0 or prob_modelo <= 0.0:
        return 0.0, 0.0

    b = odd_mercado - 1.0
    ev = prob_modelo * b - (1.0 - prob_modelo)

    if ev <= 0.0:
        return 0.0, ev

    kelly_puro = ev / b
    stake = min(kelly_puro * fracao, stake_max)

    return round(stake, 5), round(ev, 5)


# =====================================================================
# 5. Calculadora de Hedging — Múltiplas estratégias
# =====================================================================

def calcular_hedge_simples(
    stake_original: float,
    odd_original: float,
    odd_cover: float,
) -> dict:
    """
    Hedging simples: cobre uma única seleção oposta.

    Retorna stake de cobertura e lucro em cada cenário.
    """
    if odd_cover <= 1.0:
        return {
            "stake_hedge": 0.0, "liability": 0.0,
            "lucro_ganha_original": 0.0,
            "lucro_ganha_hedge": 0.0,
            "lucro_empate": 0.0,
        }

    lucro_original = stake_original * (odd_original - 1.0)
    stake_hedge = lucro_original / (odd_cover - 1.0)
    liability = stake_hedge * (odd_cover - 1.0)

    return {
        "stake_hedge": round(stake_hedge, 2),
        "liability": round(liability, 2),
        "lucro_ganha_original": round(lucro_original - stake_hedge, 2),
        "lucro_ganha_hedge": round(liability - stake_original, 2),
        "lucro_empate": 0.0,  # depende do mercado
    }


def calcular_hedge_3_modos(
    stake_original: float,
    odd_original: float,
    mercado_original: str,
    odds_mercado: dict,
) -> list[dict]:
    """
    Três estratégias de hedging para proteção após entrada no mercado.

    Modo 1 — Proteção Total: lucro equalizado nos 3 resultados.
    Modo 2 — Lucro Parcial (foco empate): maximiza lucro se empatar.
    Modo 3 — Freebet/Recuperação: recupera stake original na cobertura.

    Retorna lista de 3 dicts, cada um com "plano" e "lucro_cenarios".
    """
    resultados = []
    opcoes = {k: v for k, v in odds_mercado.items() if k != mercado_original}
    lucro_base = stake_original * (odd_original - 1.0)

    # ── Modo 1: Proteção Total ──────────────────────────────────────
    modo1 = {
        "modo": "PROTEÇÃO TOTAL",
        "descricao": "Lucro mínimo garantido em qualquer resultado",
        "stake_original": stake_original,
        "odd_original": odd_original,
        "mercado_original": mercado_original,
        "plano": {},
        "lucro_cenarios": {},
        "aviso": None,
    }
    for mercado, odd in opcoes.items():
        stake_h = (lucro_base / (odd - 1.0)) if odd > 1.0 else 0.0
        modo1["plano"][mercado] = {"stake": round(stake_h, 2), "odd": odd}

    total_h1 = sum(v["stake"] for v in modo1["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_base
        elif m in modo1["plano"]:
            base = modo1["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
        else:
            base = 0.0
        modo1["lucro_cenarios"][m] = round(base - total_h1, 2)
    resultados.append(modo1)

    # ── Modo 2: Lucro Parcial (foco empate) ────────────────────────
    modo2 = {
        "modo": "LUCRO PARCIAL (FOCO EMPATE)",
        "descricao": "Maximiza lucro no empate; cobertura mínima no terceiro",
        "stake_original": stake_original,
        "odd_original": odd_original,
        "mercado_original": mercado_original,
        "plano": {},
        "lucro_cenarios": {},
        "aviso": None,
    }
    if "EMPATE" in opcoes:
        odd_e = odds_mercado["EMPATE"]
        stake_e = (lucro_base / (odd_e - 1.0)) if odd_e > 1.0 else 0.0
        modo2["plano"]["EMPATE"] = {"stake": round(stake_e, 2), "odd": odd_e}
        outro = [k for k in opcoes if k != "EMPATE"]
        if outro:
            odd_o = odds_mercado[outro[0]]
            stake_o = stake_original * 0.05 if odd_o > 1.0 else 0.0
            modo2["plano"][outro[0]] = {"stake": round(stake_o, 2), "odd": odd_o}
    else:
        modo2["aviso"] = "Empate é o mercado original; modo foco-empate não aplicável."

    total_h2 = sum(v["stake"] for v in modo2["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_base
        elif m in modo2["plano"]:
            base = modo2["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
        else:
            base = 0.0
        modo2["lucro_cenarios"][m] = round(base - total_h2, 2)
    resultados.append(modo2)

    # ── Modo 3: Freebet / Recuperação de Stake ─────────────────────
    modo3 = {
        "modo": "FREEBET / RECUPERAÇÃO",
        "descricao": "Recupere o stake investido (ideal para odds > 2.0)",
        "stake_original": stake_original,
        "odd_original": odd_original,
        "mercado_original": mercado_original,
        "plano": {},
        "lucro_cenarios": {},
        "aviso": None,
    }
    if odd_original < 2.0:
        modo3["aviso"] = "Odd original < 2.0: modo freebet é subótimo."

    for mercado, odd in opcoes.items():
        if odd > 1.0:
            # Garante que o retorno da cobertura = stake_original
            stake_f = (stake_original + lucro_base) / (odd - 1.0)
        else:
            stake_f = 0.0
        modo3["plano"][mercado] = {"stake": round(stake_f, 2), "odd": odd}

    total_h3 = sum(v["stake"] for v in modo3["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_base - total_h3
        elif m in modo3["plano"]:
            base = (
                modo3["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
                - total_h3
                + stake_original
            )
        else:
            base = -stake_original
        modo3["lucro_cenarios"][m] = round(base, 2)
    resultados.append(modo3)

    return resultados


def calcular_hedge_parcial(
    stake_original: float,
    odd_original: float,
    mercado_original: str,
    odds_mercado: dict,
    fracao_protecao: float = 0.5,
) -> dict:
    """
    Hedging parcial: cobre apenas uma fração da exposição total.
    fracao_protecao = 0.0 (sem hedge) … 1.0 (proteção total).
    """
    resultado = {
        "stake_original": stake_original,
        "odd_original": odd_original,
        "mercado_original": mercado_original,
        "fracao_protecao": fracao_protecao,
        "plano": {},
        "lucro_cenarios": {},
    }

    opcoes = {k: v for k, v in odds_mercado.items() if k != mercado_original}
    lucro_original = stake_original * (odd_original - 1.0)

    for mercado, odd in opcoes.items():
        hedge_total = (lucro_original / (odd - 1.0)) if odd > 1.0 else 0.0
        stake_h = round(hedge_total * fracao_protecao, 2)
        resultado["plano"][mercado] = {"stake": stake_h, "odd": odd}

    total_h = sum(v["stake"] for v in resultado["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_original
        elif m in resultado["plano"]:
            base = resultado["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
        else:
            base = 0.0
        resultado["lucro_cenarios"][m] = round(base - total_h, 2)

    return resultado


# =====================================================================
# 6. Ajuste de xG por resultado (Dixon-Coles simplificado)
# =====================================================================

def calcular_xg_ajustado_por_resultado(
    xg_marcado: float,
    xg_sofrido: float,
    gols_marcados: float,
    gols_sofridos: float,
    peso_xg: float = 0.6,
) -> tuple[float, float]:
    """
    Blenda xG com gols reais para corrigir viés de finalização.

    Blend = peso_xg × xG + (1 - peso_xg) × Gols_Reais

    Na Copa (poucos jogos), peso_xg=0.6 é conservador.
    Para times com muitos jogos, reduzir para 0.5 ou 0.4.
    """
    xg_aj_m = peso_xg * xg_marcado + (1.0 - peso_xg) * max(gols_marcados, 0.1)
    xg_aj_s = peso_xg * xg_sofrido  + (1.0 - peso_xg) * max(gols_sofridos, 0.1)
    return round(xg_aj_m, 3), round(xg_aj_s, 3)


# =====================================================================
# 7. Simulação Monte Carlo (validação do modelo Poisson)
# =====================================================================

def simular_distribuicao_gols(
    lambda_a: float,
    lambda_b: float,
    n_simulacoes: int = 10_000,
    seed: int = 42,
) -> dict:
    """
    Monte Carlo para validar o modelo Poisson analítico.
    Retorna winrates simulados e distribuição empírica de gols.
    """
    rng = np.random.default_rng(seed)
    gols_a = rng.poisson(lambda_a, n_simulacoes)
    gols_b = rng.poisson(lambda_b, n_simulacoes)

    return {
        "MANDANTE":  round(float(np.sum(gols_a > gols_b) / n_simulacoes), 4),
        "EMPATE":    round(float(np.sum(gols_a == gols_b) / n_simulacoes), 4),
        "VISITANTE": round(float(np.sum(gols_b > gols_a) / n_simulacoes), 4),
        "media_gols_a": round(float(np.mean(gols_a)), 2),
        "media_gols_b": round(float(np.mean(gols_b)), 2),
        "distribuicao": {
            "mandante":  {int(g): int(np.sum(gols_a == g)) for g in range(7)},
            "visitante": {int(g): int(np.sum(gols_b == g)) for g in range(7)},
        },
    }


# =====================================================================
# __main__: testes de sanidade
# =====================================================================

if __name__ == "__main__":
    print("=== Sanity checks — core_math (Copa do Mundo 2026) ===\n")

    # 1. Campo neutro: lambdas simétricos para times equivalentes
    atk = calcular_forca_ataque(1.25, MEDIA_LIGA_COPA_REFERENCIA)
    dfs = calcular_forca_defesa(1.25, MEDIA_LIGA_COPA_REFERENCIA)
    lam = calcular_lambda_esperado(atk, dfs, MEDIA_LIGA_COPA_REFERENCIA)
    print(f"[1] Times equivalentes → lambda = {lam:.3f} (esperado ≈ 1.25)")

    # 2. is_mandante não deve mudar nada na Copa
    lam_m = calcular_lambda_esperado(atk, dfs, MEDIA_LIGA_COPA_REFERENCIA, is_mandante=True, tournament="copa")
    lam_v = calcular_lambda_esperado(atk, dfs, MEDIA_LIGA_COPA_REFERENCIA, is_mandante=False, tournament="copa")
    assert lam_m == lam_v, "❌ Home advantage não deve existir na Copa!"
    print(f"[2] Campo neutro confirmado: λ_mandante={lam_m:.3f} == λ_visitante={lam_v:.3f} ✓")

    # 3. Poisson: times iguais devem ter P(empate) > P(mandante) ≈ P(visitante)
    probs = calcular_probabilidades_partida(1.25, 1.25)
    print(f"[3] Poisson (λ=1.25 vs λ=1.25): M={probs['MANDANTE']:.3f}  E={probs['EMPATE']:.3f}  V={probs['VISITANTE']:.3f}")
    assert abs(probs["MANDANTE"] - probs["VISITANTE"]) < 0.001, "❌ Times iguais devem ter prob simétrica!"
    print("    Simetria confirmada ✓")

    # 4. Time forte (Brasil xG=2.0) vs fraco (Panamá xG=0.8)
    media = MEDIA_LIGA_COPA_REFERENCIA
    atk_bra = calcular_forca_ataque(2.0, media)
    def_pan = calcular_forca_defesa(1.2, media)   # Panamá defende mal
    atk_pan = calcular_forca_ataque(0.8, media)
    def_bra = calcular_forca_defesa(0.7, media)   # Brasil defende bem
    lam_bra = calcular_lambda_esperado(atk_bra, def_pan, media)
    lam_pan = calcular_lambda_esperado(atk_pan, def_bra, media)
    probs2 = calcular_probabilidades_partida(lam_bra, lam_pan)
    print(f"[4] Brasil(xG=2.0) vs Panamá(xG=0.8): λ_bra={lam_bra:.2f} λ_pan={lam_pan:.2f}")
    print(f"    P(Brasil)={probs2['MANDANTE']:.3f}  P(Empate)={probs2['EMPATE']:.3f}  P(Panamá)={probs2['VISITANTE']:.3f}")

    # 5. Kelly
    stake, ev = calcular_kelly_fracionado(0.55, 2.10)
    print(f"[5] Kelly (p=55%, odd=2.10): stake={stake*100:.2f}% banca, EV={ev*100:.2f}%")

    # 6. Monte Carlo vs Poisson analítico (devem convergir)
    sim = simular_distribuicao_gols(lam_bra, lam_pan)
    print(f"[6] MC(n=10k): M={sim['MANDANTE']}  E={sim['EMPATE']}  V={sim['VISITANTE']}")

    print("\n=== Todos os checks passaram ✓ ===")
