# scripts/cleanup.py
"""
Mata todos os processos relacionados ao sistema (uvicorn + streamlit)
e libera a porta 8000. Rodar antes de `python run.py` em caso de travamento.
"""
import psutil
import sys

PORTA = 8000
NOMES_ALVO = {"uvicorn", "streamlit", "uvicorn.exe", "streamlit.exe"}

mortos = 0
for proc in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        nome = (proc.info["name"] or "").lower()
        cmdline = " ".join(proc.info["cmdline"] or []).lower()

        na_porta = False
        for conn in proc.connections(kind="inet"):
            if conn.laddr.port == PORTA:
                na_porta = True
                break
                
        e_alvo = nome in NOMES_ALVO or "uvicorn" in cmdline or "streamlit" in cmdline

        if na_porta or e_alvo:
            print(f"[cleanup] Matando PID {proc.pid} ({proc.name()})")
            proc.kill()
            proc.wait(timeout=3)
            mortos += 1
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

print(f"\n[cleanup] {mortos} processo(s) encerrado(s).")
print("[cleanup] Agora rode: python run.py")
