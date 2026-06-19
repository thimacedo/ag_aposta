import requests, os, json
from dotenv import load_dotenv

# Carrega variáveis de ambiente — erro se .env não existir
if not os.path.exists(".env"):
    raise FileNotFoundError(
        "Arquivo .env não encontrado! Copie .env.example para .env e preencha as chaves."
    )
load_dotenv()
url = f"{os.getenv('THE_ODDS_API_BASE_URL')}/sports/soccer_fifa_world_cup/odds/"
params={'apiKey': os.getenv('THE_ODDS_API_KEY'), 'regions': 'eu', 'markets': 'h2h'}
resp = requests.get(url, params=params)
if resp.status_code == 200:
    data = resp.json()
    print(f"Got {len(data)} matches with odds.")
    if data:
        print(f"First match: {data[0]['home_team']} vs {data[0]['away_team']}")
else:
    print(f"Error: {resp.status_code} - {resp.text}")
