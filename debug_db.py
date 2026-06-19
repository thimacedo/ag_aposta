import db
db.init_db()
with db.get_connection() as conn:
    print(f"Times: {conn.execute('SELECT COUNT(*) FROM times_performance').fetchone()[0]}")
    print(f"Partidas: {conn.execute('SELECT COUNT(*) FROM partidas_agenda').fetchone()[0]}")
    print(f"Odds: {conn.execute('SELECT COUNT(*) FROM odds_mercado').fetchone()[0]}")
    print("\n--- Times ---")
    for r in conn.execute("SELECT * FROM times_performance LIMIT 5").fetchall():
        print(dict(r))
    print("\n--- Partidas (primeiras) ---")
    for r in conn.execute("SELECT * FROM partidas_agenda LIMIT 5").fetchall():
        print(dict(r))
    print("\n--- Odd por partida (amostra) ---")
    for r in conn.execute("SELECT * FROM odds_mercado LIMIT 5").fetchall():
        print(dict(r))
