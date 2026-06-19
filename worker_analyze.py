"""
worker_analyze.py
Worker de análise — dispara o Agente de Risco (risk_agent).

Uso:
    python worker_analyze.py            # EV mínimo padrão (2%)
    python worker_analyze.py 0.05       # EV mínimo 5%
"""
import sys
import risk_agent
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker_analyze")

if __name__ == "__main__":
    ev_minimo = float(sys.argv[1]) if len(sys.argv) > 1 else 0.02
    logger.info(f"Iniciando análise com EV mínimo: {ev_minimo:.2%}")
    risk_agent.processar_partidas_pendentes(ev_minimo=ev_minimo)
    logger.info("Análise concluída.")
