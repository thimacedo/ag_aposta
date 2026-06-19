import fetcher_agent
import logging

logging.basicConfig(level=logging.INFO)

print("Tentando buscar odds...")
odds = fetcher_agent.extrair_odds_the_odds_api()
if odds:
    print(f"Sucesso! Encontrados {len(odds)} eventos.")
else:
    print("Falha ao buscar odds.")
