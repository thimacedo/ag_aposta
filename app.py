"""
app.py
Painel Visual — Football Quant Analyst (Copa do Mundo 2026).

Dashboard que mostra:
  - Partidas reais capturadas pelo spider FIFA (jogadas e futuras)
  - Próximos jogos (com horário, estádio, cidade)
  - Recomendações EV+ (quando houver odds no banco)
  - Histórico de capturas do spider (auditoria)
  - Métricas gerais do banco
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import streamlit as st
import db

# Garante que o diretório do script está no PYTHONPATH para imports locais
sys.path.insert(0, str(Path(__file__).resolve().parent))


def carregar_metricas() -> dict:
    """Carrega métricas gerais do banco."""
    with db.get_connection() as conn:
        partidas_total = conn.execute(
            "SELECT COUNT(*) as n FROM partidas_agenda"
        ).fetchone()["n"]

        partidas_jogadas = conn.execute(
            "SELECT COUNT(*) as n FROM partidas_agenda WHERE status_fifa = 'FIM'"
        ).fetchone()["n"]

        partidas_futuras = conn.execute(
            """SELECT COUNT(*) as n FROM partidas_agenda
                WHERE status_fifa IS NULL AND horario_kickoff IS NOT NULL"""
        ).fetchone()["n"]

        times_total = conn.execute(
            "SELECT COUNT(*) as n FROM times_performance"
        ).fetchone()["n"]

        codigos_fifa = conn.execute(
            "SELECT COUNT(*) as n FROM times_fifa_codes"
        ).fetchone()["n"]

        capturas_total = conn.execute(
            "SELECT COUNT(*) as n FROM capturas_fifa"
        ).fetchone()["n"]

        ultima_captura = conn.execute(
            "SELECT capture_timestamp FROM capturas_fifa ORDER BY id DESC LIMIT 1"
        ).fetchone()

        analises_pendentes = conn.execute(
            "SELECT COUNT(*) as n FROM historico_analises WHERE resultado_partida = 'PENDENTE'"
        ).fetchone()["n"]

        return {
            "partidas_total": partidas_total,
            "partidas_jogadas": partidas_jogadas,
            "partidas_futuras": partidas_futuras,
            "times_total": times_total,
            "codigos_fifa": codigos_fifa,
            "capturas_total": capturas_total,
            "ultima_captura": (ultima_captura["capture_timestamp"]
                               if ultima_captura else "Nunca"),
            "analises_pendentes": analises_pendentes,
        }


def carregar_proximos_jogos(janela_horas: int = 36, limite: int = 50) -> list[dict]:
    """
    Carrega próximos jogos dentro da janela temporal, com odds e análises EV+ embutidas.

    janela_horas: quantas horas à frente olhar (padrão 36h = hoje + amanhã).
    Se janela_horas=0, retorna todos os jogos futuros sem limite de data.
    """
    with db.get_connection() as conn:
        if janela_horas > 0:
            filtro_data = f"AND p.data_evento <= datetime('now', '+{janela_horas} hours')"
        else:
            filtro_data = ""

        query = f"""
        SELECT
            p.partida_id,
            p.data_evento,
            p.horario_kickoff,
            p.nome_mandante,
            p.nome_visitante,
            p.codigo_fifa_home,
            p.codigo_fifa_away,
            p.flag_url_home,
            p.flag_url_away,
            p.fase,
            p.grupo,
            p.estadio,
            p.cidade,
            p.status_fifa,
            p.score_home,
            p.score_away,
            MAX(o.odd_mandante)  AS odd_mandante,
            MAX(o.odd_empate)    AS odd_empate,
            MAX(o.odd_visitante) AS odd_visitante,
            COUNT(DISTINCT o.casa_aposta) AS num_casas,
            GROUP_CONCAT(DISTINCT h.mercado_sugerido) AS mercados_ev,
            MAX(h.ev_calculado)  AS melhor_ev,
            MAX(h.stake_kelly)   AS stake_kelly
        FROM partidas_agenda p
        LEFT JOIN odds_mercado o ON p.partida_id = o.partida_id
        LEFT JOIN historico_analises h
               ON p.partida_id = h.partida_id
              AND h.resultado_partida = 'PENDENTE'
        WHERE (p.status_fifa IS NULL OR p.status_fifa != 'FIM')
          AND p.data_evento >= datetime('now', '-2 hours')
          {filtro_data}
        GROUP BY p.partida_id
        ORDER BY p.data_evento ASC
        LIMIT ?
        """
        return [dict(r) for r in conn.execute(query, (limite,)).fetchall()]


def carregar_jogos_do_brasil() -> list[dict]:
    """Carrega todos os jogos do Brasil."""
    with db.get_connection() as conn:
        query = """
        SELECT
            p.data_evento,
            p.horario_kickoff,
            p.nome_mandante,
            p.nome_visitante,
            p.codigo_fifa_home,
            p.codigo_fifa_away,
            p.fase,
            p.grupo,
            p.estadio,
            p.cidade,
            p.status_fifa,
            p.score_home,
            p.score_away
        FROM partidas_agenda p
        WHERE p.codigo_fifa_home = 'BRA' OR p.codigo_fifa_away = 'BRA'
        ORDER BY p.data_evento
        """
        return [dict(r) for r in conn.execute(query).fetchall()]


def carregar_ultimos_resultados(limite: int = 10) -> list[dict]:
    """Carrega últimos jogos finalizados."""
    with db.get_connection() as conn:
        query = """
        SELECT
            p.data_evento,
            p.nome_mandante,
            p.nome_visitante,
            p.codigo_fifa_home,
            p.codigo_fifa_away,
            p.score_home,
            p.score_away,
            p.status_fifa,
            p.fase,
            p.grupo,
            p.estadio
        FROM partidas_agenda p
        WHERE p.status_fifa = 'FIM'
        ORDER BY p.data_evento DESC
        LIMIT ?
        """
        return [dict(r) for r in conn.execute(query, (limite,)).fetchall()]


def carregar_recomendacoes(apenas_hoje: bool = False) -> list[dict]:
    """Carrega recomendações EV+ pendentes (LEFT JOIN para tolerar placeholders)."""
    with db.get_connection() as conn:
        query = """
        SELECT DISTINCT
            h.*,
            p.data_evento,
            COALESCE(tm.nome, p.nome_mandante) AS mandante,
            COALESCE(tv.nome, p.nome_visitante) AS visitante,
            p.codigo_fifa_home,
            p.codigo_fifa_away,
            p.fase,
            p.grupo
        FROM historico_analises h
        JOIN partidas_agenda p ON h.partida_id = p.partida_id
        LEFT JOIN times_performance tm ON p.time_mandante_id = tm.time_id
        LEFT JOIN times_performance tv ON p.time_visitante_id = tv.time_id
        WHERE h.resultado_partida = 'PENDENTE'
        """
        if apenas_hoje:
            query += " AND date(p.data_evento) = date('now') "
        
        query += " ORDER BY h.ev_calculado DESC"
        
        return [dict(r) for r in conn.execute(query).fetchall()]


def carregar_capturas_recentes(limite: int = 10) -> list[dict]:
    """Carrega histórico das últimas capturas do spider."""
    with db.get_connection() as conn:
        query = """
        SELECT id, capture_timestamp, matches_found, matches_played,
               matches_upcoming, changes_detected, success,
               imported_to_db, imported_at
        FROM capturas_fifa
        ORDER BY id DESC
        LIMIT ?
        """
        return [dict(r) for r in conn.execute(query, (limite,)).fetchall()]


def _flag_img(url: str | None, code: str | None) -> str:
    """Retorna HTML para mostrar a bandeira ou o código."""
    if url:
        return f'<img src="{url}" width="28" height="20" style="vertical-align:middle"/>'
    if code:
        return f'<span style="display:inline-block;padding:2px 6px;background:#e0e0e0;border-radius:3px;font-size:11px;font-weight:bold">{code}</span>'
    return ""


def _score_box(score: int | None, status: str | None) -> str:
    """Retorna HTML do placar ou status."""
    if status == "FIM" and score is not None:
        return f'<span style="font-weight:bold;font-size:16px">{score}</span>'
    if status and status != "FIM":
        return f'<span style="color:#ff6b35;font-weight:bold">{status}</span>'
    return '<span style="color:#999">—</span>'


def main() -> None:
    st.set_page_config(
        page_title="Football Quant Analyst — Copa 2026",
        page_icon="⚽",
        layout="wide",
    )
    st.title("⚽ Football Quant Analyst — Copa 2026")

    # ---------------------------------------------------------------------
    # Métricas principais (cabeçalho)
    # ---------------------------------------------------------------------
    metricas = carregar_metricas()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("🎯 Partidas no banco", metricas["partidas_total"])
    col2.metric("✅ Jogadas (FIM)", metricas["partidas_jogadas"])
    col3.metric("⏰ Futuras", metricas["partidas_futuras"])
    col4.metric("🌐 Capturas spider", metricas["capturas_total"])
    col5.metric("📊 Análises EV+ pendentes", metricas["analises_pendentes"])

    st.caption(f"Última captura do spider: **{metricas['ultima_captura']}**")

    # ---------------------------------------------------------------------
    # Tabs
    # ---------------------------------------------------------------------
    tab_jogos, tab_brasil, tab_resultados, tab_recs, tab_capturas, tab_acoes = st.tabs([
        "📅 Próximos jogos",
        "🇧🇷 Jogos do Brasil",
        "🏆 Últimos resultados",
        "📊 Recomendações EV+",
        "🕷️ Capturas do spider",
        "⚙️ Ações",
    ])

    # ---------------------------------------------------------------------
    # ---------------------------------------------------------------------
    # TAB 1: PRÓXIMOS JOGOS (Navegação Dinâmica: Lista -> Detalhes)
    # ---------------------------------------------------------------------
    with tab_jogos:
        # Inicializa o estado de navegação na primeira vez
        if 'selected_match_id' not in st.session_state:
            st.session_state.selected_match_id = None

        # ==========================================
        # TELA 2: DETALHES DO JOGO (Menu Clicável)
        # ==========================================
        if st.session_state.selected_match_id:
            match_id = st.session_state.selected_match_id
            # Encontra os dados do jogo clicado
            match_data = next((j for j in jogos if j['partida_id'] == match_id), None)

            if match_data:
                # Botão de Voltar
                if st.button("← Voltar para a lista de jogos", use_container_width=True):
                    st.session_state.selected_match_id = None
                    st.rerun()
                
                # Extrai variáveis do jogo selecionado
                j = match_data
                home_flag  = _flag_img(j.get("flag_url_home"),  j.get("codigo_fifa_home"))
                away_flag  = _flag_img(j.get("flag_url_away"),  j.get("codigo_fifa_away"))
                horario    = j.get("horario_kickoff") or "—"
                fase_str   = f"{j.get('fase') or ''} {j.get('grupo') or ''}".strip()
                melhor_ev   = j.get("melhor_ev")
                stake_kelly = j.get("stake_kelly")
                mercados_ev = j.get("mercados_ev") or ""
                tem_ev = bool(melhor_ev and melhor_ev > 0)
                odd_m     = j.get("odd_mandante")
                odd_e     = j.get("odd_empate")
                odd_v     = j.get("odd_visitante")
                num_casas = j.get("num_casas") or 0

                # Formata o Badge de EV (se houver)
                ev_badge = ""
                if tem_ev:
                    ev_pct = melhor_ev * 100
                    stake_pct = (stake_kelly or 0) * 100
                    ev_badge = f'<div style="text-align:center;margin-top:12px"><span style="background:#1a7f37;color:#fff;padding:6px 16px;border-radius:12px;font-size:14px;font-weight:bold">⚡ Oportunidade EV+ {ev_pct:.1f}% — {mercados_ev} — Stake: {stake_pct:.1f}%</span></div>'

                # Formata as Odds Maiores
                if odd_m and odd_e and odd_v:
                    odds_html = (
                        '<div style="display:flex;gap:12px;justify-content:center;margin-top:14px">' +
                        '<span style="background:#f0f4ff;border:1px solid #c0cfe8;padding:10px 24px;border-radius:8px;font-size:18px;font-weight:bold">' +
                        f'1 &nbsp; {odd_m:.2f}</span>' +
                        '<span style="background:#f0f4ff;border:1px solid #c0cfe8;padding:10px 24px;border-radius:8px;font-size:18px;font-weight:bold">' +
                        f'X &nbsp; {odd_e:.2f}</span>' +
                        '<span style="background:#f0f4ff;border:1px solid #c0cfe8;padding:10px 24px;border-radius:8px;font-size:18px;font-weight:bold">' +
                        f'2 &nbsp; {odd_v:.2f}</span>' +
                        f'<span style="color:#aaa;font-size:12px;align-self:center; margin-left:10px">{num_casas} casas</span>' +
                        '</div>'
                    )
                else:
                    odds_html = '<div style="text-align:center;margin-top:14px;color:#bbb;font-size:14px">Sem odds disponíveis</div>'

                borda = "#1a7f37" if tem_ev else "#e0e0e0"
                fundo = "#f6fff8" if tem_ev else "#fafafa"
                local_str = (j["estadio"] or "") + (" — " if j["estadio"] and j["cidade"] else "") + (j["cidade"] or "")

                # Card Grande do Jogo
                st.markdown(
                    f'''<div style="border:2px solid {borda};border-radius:14px;padding:28px;background:{fundo};margin-top:10px">
                        <div style="display:flex;align-items:center;justify-content:space-between">
                            <div style="flex:1;text-align:right">
                                {home_flag}
                                <strong style="margin-left:12px;font-size:22px">{j["nome_mandante"]}</strong>
                            </div>
                            <div style="margin:0 40px;text-align:center;min-width:110px">
                                <div style="font-size:20px;color:#333;font-weight:bold">{horario} BRT</div>
                                <div style="font-size:13px;color:#888;margin-top:4px">{fase_str}</div>
                            </div>
                            <div style="flex:1">
                                <strong style="margin-right:12px;font-size:22px">{j["nome_visitante"]}</strong>
                                {away_flag}
                            </div>
                        </div>
                        {odds_html}
                        {ev_badge}
                        <div style="font-size:13px;color:#888;text-align:center;margin-top:14px">{local_str}</div>
                    </div>''',
                    unsafe_allow_html=True,
                )

                # ==========================================
                # RODAPÉ DO JOGO: IA + HEDGE
                # ==========================================
                if odd_m and odd_e and odd_v:
                    with st.expander("🧠 Análise Rápida + 🛡️ Cash Out (Hedge)", expanded=False):
                        odd_base_ev = odd_m
                        if "VISITANTE" in mercados_ev: odd_base_ev = odd_v
                        elif "EMPATE" in mercados_ev: odd_base_ev = odd_e
                        odd_contraria = odd_v if "MANDANTE" in mercados_ev else odd_m

                        c1, c2, c3 = st.columns(3)
                        with c1:
                            stake_input = st.number_input("Sua Aposta (R$)", min_value=1.0, value=100.0, step=10.0, key=f"stk_{j['partida_id']}")
                        with c2:
                            odd_aposta_input = st.number_input("Odd da Aposta", min_value=1.01, value=float(odd_base_ev), step=0.01, key=f"od1_{j['partida_id']}")
                        with c3:
                            odd_hedge_input = st.number_input("Odd p/ Cobrir", min_value=1.01, value=float(odd_contraria), step=0.01, key=f"od2_{j['partida_id']}")

                        retorno_base = stake_input * odd_aposta_input
                        stake_hedge_calc = retorno_base / odd_hedge_input
                        lucro_garantido = retorno_base - (stake_input + stake_hedge_calc)
                        
                        if lucro_garantido > 0:
                            st.success(f"✅ **Cash Out Viável Agora:** Aposte **R$ {stake_hedge_calc:.2f}** na odd contrária para garantir **R$ {lucro_garantido:.2f}** de lucro líquido.")
                        else:
                            st.info(f"⏳ **Sem lucro pré-jogo.** Espere o jogo começar. Se o favorito fizer gol cedo, a odd contrária sobe e aí você usa essa calculadora para travar o lucro.")

                        st.divider()
                        
                        @st.cache_data(ttl=600, show_spinner="Consultando especialista...")
                        def chamar_ia_unificada(pid, s, oh, lg, sh):
                            from llm_agent import gerar_analise_com_hedge
                            return gerar_analise_com_hedge(j, s, oh, lg, sh)
                        
                        analise_texto = chamar_ia_unificada(j["partida_id"], stake_input, odd_hedge_input, lucro_garantido, stake_hedge_calc)
                        st.markdown(analise_texto)

            else:
                st.warning("Jogo não encontrado.")
                st.session_state.selected_match_id = None
                st.rerun()


        # ==========================================
        # TELA 1: LISTA LIMPA DE JOGOS (Início Rápido)
        # ==========================================
        else:
            col_foco, col_expand = st.columns([4, 1])
            with col_foco:
                st.markdown("### ⚽ Próximos Jogos")
            with col_expand:
                ver_futuros = st.toggle("Todos os jogos", value=False)

            janela = 0 if ver_futuros else 36
            jogos = carregar_proximos_jogos(janela_horas=janela, limite=60)

            if not jogos:
                st.info("Nenhum jogo nas próximas 36 horas. Ative 'Todos os jogos' ou execute o spider.")
            else:
                # CSS para deixar os botões parecidos com um App de celular
                st.markdown("""
                <style>
                    div.stButton > button[kind="secondary"] {
                        width: 100%;
                        text-align: left;
                        padding: 14px 20px;
                        border: 1px solid #f0f0f0;
                        border-radius: 10px;
                        background-color: #ffffff;
                        transition: all 0.2s;
                        font-size: 15px;
                    }
                    div.stButton > button[kind="secondary"]:hover {
                        background-color: #f8f9fa;
                        border-color: #d0d0d0;
                        transform: translateY(-1px);
                        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
                    }
                </style>
                """, unsafe_allow_html=True)

                from collections import defaultdict
                from datetime import datetime, date as date_type
                jogos_por_data: dict = defaultdict(list)
                for j in jogos:
                    data_key = (j["data_evento"] or "")[:10]
                    jogos_por_data[data_key].append(j)

                for data_key in sorted(jogos_por_data.keys()):
                    grupo_jogos = jogos_por_data[data_key]
                    try:
                        d = datetime.strptime(data_key, "%Y-%m-%d").date()
                        delta = (d - date_type.today()).days
                        label_data = "📍 Hoje" if delta == 0 else ("⏭️ Amanhã" if delta == 1 else f"📆 {data_key}")
                    except Exception:
                        label_data = f"📆 {data_key}"

                    st.markdown(f"**{label_data}**")

                    for j in grupo_jogos:
                        horario = j.get("horario_kickoff") or "--:--"
                        m = j.get("nome_mandante") or "?"
                        v = j.get("nome_visitante") or "?"
                        tem_odds = bool(j.get("odd_mandante"))
                        
                        # Badges rápidos na lista
                        badge = ""
                        if j.get("melhor_ev") and j.get("melhor_ev") > 0:
                            badge = " ⚡EV+"
                        elif not tem_odds:
                            badge = " (Sem odds)"
                        
                        # O botão que serve como a linha do menu
                        label_btn = f"⏰ {horario}  —  {m}  vs  {v}{badge}"
                        
                        if st.button(label_btn, key=f"row_{j['partida_id']}", use_container_width=True):
                            st.session_state.selected_match_id = j['partida_id']
                            st.rerun()

    # ---------------------------------------------------------------------
    # TAB 2: Jogos do Brasil
    # ---------------------------------------------------------------------
    with tab_brasil:
        st.subheader("🇧🇷 Todos os jogos da Seleção Brasileira")
        jogos_bra = carregar_jogos_do_brasil()

        if not jogos_bra:
            st.info("Nenhum jogo do Brasil encontrado no banco ainda.")
        else:
            data = []
            for j in jogos_bra:
                placar = "—"
                if j.get("status_fifa") == "FIM" and j.get("score_home") is not None:
                    placar = f"{j['score_home']} × {j['score_away']}"
                elif j.get("horario_kickoff"):
                    placar = j["horario_kickoff"]

                data.append({
                    "Data": j["data_evento"][:10] if j["data_evento"] else "",
                    "Horário": j.get("horario_kickoff") or "",
                    "Mandante": j["nome_mandante"] or "",
                    "Visitante": j["nome_visitante"] or "",
                    "Placar/Status": placar,
                    "Fase": j.get("fase") or "",
                    "Grupo": j.get("grupo") or "",
                    "Estádio": j.get("estadio") or "",
                    "Cidade": j.get("cidade") or "",
                })
            st.dataframe(data, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------------------
    # TAB 3: Últimos resultados
    # ---------------------------------------------------------------------
    with tab_resultados:
        st.subheader("🏆 Últimos resultados (FIM)")
        resultados = carregar_ultimos_resultados(limite=20)

        if not resultados:
            st.info("Nenhum resultado finalizado no banco ainda.")
        else:
            data = []
            for r in resultados:
                data.append({
                    "Data": r["data_evento"][:10] if r["data_evento"] else "",
                    "Mandante": r["nome_mandante"] or "",
                    "Placar": f"{r['score_home']} × {r['score_away']}",
                    "Visitante": r["nome_visitante"] or "",
                    "Fase": r.get("fase") or "",
                    "Grupo": r.get("grupo") or "",
                    "Estádio": r.get("estadio") or "",
                })
            st.dataframe(data, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------------------
    # TAB 4: Recomendações EV+
    # ---------------------------------------------------------------------
    with tab_recs:
        st.subheader("📊 Recomendações com Valor Esperado Positivo (EV+)")
        apenas_hoje = st.checkbox("Apenas jogos de hoje", value=False)
        recs = carregar_recomendacoes(apenas_hoje=apenas_hoje)

        if not recs:
            st.info("Nenhuma análise EV+ ativa. Para gerar, é necessário ter odds no banco "
                    "(tabela `odds_mercado`). Execute `python worker_analyze.py` após popular odds.")
        else:
            for rec in recs:
                with st.container(border=True):
                    col_a, col_b, col_c, col_d = st.columns([2, 1, 1, 1])
                    with col_a:
                        st.markdown(f"### {rec['mandante']} vs {rec['visitante']}")
                        st.caption(f"Data: {rec['data_evento']} | "
                                   f"{rec.get('fase', '')} {rec.get('grupo', '')}")
                    with col_b:
                        st.metric("Mercado", rec["mercado_sugerido"])
                    with col_c:
                        st.metric("Odd", f"{rec['odd_disponivel']:.2f}")
                    with col_d:
                        st.metric(
                            "EV Calculado",
                            f"{rec['ev_calculado']*100:.2f}%",
                            delta=f"Stake: {rec['stake_kelly']*100:.2f}%",
                        )

    # ---------------------------------------------------------------------
    # TAB 5: Capturas do spider
    # ---------------------------------------------------------------------
    with tab_capturas:
        st.subheader("🕷️ Histórico de capturas do spider FIFA")
        capturas = carregar_capturas_recentes(limite=20)

        if not capturas:
            st.warning("Nenhuma captura registrada. Execute o spider em '⚙️ Ações'.")
        else:
            data = []
            for c in capturas:
                data.append({
                    "ID": c["id"],
                    "Timestamp": c["capture_timestamp"][:19] if c["capture_timestamp"] else "",
                    "Partidas": c["matches_found"],
                    "Jogadas": c["matches_played"],
                    "Futuras": c["matches_upcoming"],
                    "Mudanças": c["changes_detected"],
                    "Sucesso": "✅" if c["success"] else "❌",
                    "Importada p/ DB": "✅" if c["imported_to_db"] else "⏳",
                    "Importada em": c["imported_at"][:19] if c["imported_at"] else "",
                })
            st.dataframe(data, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------------------
    # TAB 6: Ações
    # ---------------------------------------------------------------------
    with tab_acoes:
        st.subheader("⚙️ Ações manuais")

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("#### 🕷️ Executar spider + sync (uma vez)")
            if st.button("▶️ Rodar agora", type="primary", use_container_width=True):
                with st.spinner("Executando spider FIFA (pode levar ~15s)..."):
                    try:
                        result = subprocess.run(
                            [sys.executable, "worker_ingest.py", "--once"],
                            capture_output=True, text=True, timeout=180,
                            cwd=str(Path(__file__).resolve().parent),
                        )
                        st.code(result.stdout[-3000:] if result.stdout else "(sem output)")
                        if result.returncode == 0:
                            st.success("✅ Spider + sync concluídos! Recarregue a página.")
                        else:
                            st.error(f"❌ Erro (returncode={result.returncode})")
                            if result.stderr:
                                st.code(result.stderr[-2000:])
                    except subprocess.TimeoutExpired:
                        st.error("❌ Timeout (>180s).")
                    except FileNotFoundError:
                        st.error("❌ Python não encontrado.")

        with col_b:
            st.markdown("#### 📊 Gerar análise EV+")
            st.caption("Requer odds no banco (tabela `odds_mercado`).")
            ev_min = st.slider("EV mínimo (%)", min_value=1, max_value=10, value=2, step=1) / 100
            if st.button("🔍 Analisar", use_container_width=True):
                with st.spinner(f"Analisando com EV mínimo = {ev_min:.0%}..."):
                    try:
                        result = subprocess.run(
                            [sys.executable, "worker_analyze.py", str(ev_min)],
                            capture_output=True, text=True, timeout=60,
                            cwd=str(Path(__file__).resolve().parent),
                        )
                        st.code(result.stdout[-3000:] if result.stdout else "(sem output)")
                        if result.returncode == 0:
                            st.success("✅ Análise concluída! Veja a aba 'Recomendações EV+'.")
                        else:
                            st.error(f"❌ Erro (returncode={result.returncode})")
                            if result.stderr:
                                st.code(result.stderr[-2000:])
                    except subprocess.TimeoutExpired:
                        st.error("❌ Timeout (>60s).")

        st.divider()
        st.markdown("#### 📁 Estrutura de arquivos")
        st.code("""
C:\\Projetos\\ag_aposta\\
├── app.py
├── db.py
├── schema.sql
├── core_math.py
├── risk_agent.py
├── worker_ingest.py
├── worker_analyze.py
├── fifa_spider.py
├── fifa_sync.py
├── data\\
│   └── quant_bet.db          (SQLite)
└── fifa_spider\\              (outputs do spider)
    ├── captures\\             (JSONs por execução)
    ├── raw_html\\             (HTML bruto)
    └── logs\\
        ├── spider.log
        └── changes.log
""")


if __name__ == "__main__":
    db.init_db()
    main()
