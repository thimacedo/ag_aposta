# Football Quant-Agent — Instruções de Projeto

Este repositório é regido por regras inegociáveis de funcionamento. Toda operação deve respeitar estritamente o definido em `regras_inegociaveis.md`.

## Regras Operacionais
1. Respeito absoluto ao modelo matemático (`core_math.py`).
2. Transações de banco de dados devem ser atômicas e protegidas por rollback.
3. A separação de preocupações entre API, Workers e UI é obrigatória.
4. O sistema não executa apostas automaticamente (Regra R20).
5. Toda análise deve ser documentada com fontes de dados e métricas de confiança.
6. A modularidade é mandatória — novos componentes devem seguir a estrutura existente.

## Checklist de Operação Diária
Antes de iniciar qualquer análise:
- [ ] Validar ambiente (`.env` preenchido).
- [ ] Banco inicializado (`db.init_db()` idempotente).
- [ ] Testes de core (`pytest tests/test_core_math.py`) aprovados.
- [ ] Ingestão (`worker_ingest.py`) executada sem erros críticos.
- [ ] Verificação de quota de APIs confirmada.
- [ ] Execução de análise (`worker_analyze.py`) com logs de conferência de lambdas ativos.
