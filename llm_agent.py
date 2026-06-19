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


def gerar_analise_partida(jogo: dict) -> str:
    """
    Gera análise contextual E estratégia de Hedge concreta para UMA partida,
    usando uma banca simulada de R$ 100 para manter os valores reais e baixos.
    """
    if not MISTRAL_API_KEY:
        return "⚠️ Chave MISTRAL_API_KEY não configurada no .env."
        
    mandante = jogo.get("nome_mandante", "Desconhecido")
    visitante = jogo.get("nome_visitante", "Desconhecido")
    odd_m = jogo.get("odd_mandante") or 0.0
    odd_e = jogo.get("odd_empate") or 0.0
    odd_v = jogo.get("odd_visitante") or 0.0
    fase = jogo.get("fase", "")
    grupo = jogo.get("grupo", "")
    estadio = jogo.get("estadio", "")
    cidade = jogo.get("cidade", "")
    num_casas = jogo.get("num_casas", 0)
    
    # --- MATEMÁTICA FINANCEIRA CONCRETA (BANCA R$ 100) ---
    banco_simulado = 100.0
    stake_pct = jogo.get("stake_kelly") or 0.03
    stake_real = banco_simulado * stake_pct
    
    mercados_ev = jogo.get("mercados_ev", "")
    melhor_ev = jogo.get("melhor_ev") or 0.0
    
    # Descobre qual odd o modelo sugeriu
    odd_sugerida = odd_m
    mercado_sugerido = "Mandante"
    if "VISITANTE" in mercados_ev:
        odd_sugerida = odd_v
        mercado_sugerido = "Visitante"
    elif "EMPATE" in mercados_ev:
        odd_sugerida = odd_e
        mercado_sugerido = "Empate"
        
    retorno_potencial = stake_real * odd_sugerida
    
    # Contexto do EV
    texto_ev = f"O modelo detectou EV+ de {melhor_ev*100:.1f}% apostando no {mercado_sugerido}." if melhor_ev > 0 else "Nenhuma oportunidade de valor detectada."

    # --- SIMULAÇÃO DE HEDGE (Odd contrária sobe 50% ao vivo) ---
    texto_simulacao = "Sem dados suficientes para simular hedge."
    if odd_sugerida > 1.0:
        # Simula a odd contrária subindo (cenário clássico de início de jogo favorito)
        if mercado_sugerido == "Mandante" and odd_v > 0:
            odd_hedge_simulada = round(odd_v * 1.5, 2)
            mercado_hedge = f"Visitante (Odd simulada ao vivo: {odd_hedge_simulada})"
        elif mercado_sugerido == "Visitante" and odd_m > 0:
            odd_hedge_simulada = round(odd_m * 1.5, 2)
            mercado_hedge = f"Mandante (Odd simulada ao vivo: {odd_hedge_simulada})"
        else:
            odd_hedge_simulada = round(max(odd_m, odd_v) * 1.5, 2)
            mercado_hedge = f"Resultado Oposto (Odd simulada ao vivo: {odd_hedge_simulada})"
            
        stake_hedge = retorno_potencial / odd_hedge_simulada
        lucro_garantido = retorno_potencial - (stake_real + stake_hedge)
        
        texto_simulacao = f"""
- Aposta Original: R$ {stake_real:.2f} no {mercado_sugerido} (Odd {odd_sugerida:.2f})
- Retorno Total Esperado: R$ {retorno_potencial:.2f}
- Cenário de Hedge: Se a odd do {mercado_hedge} disparar, você deve apostar R$ {stake_hedge:.2f} para travar um lucro garantido de R$ {lucro_garantido:.2f} (independente do resultado final).
"""

    prompt = f"""
    Você é um comentarista e analista de futebol de elite, com profundo conhecimento em mercados de apostas e modelagem estatística (Poisson, xG). 
    Sua tarefa é analisar a partida abaixo de forma técnica, realista e sem envolvimento emocional.

    DADOS DO JOGO:
    - Confronto: {mandante} x {visitante}
    - Fase/Grupo: {fase} {grupo}
    - Local: {estadio} ({cidade})
    - Odds Pré-Jogo ({num_casas} casas): Mandante: {odd_m} | Empate: {odd_e} | Visitante: {odd_v}
    - Status do Modelo Quantitativo: {texto_ev}

    SIMULAÇÃO MATEMÁTICA PRONTA (R$ 100 de Banca):
    {texto_simulacao}

    INSTRUÇÕES DE ESTRUTURA (Siga exatamente este formato):
    1. **Leitura de Mercado (Probabilidades Implícitas)**: Qual a % de chance de cada resultado segundo as casas?
    2. **O Plano de Ação (R$ 100)**: Com base nos números da simulação, diga se faz sentido entrar com R$ {stake_real:.2f}. Se o modelo sugeriu um EV absurdo (ex: 800%), critique duramente e diga que é um "Falso Positivo" do algoritmo.
    3. **Estratégia de Hedge Ao Vivo**: Use os dados de hedge acima. Explique em linguagem simples quando o usuário deve acionar a cobertura (ex: "Se o time favorito marcar aos 15 min, a odd do adversário vai subir, é hora de fazer a aposta de R$ X para travar o lucro").
    4. **Veredito Final**: Um resumo de 1 linha dizendo "APOSTAR" ou "NÃO APOSTAR" e o porquê.
    
    Use formatação Markdown. Seja direto, profissional e use no máximo 4-5 parágrafos curtos.
    """

    try:
        client = Mistral(api_key=MISTRAL_API_KEY)
        chat_response = client.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4 # Frio e objetivo
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
