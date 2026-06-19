import subprocess
import os
import sys
import time
import signal
import requests

def run():
    print("Iniciando sistema Football Quant (API + UI)...")
    
    # Caminho absoluto para o python do venv
    venv_python = os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe")
    
    if not os.path.exists(venv_python):
        print(f"Erro: Interpretador Python não encontrado em {venv_python}")
        sys.exit(1)
        
    # Inicia a API
    api = subprocess.Popen([venv_python, "-m", "uvicorn", "api.main:app", "--port", "8000"])
    
    # Espera API subir via health check
    print("Aguardando API subir...")
    for _ in range(10):
        try:
            if requests.get("http://127.0.0.1:8000/health").status_code == 200:
                print("API iniciada com sucesso.")
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    else:
        print("Erro: API não iniciou em 10 segundos.")
        api.terminate()
        sys.exit(1)
    
    # Inicia a UI
    ui = subprocess.Popen([venv_python, "-m", "streamlit", "run", "app.py"])
    
    try:
        # Mantém vivo
        api.wait()
        ui.wait()
    except KeyboardInterrupt:
        print("\nEncerrando serviços...")
        api.terminate()
        ui.terminate()

if __name__ == "__main__":
    run()
