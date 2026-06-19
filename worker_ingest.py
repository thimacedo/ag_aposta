"""
worker_ingest.py
Worker de ingestão — orquestra a coleta de dados da Copa 2026.

Fluxo (modo spider FIFA):
    1. Dispara o spider (Playwright) para capturar a página da FIFA
    2. Sincroniza a captura com o banco SQLite (fifa_sync)
    3. (Opcional) Chama o fetcher_agent original se disponível

Uso:
    python worker_ingest.py            # spider + sync uma vez
    python worker_ingest.py --daemon   # spider + sync a cada 3 horas
    python worker_ingest.py --legacy   # apenas fetcher_agent original
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker_ingest")

sys.path.insert(0, str(Path(__file__).resolve().parent))

INTERVALO_HORAS = 3


def rotina_ingestao_spider() -> dict:
    """Executa uma rotação completa de ingestão via spider FIFA."""
    logger.info("=== Iniciando rotação de ingestão (spider FIFA) ===")
    resultado = {"spider": None, "sync": None, "erro": None}
    t0 = time.time()

    try:
        import fifa_spider
        logger.info("→ Executando spider FIFA...")
        fifa_spider.run_job()
        resultado["spider"] = "OK"
        logger.info("✓ Spider concluído")
    except Exception as e:
        logger.error("Erro no spider: %s", e)
        logger.error(traceback.format_exc())
        resultado["spider"] = f"ERRO: {e}"
        resultado["erro"] = str(e)

    try:
        import fifa_sync
        logger.info("→ Sincronizando captura com o banco...")
        sync_result = fifa_sync.sincronizar()
        resultado["sync"] = sync_result
        logger.info("✓ Sincronização concluída")
    except Exception as e:
        logger.error("Erro na sincronização: %s", e)
        logger.error(traceback.format_exc())
        resultado["sync"] = f"ERRO: {e}"
        if not resultado["erro"]:
            resultado["erro"] = str(e)
            
    # Adicionando ingestão de odds aqui
    try:
        import fetcher_agent
        logger.info("→ Buscando odds...")
        odds = fetcher_agent.extrair_odds_the_odds_api()
        if odds:
            n = fetcher_agent.salvar_odds_banco(odds)
            resultado["odds"] = f"{n} salvas"
        else:
            resultado["odds"] = "Sem odds"
        logger.info(f"✓ Ingestão de odds concluída: {resultado['odds']}")
    except Exception as e:
        logger.error("Erro na ingestão de odds: %s", e)
        resultado["odds"] = f"ERRO: {e}"

    elapsed = time.time() - t0
    logger.info("=== Rotação concluída em %.1fs ===", elapsed)
    return resultado


def rotina_ingestao_legacy() -> None:
    """Chama o fetcher_agent original (compatibilidade)."""
    try:
        import fetcher_agent  # type: ignore
        logger.info("→ Chamando fetcher_agent.rotina_ingestao_diaria()...")
        fetcher_agent.rotina_ingestao_diaria()
        logger.info("✓ Ingestão legacy concluída")
    except ImportError:
        logger.error(
            "fetcher_agent não encontrado. Use 'python worker_ingest.py' "
            "(modo spider) em vez de --legacy."
        )
    except Exception as e:
        logger.error("Erro no fetcher_agent: %s", e)
        logger.error(traceback.format_exc())


def run_daemon(intervalo_horas: int = INTERVALO_HORAS) -> None:
    """Modo daemon: rota a cada N horas."""
    import signal

    def handle_sig(signum, frame):
        logger.info("Sinal %s recebido. Encerrando...", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    logger.info("🚀 Worker de ingestão iniciado em modo daemon "
                "(a cada %d horas)", intervalo_horas)

    rotina_ingestao_spider()

    try:
        import schedule
        schedule.every(intervalo_horas).hours.do(rotina_ingestao_spider)
        logger.info("Próxima execução em %d horas. Aguardando...", intervalo_horas)
        while True:
            schedule.run_pending()
            time.sleep(30)
    except ImportError:
        logger.warning("Biblioteca 'schedule' não encontrada. Usando loop com sleep.")
        while True:
            time.sleep(intervalo_horas * 3600)
            rotina_ingestao_spider()


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker de ingestão Copa 2026")
    parser.add_argument("--daemon", action="store_true",
                        help="Modo daemon: spider + sync a cada 3 horas")
    parser.add_argument("--once", action="store_true",
                        help="Executa uma única rotação e sai (default)")
    parser.add_argument("--legacy", action="store_true",
                        help="Apenas fetcher_agent original (sem spider)")
    parser.add_argument("--interval", type=int, default=INTERVALO_HORAS,
                        help=f"Intervalo em horas para o daemon (padrão: {INTERVALO_HORAS})")
    args = parser.parse_args()

    if args.legacy:
        rotina_ingestao_legacy()
    elif args.daemon:
        run_daemon(intervalo_horas=args.interval)
    else:
        rotina_ingestao_spider()


if __name__ == "__main__":
    main()
