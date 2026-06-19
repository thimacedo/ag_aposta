from playwright.sync_api import sync_playwright
import time

def verify_dashboard():
    print("Iniciando verificação do dashboard com Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        url = "http://localhost:8501"
        print(f"Navegando para {url}...")
        
        try:
            page.goto(url, timeout=30000)
            
            # Aguarda o carregamento inicial do Streamlit
            # Streamlit renderiza elementos dinamicamente, precisamos de um tempo de espera
            print("Aguardando carregamento dos componentes...")
            page.wait_for_timeout(5000)
            
            # Verifica se o título principal está presente
            if page.get_by_text("Futebol Quant-Agent").count() > 0 or page.get_by_text("Recomendações").count() > 0:
                print("SUCCESS: Dashboard está acessível.")
            else:
                print("WARNING: Título principal não encontrado, verifique a renderização.")

            # Verifica especificamente a aba de Recomendações
            # O Streamlit renderiza os headers de forma dinâmica
            if page.get_by_text("Recomendações com Valor Esperado Positivo").count() > 0:
                print("SUCCESS: Seção 'Recomendações EV+' encontrada.")
            else:
                print("FAILURE: Seção 'Recomendações EV+' não foi carregada.")

        except Exception as e:
            print(f"FAILURE: Erro ao carregar página: {e}")
            
        browser.close()

if __name__ == "__main__":
    verify_dashboard()
