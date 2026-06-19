import db
db.init_db()
with db.get_connection() as conn:
    rows = conn.execute('''
        SELECT h.id, p.partida_id, tm.nome as mandante, tv.nome as visitante,
               h.mercado_sugerido, h.odd_disponivel, h.ev_calculado, h.stake_kelly,
               o.casa_aposta
        FROM historico_analises h
        JOIN partidas_agenda p ON h.partida_id = p.partida_id
        JOIN times_performance tm ON p.time_mandante_id = tm.time_id
        JOIN times_performance tv ON p.time_visitante_id = tv.time_id
        LEFT JOIN odds_mercado o ON h.partida_id = o.partida_id
        ORDER BY h.ev_calculado DESC
        LIMIT 20
    ''').fetchall()

print("Top 20 recomendacoes por EV:")
for r in rows:
    ev_pct = r["ev_calculado"] * 100
    stake_pct = r["stake_kelly"] * 100
    print(f"  Partida {r['partida_id']}: {r['mandante']} vs {r['visitante']}")
    print(f"    Mercado: {r['mercado_sugerido']} @ {r['odd_disponivel']:.2f} ({r['casa_aposta']})")
    print(f"    EV={ev_pct:.2f}% | Stake={stake_pct:.2f}%")

# Estatisticas gerais
with db.get_connection() as conn:
    total = conn.execute("SELECT COUNT(*) FROM historico_analises").fetchone()[0]
    pos = conn.execute("SELECT COUNT(*) FROM historico_analises WHERE ev_calculado > 0").fetchone()[0]
    print(f"\nTotal recomendacoes: {total}")
    print(f"Com EV+: {pos} ({pos/total*100:.1f}%)")
