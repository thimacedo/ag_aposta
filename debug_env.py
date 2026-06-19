import os
from dotenv import load_dotenv

load_dotenv()
print(f"THE_ODDS_API_KEY: {os.getenv('THE_ODDS_API_KEY')}")
print(f"API_FOOTBALL_BASE_URL: {os.getenv('API_FOOTBALL_BASE_URL')}")
