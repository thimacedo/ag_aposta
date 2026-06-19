import sqlite3
conn = sqlite3.connect('data/quant_bet.db')
matches = conn.execute('SELECT partida_id, nome_mandante, nome_visitante FROM partidas_agenda WHERE nome_mandante LIKE "%Equador%" OR nome_visitante LIKE "%Equador%" OR nome_mandante LIKE "%Cura%" OR nome_visitante LIKE "%Cura%"').fetchall()
for m in matches:
    print(m)
conn.close()
