"""
check_today.py
Teste de APIs externas (API-Football + The Odds API).

OPCIONAL — só é necessário se você quiser popular a tabela `odds_mercado`
para que o Agente de Risco possa gerar recomendações EV+.

O spider FIFA (fifa_spider.py) NÃO depende destas APIs — ele usa
Playwright direto no site da FIFA.
"""
import os
import datetime
import requests
from dotenv import load_dotenv

if not os.path.exists(".env"):
    raise FileNotFoundError(
        "Arquivo .env não encontrado! Copie .env.example para .env e preencha as chaves."
    )
load_dotenv()

url = 'https://v3.football.api-sports.io/fixtures'
headers = {'x-apisports-key': os.getenv('API_FOOTBALL_KEY')}

hoje = datetime.datetime.now().strftime('%Y-%m-%d')
params = {'date': hoje}

r = requests.get(url, headers=headers, params=params)
fixtures = r.json().get('response', [])

print(f"Total fixtures for today ({hoje}): {len(fixtures)}")
for f in fixtures:
    league = f['league']['name']
    home = f['teams']['home']['name']
    away = f['teams']['away']['name']
    date = f['fixture']['date']
    if "Copa" in league or "Euro" in league or "World" in league:
        print(f"[{date}] {league}: {home} vs {away}")
