# run.py
import subprocess
import sys
import time
import signal
import os
import requests
import psutil

API_PORT = 8000
API_URL = f"http://localhost:{API_PORT}"
STARTUP_TIMEOUT = 15   # segundos esperando a API subir
HEALTH_INTERVAL = 5    # segundos entre verificações de saúde

# Caminho absoluto para o python do venv
VENV_PYTHON = os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe")

def matar_porta(porta: int) -> None:
    """Mata qualquer processo ocupando a porta antes de subir."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for conn in proc.connections(kind="inet"):
                if conn.laddr.port == porta:
                    print(f"[cleanup] Matando processo {proc.pid} ({proc.name()}) na porta {porta}")
                    proc.kill()
                    proc.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

def api_saudavel() -> bool:
    try:
        r = requests.get(f"{API_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

def aguardar_api(timeout: int = STARTUP_TIMEOUT) -> bool:
    print(f"[run] Aguardando API subir em {API_URL}...", end="", flush=True)
    for _ in range(timeout):
        if api_saudavel():
            print(" OK")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" FALHOU")
    return False

def run():
    if not os.path.exists(VENV_PYTHON):
        print(f"Erro: Interpretador Python não encontrado em {VENV_PYTHON}")
        sys.exit(1)

    matar_porta(API_PORT)

    processos = []

    def encerrar(sig=None, frame=None):
        print("\n[run] Encerrando todos os serviços...")
        for p in processos:
            try:
                pai = psutil.Process(p.pid)
                for filho in pai.children(recursive=True):
                    filho.kill()
                pai.kill()
            except psutil.NoSuchProcess:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, encerrar)
    signal.signal(signal.SIGTERM, encerrar)

    # Função para subir API
    def start_api():
        print("[run] Iniciando API (uvicorn)...")
        return subprocess.Popen(
            [VENV_PYTHON, "-m", "uvicorn", "api.main:app",
             "--host", "127.0.0.1", "--port", str(API_PORT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    # Função para subir UI
    def start_ui():
        print("[run] Iniciando painel Streamlit...")
        return subprocess.Popen(
            [VENV_PYTHON, "-m", "streamlit", "run", "app.py",
             "--server.headless", "true"]
        )

    api = start_api()
    processos.append(api)

    if not aguardar_api():
        stderr = api.stderr.read().decode(errors="replace")
        print(f"[run] ERRO: API não respondeu. Saída:\n{stderr}")
        encerrar()

    ui = start_ui()
    processos.append(ui)
    print("[run] Sistema no ar. Ctrl+C para encerrar.\n")

    while True:
        time.sleep(HEALTH_INTERVAL)

        if api.poll() is not None:
            stderr = api.stderr.read().decode(errors="replace")
            print(f"[run] AVISO: API morreu. Erro:\n{stderr}")
            print("[run] Reiniciando...")
            processos.remove(api)
            matar_porta(API_PORT)
            api = start_api()
            processos.append(api)
            if not aguardar_api():
                print("[run] ERRO: API não voltou. Encerrando tudo.")
                encerrar()

        if ui.poll() is not None:
            print("[run] AVISO: Painel morreu. Reiniciando...")
            processos.remove(ui)
            ui = start_ui()
            processos.append(ui)

if __name__ == "__main__":
    run()
