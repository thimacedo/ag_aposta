# run.py
import subprocess
import sys
import time
import signal
import os
import requests
import psutil  # pip install psutil

API_PORT = 8000
API_URL = f"http://localhost:{API_PORT}"
STARTUP_TIMEOUT = 15   # segundos esperando a API subir
HEALTH_INTERVAL = 5    # segundos entre verificações de saúde


def matar_porta(porta: int) -> None:
    """Mata qualquer processo ocupando a porta antes de subir."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for conn in proc.connections(kind="inet"):
                if conn.laddr.port == porta:
                    print(f"[cleanup] Matando processo {proc.pid} ({proc.name()}) na porta {porta}")
                    proc.kill()
                    proc.wait(timeout=3)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.AccessDenied):
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
    # 1. Limpa a porta antes de tentar subir
    matar_porta(API_PORT)

    processos = []

    def encerrar(sig=None, frame=None):
        print("\n[run] Encerrando todos os serviços...")
        for p in processos:
            try:
                # Mata a árvore inteira de processos filhos
                pai = psutil.Process(p.pid)
                for filho in pai.children(recursive=True):
                    filho.kill()
                pai.kill()
            except psutil.NoSuchProcess:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, encerrar)
    signal.signal(signal.SIGTERM, encerrar)

    # 2. Sobe a API
    print("[run] Iniciando API (uvicorn)...")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app",
         "--host", "127.0.0.1", "--port", str(API_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    processos.append(api)

    # 3. Verifica se a API realmente subiu antes de continuar
    if not aguardar_api():
        stderr = api.stderr.read().decode(errors="replace")
        print(f"[run] ERRO: API não respondeu. Saída:\n{stderr}")
        encerrar()

    # 4. Só sobe o frontend depois que a API estiver saudável
    print("[run] Iniciando painel Streamlit...")
    ui = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.headless", "true"]
    )
    processos.append(ui)
    print("[run] Sistema no ar. Ctrl+C para encerrar.\n")

    # 5. Loop de monitoramento — reinicia serviço morto
    while True:
        time.sleep(HEALTH_INTERVAL)

        if api.poll() is not None:
            print("[run] AVISO: API morreu. Reiniciando...")
            processos.remove(api)
            matar_porta(API_PORT)
            api = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app",
                 "--host", "127.0.0.1", "--port", str(API_PORT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processos.append(api)
            if not aguardar_api():
                print("[run] ERRO: API não voltou. Encerrando tudo.")
                encerrar()

        if ui.poll() is not None:
            print("[run] AVISO: Painel morreu. Reiniciando...")
            processos.remove(ui)
            ui = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", "app.py",
                 "--server.headless", "true"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processos.append(ui)


if __name__ == "__main__":
    run()
