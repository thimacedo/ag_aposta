"""
fetcher_agent.py
Agente responsável por buscar odds na The Odds API e salvar no SQLite.
"""

import os
import logging
import requests
import sqlite3
from dotenv import load_dotenv

# Configuração de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Carrega variáveis de ambiente
load_dotenv()

API_KEY = os.getenv("THE_ODDS_API_KEY")
BASE_URL = os.getenv("THE_ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
DB_PATH = "data/quant_bet.db"

# Dicionário de mapeamento crucial: A API usa inglês, o Spider da FIFA usa português.
MAPEAMENTO_TIMES = {
    "Brazil": "Brasil",
    "Argentina": "Argentina",
    "France": "França",
    "Germany": "Alemanha",
    "Spain": "Espanha",
    "England": "Inglaterra",
    "Portugal": "Portugal",
    "Netherlands": "Holanda",
    "Belgium": "Bélgica",
    "Croatia": "Croácia",
    "Uruguay": "Uruguai",
    "Mexico": "México",
    "United States": "Estados Unidos",
    "USA": "EUA",
    "Canada": "Canadá",
    "South Korea": "Coreia do Sul",
    "Korea Republic": "Coreia do Sul",
    "Japan": "Japão",
    "Australia": "Austrália",
    "Morocco": "Marrocos",
    "Iran": "Irã",
    "Nigeria": "Nigéria",
    "Ghana": "Gana",
    "Senegal": "Senegal",
    "Egypt": "Egito",
    "Peru": "Peru",
    "Ecuador": "Equador",
    "Saudi Arabia": "Arábia Saudita",
    "Qatar": "Catar",
    "New Zealand": "Nova Zelândia",
    "Panama": "Panamá",
    "Jamaica": "Jamaica",
    "Honduras": "Honduras",
    "Venezuela": "Venezuela",
    "Tunisia": "Tunísia",
    "Algeria": "Argélia",
    "Ivory Coast": "Costa do Marfim",
    "Austria": "Áustria",
    "Denmark": "Dinamarca",
    "Norway": "Noruega",
    "Scotland": "Escócia",
    "Wales": "Gales",
    "Hungary": "Hungria",
    "Romania": "Romênia",
    "Czech Republic": "Tchéquia",
    "Slovakia": "Eslováquia",
    "Greece": "Grécia",
    "Albania": "Albânia",
    "Iceland": "Islândia",
    "Ireland": "Irlanda",
    "Haiti": "Haiti",
    "Turkey": "Turquia",
    "Costa Rica": "Costa Rica",
    "Cameroon": "Camarões",
    "Colombia": "Colômbia",
    "Switzerland": "Suíça",
    "Poland": "Polônia",
    "Serbia": "Sérvia",
    "Sweden": "Suécia",
    "Ukraine": "Ucrânia",
    "Chile": "Chile",
    "Paraguay": "Paraguai",
    "Bolivia": "Bolívia",
    "Bulgaria": "Bulgária",
    "Bosnia & Herzegovina": "Bósnia e Herzegovina",
    "South Africa": "Africa do Sul",
    "Cape Verde": "Cabo Verde",
    "DR Congo": "RD Congo"
}

def extrair_odds_the_odds_api() -> list[dict]:
    """Busca odds na The Odds API para a Copa do Mundo."""
    if not API_KEY:
        logger.error("THE_ODDS_API_KEY não encontrada no .env")
        return []

    url = f"{BASE_URL}/sports/soccer_fifa_world_cup/odds/"
    params = {
        'apiKey': API_KEY,
        'regions': 'eu',  # Europa tem odds melhores e mais casas
        'markets': 'h2h', # Apenas Match Winner (Mandante, Empate, Visitante)
        'oddsFormat': 'decimal'
    }

    logger.info("Buscando odds na The Odds API...")
    response = requests.get(url, params=params, timeout=15)

    if response.status_code != 200:
        logger.error(f"Erro na API The Odds: {response.status_code} - {response.text}")
        return []

    data = response.json()
    logger.info(f"{len(data)} jogos recebidos da API.")
    return data

def salvar_odds_banco(odds_api: list[dict]) -> int:
    """Percorre as odds da API, encontra o partida_id no banco e salva."""
    if not odds_api:
        return 0

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    salvas = 0

    for jogo in odds_api:
        home_api = jogo.get('home_team')
        away_api = jogo.get('away_team')

        # 1. Traduz os nomes usando o dicionário
        home_banco = MAPEAMENTO_TIMES.get(home_api, home_api)
        away_banco = MAPEAMENTO_TIMES.get(away_api, away_api)

        # 2. Busca o partida_id no banco de dados local
        # Usamos LIKE para evitar problemas com acentos/espaçamento sutil
        query = """
            SELECT partida_id FROM partidas_agenda 
            WHERE (nome_mandante LIKE ? OR nome_mandante = ?)
              AND (nome_visitante LIKE ? OR nome_visitante = ?)
            LIMIT 1
        """
        cursor.execute(query, (f"%{home_banco}%", home_banco, f"%{away_banco}%", away_banco))
        row = cursor.fetchone()

        if not row:
            logger.info(f"Partida não encontrada no banco local: {home_api} vs {away_api} (Tentou: {home_banco} vs {away_banco})")
            continue

        partida_id = row['partida_id']

        # 3. Extrai as odds de cada casa de apostas retornada
        for bookmaker in jogo.get('bookmakers', []):
            casa_aposta = bookmaker.get('title', 'Desconhecida')
            
            # Pega apenas o mercado h2h (Match Winner)
            mercados = bookmaker.get('markets', [])
            h2h = next((m for m in mercados if m['key'] == 'h2h'), None)
            
            if not h2h:
                continue

            outcomes = h2h.get('outcomes', [])
            odd_mandante = next((o['price'] for o in outcomes if o['name'] == home_api), None)
            odd_visitante = next((o['price'] for o in outcomes if o['name'] == away_api), None)
            odd_empate = next((o['price'] for o in outcomes if o['name'] == 'Draw'), None)

            if not all([odd_mandante, odd_empate, odd_visitante]):
                continue

            # 4. Upsert no banco (evita duplicatas se rodar duas vezes)
            cursor.execute("""
                INSERT INTO odds_mercado (partida_id, casa_aposta, odd_mandante, odd_empate, odd_visitante)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(partida_id, casa_aposta) DO UPDATE SET
                    odd_mandante = excluded.odd_mandante,
                    odd_empate = excluded.odd_empate,
                    odd_visitante = excluded.odd_visitante
            """, (partida_id, casa_aposta, odd_mandante, odd_empate, odd_visitante))
            salvas += 1

    conn.commit()
    conn.close()
    logger.info(f"Total de {salvas} linhas de odds salvas/atualizadas no banco.")
    return salvas

# Mantendo compatibilidade com o modo --legacy do worker_ingest.py
def rotina_ingestao_diaria():
    odds = extrair_odds_the_odds_api()
    if odds:
        salvar_odds_banco(odds)

if __name__ == "__main__":
    # Teste rápido direto no terminal
    odds = extrair_odds_the_odds_api()
    if odds:
        salvar_odds_banco(odds)
    else:
        print("Nenhuma odd obtida.")
