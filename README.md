# Football Quant Analyst - Copa 2026

*Ouça bem: este projeto é uma ferramenta de análise quantitativa construída para ser eficiente, não para ser um playground de bugs.*

O sistema foi refatorado de um monólito amador para uma **arquitetura de workers distribuídos**. Se você tentar enfiar lógica de negócio no lugar errado, eu vou saber.

## Arquitetura (Não mexa se não entender)

A arquitetura é baseada em eventos, desacoplada e robusta:

1.  **Orquestrador (`api/`)**: FastAPI. Aceita comandos via HTTP e despacha tarefas.
2.  **Workers (`worker_*.py`)**: Processos independentes que executam a lógica pesada (fetcher/analisador) em background.
3.  **UI (`app.py`)**: Streamlit cliente. Apenas consulta a API e exibe dados. Sem lógica de negócio aqui.
4.  **Banco (`data/quant_bet.db`)**: SQLite. A única fonte da verdade.

## Setup e Execução

Não perca tempo abrindo vários terminais. Eu fiz um script pra você:

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar variáveis (preencha suas chaves no .env)
cp .env.example .env

# 3. Rodar tudo de uma vez
python run.py
```

O `run.py` sobe a API e a UI em paralelo. Ctrl+C encerra tudo. Simples, rápido, eficiente.

## Componentes

| Arquivo | Função |
| :--- | :--- |
| `api/main.py` | Entrada da API. Recebe requisições e despacha para workers. |
| `worker_ingest.py` | Worker de ingestão (APIs externas). |
| `worker_analyze.py` | Worker de cálculo quantitativo (Risk Agent). |
| `app.py` | UI. Cliente da API. **Proibido** acessar banco diretamente. |
| `core_math.py` | Matemática pura. Poisson e Kelly. Não toque aqui se não souber estatística. |
| `db.py` | Acesso ao banco. A única porta de entrada pros dados. |

## Regras Inegociáveis
1. **Lógica na UI?** Nem pense. Se eu vir lógica de negócio em `app.py`, vou deletar.
2. **Workers são autônomos.** Eles não dependem da UI. Eles leem e escrevem no banco.
3. **Erros são registrados.** Tudo entra em log. Não me venha com "não sei por que parou". Olhe o log.

---
*Agora pare de perder tempo lendo README e vá botar esse sistema pra rodar.*
