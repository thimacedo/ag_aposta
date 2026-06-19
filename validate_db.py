import sqlite3

def main():
    conn = sqlite3.connect('data/quant_bet.db')
    conn.row_factory = sqlite3.Row
    
    # Check columns
    cursor = conn.execute("PRAGMA table_info(partidas_agenda)")
    columns = [row['name'] for row in cursor]
    print("Colunas da tabela partidas_agenda:", columns)
    
    total = conn.execute('SELECT COUNT(*) FROM partidas_agenda').fetchone()[0]
    jogadas = conn.execute("SELECT COUNT(*) FROM partidas_agenda WHERE status_fifa='FIM'").fetchone()[0]
    
    # We can check other status or columns based on what's available
    status_counts = conn.execute("SELECT status_fifa, COUNT(*) FROM partidas_agenda GROUP BY status_fifa").fetchall()
    print("\nContagem por status_fifa:")
    for row in status_counts:
        print(f"  {row[0]}: {row[1]}")
        
    capturas = conn.execute('SELECT COUNT(*) FROM capturas_fifa').fetchone()[0]
    
    print(f"\nPartidas totais: {total}")
    print(f"Jogadas (FIM): {jogadas}")
    print(f"Capturas registradas: {capturas}")
    
    # Let's see some Brazil games using dynamic columns
    has_mandante = "nome_mandante" in columns or "time_mandante" in columns
    m_col = "nome_mandante" if "nome_mandante" in columns else "time_mandante"
    v_col = "nome_visitante" if "nome_visitante" in columns else "time_visitante"
    
    print("\nAmostra de 3 jogos do Brasil:")
    query = f"""
        SELECT {m_col}, {v_col}, status_fifa, data_evento 
        FROM partidas_agenda 
        WHERE {m_col} = 'Brasil' OR {v_col} = 'Brasil'
        LIMIT 3
    """
    cursor = conn.execute(query)
    for row in cursor:
        print(dict(row))

if __name__ == '__main__':
    main()
