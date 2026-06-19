import os
import requests
from dotenv import load_dotenv

# Carrega variáveis de ambiente — erro se .env não existir
if not os.path.exists(".env"):
    raise FileNotFoundError(
        "Arquivo .env não encontrado! Copie .env.example para .env e preencha as chaves."
    )
load_dotenv(dotenv_path=".env")

def test_api_football():
    key = os.getenv("API_FOOTBALL_KEY")
    url = f"{os.getenv('API_FOOTBALL_BASE_URL')}/status"
    headers = {'x-apisports-key': key}
    
    print(f"--- Testando API-Football ---")
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            account = data.get('response', {}).get('account', {})
            print(f"Sucesso! Status: {response.status_code}")
            print(f"Conta: {account.get('firstname')} {account.get('lastname')}")
            print(f"Limite Diário: {data.get('response', {}).get('subscription', {}).get('plan')}")
        else:
            print(f"Falha! Código: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"Erro na requisição: {e}")

def test_the_odds_api():
    key = os.getenv("THE_ODDS_API_KEY")
    url = f"{os.getenv('THE_ODDS_API_BASE_URL')}/sports"
    params = {'apiKey': key}
    
    print(f"\n--- Testando The Odds API ---")
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            sports = response.json()
            print(f"Sucesso! Status: {response.status_code}")
            print(f"Total de esportes ativos: {len(sports)}")
            # Mostra os 3 primeiros
            for s in sports[:3]:
                print(f"- {s.get('title')}: {s.get('key')}")
            
            # Verifica limites via headers
            remaining = response.headers.get('x-requests-remaining')
            used = response.headers.get('x-requests-used')
            print(f"Requests Restantes: {remaining} / Usadas: {used}")
        else:
            print(f"Falha! Código: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"Erro na requisição: {e}")

if __name__ == "__main__":
    test_api_football()
    test_the_odds_api()
