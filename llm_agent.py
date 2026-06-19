"""
llm_agent.py
Agente 4 — Especialista Qualitativo (Mistral AI).

Este módulo atua como a "Inteligência Principal" do sistema para a camada qualitativa.
Ele recebe as oportunidades matemáticas filtradas pelo Agente de Risco e utiliza 
a API da Mistral para redigir uma análise humana, contextualizada e especializada,
explicando por que a aposta faz sentido.
"""

from __future__ import annotations

import os
import logging
from mistralai.client import Mistral
from dotenv import load_dotenv

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carrega as chaves — erro se .env não existir
if not os.path.exists(".env"):
    raise FileNotFoundError(
        "Arquivo .env não encontrado! Copie .env.example para .env e preencha as chaves."
    )
load_dotenv()

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

def gerar_analise_especialista(recomendacoes: list[dict]) -> str:
    """
    Consome as recomendações do modelo Poisson/Kelly e envia para a Mistral
    gerar um resumo argumentativo para o usuário.
    """
    if not MISTRAL_API_KEY:
        return "⚠️ Chave MISTRAL_API_KEY não configurada. A análise qualitativa está desativada."
        
    if not recomendacoes:
        return "Nenhuma oportunidade EV+ identificada matematicamente para análise."

    # Prepara os dados para o LLM
    dados_prompt = []
    for r in recomendacoes:
        dados_prompt.append(
            f"- Jogo: {r.get('mandante', 'Casa')} vs {r.get('visitante', 'Fora')} "
            f"| Aposta: {r.get('mercado_sugerido')} "
            f"| Odd: {r.get('odd_disponivel')} "
            f"| EV: {r.get('ev_calculado', 0)*100:.2f}% "
            f"| Stake: {r.get('stake_kelly', 0)*100:.2f}%"
        )
        
    contexto_str = "\n".join(dados_prompt)
    
    prompt = f"""
    Você é um analista quantitativo de futebol de elite, focado na Copa do Mundo da FIFA.
    O nosso modelo matemático rigoroso (baseado em Poisson e Kelly) identificou as seguintes apostas
    com Valor Esperado Positivo (EV+) para os jogos atuais:
    
    {contexto_str}
    
    Por favor, escreva um breve parágrafo (máximo de 4-5 frases) resumindo essa estratégia para o investidor. 
    Seja analítico, frio, focado no longo prazo e mencione que a matemática indica valor nessas cotações.
    Não recomende ir 'all-in', reforce a gestão de banca e a disciplina quantitativa.
    Aja como o 'Agente Principal' do sistema.
    """

    try:
        client = Mistral(api_key=MISTRAL_API_KEY)
        
        chat_response = client.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": prompt}]
        )
        
        return chat_response.choices[0].message.content
        
    except Exception as e:
        return f"❌ Erro ao consultar a IA Mistral: {e}"

if __name__ == "__main__":
    # Teste rápido simulando uma recomendação
    mock_recs = [{
        "mandante": "Brasil", "visitante": "Espanha", 
        "mercado_sugerido": "MANDANTE", 
        "odd_disponivel": 2.10, "ev_calculado": 0.05, "stake_kelly": 0.02
    }]
    print(gerar_analise_especialista(mock_recs))
