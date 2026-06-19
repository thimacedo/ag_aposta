# Regras Inegociáveis — Futebol Quant-Agent

> Este documento define os contratos de funcionamento do sistema. Cada regra tem uma razão matemática, operacional ou de segurança. Nenhuma delas é opcional.

---

## I. Modelo Matemático

---

**R01 — O modelo nunca opera sem dados suficientes**

Nenhuma análise de EV é gerada para times com zero jogos registrados no banco. Times sem histórico recebem automaticamente os valores de referência da liga (`MEDIA_LIGA_COPA_REFERENCIA = 1.25`) e são sinalizados com `fonte_metrica = 'referencia'`. O painel deve exibir esse aviso explicitamente ao usuário.

> Razão: Um lambda calculado sobre zero jogos é a média da liga pura disfarçada de dado real. Apresentá-lo sem aviso cria uma falsa sensação de precisão.

---

**R02 — Kelly Fracionado é o teto, não o piso**

O stake sugerido pelo modelo (`stake_kelly`) é um **máximo absoluto por aposta**. O valor nunca é arredondado para cima. O cap de `KELLY_STAKE_MAX = 0.03` (3% da banca) é inviolável — nenhum EV, por maior que seja, justifica excedê-lo.

> Razão: Kelly puro é matematicamente ótimo no longo prazo mas produz ruína no curto prazo com amostras pequenas. A Copa tem no máximo 7 jogos por time — isso é amostra curta por definição.

---

**R03 — EV mínimo de 2% é o piso de entrada, não uma sugestão**

Oportunidades com `ev_calculado < 0.02` (2%) não são registradas em `historico_analises` e não aparecem no painel. Esse threshold existe porque o erro do modelo Poisson com poucos jogos de Copa pode superar edges menores que esse valor.

> Razão: Abaixo de 2% de EV, não é possível distinguir edge real de ruído do modelo. Agir nessa faixa é equivalente a apostar sem modelo.

---

**R04 — Poisson independente nunca é usado sem o fator Dixon-Coles**

A função `calcular_probabilidades_partida()` sempre aplica o fator de correção `rho` nos placares (0-0), (1-0), (0-1) e (1-1). O valor padrão `DIXON_COLES_RHO = -0.10` não é removido nem zerado sem uma justificativa documentada e um backteste que suporte a mudança.

> Razão: O Poisson independente subestima sistematicamente a probabilidade de empate em jogos de baixo scoring — exatamente o perfil da Copa do Mundo. Ignorar isso produz EV positivo artificial no mercado de empate.

---

**R05 — Gols reais e xG nunca são tratados como equivalentes sem rastreamento**

Todo dado salvo em `xg_marcado_*` / `xg_sofrido_*` deve ter o campo `fonte_metrica` preenchido: `'xg'` para Expected Goals reais ou `'gols'` para gols realizados usados como proxy. Partidas onde pelo menos um time tem `fonte_metrica = 'gols'` devem emitir `logger.warning` antes da análise.

> Razão: xG e gols realizados medem coisas diferentes. Misturá-los silenciosamente distorce os lambdas e, por consequência, todo o cálculo de probabilidade e EV.

---

**R06 — Suavização de lambda é sempre aplicada — sem exceção**

O peso de confiança `_peso_confianca(n_jogos)` é aplicado a todos os times, independentemente do número de jogos. Mesmo um time com 7 jogos (máximo da Copa) recebe `peso = 1.0`, o que mantém seu lambda puro — mas a função é sempre chamada. Não há bypass condicional.

> Razão: Um threshold binário (aplica/não aplica) cria descontinuidades no modelo que produzem comportamento errático próximo ao limite. Peso contínuo é matematicamente correto.

---

## II. Dados e Banco

---

**R07 — Todo timestamp no banco é UTC**

Nenhum módulo salva datas em horário local. Toda criação de `datetime` usa `timezone.utc` ou `datetime('now')` do SQLite (que é UTC por padrão). Queries de filtro temporal usam exclusivamente `datetime('now', ...)`.

> Razão: O sistema roda em Natal (UTC-3) mas as APIs retornam timestamps em UTC. Misturar fusos sem controle faz partidas serem analisadas fora da janela correta ou ignoradas silenciosamente.

---

**R08 — Toda escrita no banco usa transação explícita com rollback**

Qualquer função que execute múltiplos `INSERT` ou `UPDATE` em sequência usa `conn.execute("BEGIN")` / `conn.commit()` / `conn.rollback()` explicitamente. O autocommit do SQLite não é suficiente para operações compostas.

> Razão: Uma falha parcial (processo morto, disco cheio, sinal do OS) sem rollback deixa o banco em estado inconsistente. Em um sistema de histórico de análises financeiras, inconsistência é inaceitável.

---

**R09 — `historico_analises` nunca recebe duplicatas**

A constraint `UNIQUE(partida_id, mercado_sugerido)` é definida no schema e toda inserção usa `INSERT OR IGNORE`. Rodadas repetidas de análise para a mesma partida não criam novas linhas PENDENTE.

> Razão: Duplicatas inflam o backteste, distorcem o cálculo de ROI acumulado e podem levar o usuário a pensar que há mais oportunidades do que realmente existem.

---

**R10 — Odds são sempre validadas antes de qualquer cálculo**

Toda odd passada para `calcular_overround()`, `remover_margem()` ou `calcular_kelly_fracionado()` deve ser `> 1.0`. Odds inválidas levantam `ValueError` — nunca são substituídas por zero ou ignoradas silenciosamente.

> Razão: Uma odd de 1.0 ou menor é matematicamente impossível em um mercado real (implicaria probabilidade ≥ 100%). Aceitar esse valor sem erro propaga cálculos sem sentido pelo pipeline inteiro.

---

**R11 — O mapeamento time→ID tem fonte única de verdade**

Todos os módulos importam IDs e nomes de times exclusivamente de `teams.py`. Nenhum módulo define seu próprio dicionário de mapeamento local. Qualquer adição ou correção de time é feita em `teams.py` e reflete automaticamente em todo o sistema.

> Razão: Três dicionários divergentes produzem IDs diferentes para o mesmo time dependendo da fonte de dados, corrompendo JOINs e fazendo odds serem salvas na partida errada.

---

**R12 — O banco nunca é apagado ou recriado manualmente durante a Copa**

`db.init_db()` é idempotente e pode ser chamado a qualquer momento — ele só cria o que não existe. O arquivo `data/quant_bet.db` não é deletado manualmente durante a operação. Se houver necessidade de reset, um script específico deve ser criado com confirmação explícita.

> Razão: O banco contém o histórico de recomendações (`historico_analises`) necessário para backteste e avaliação de performance do modelo. Apagá-lo destrói a única fonte de verdade sobre o que o sistema recomendou.

---

## III. APIs e Ingestão

---

**R13 — Quota de API é gerenciada pelo sistema, não pelo operador**

Nenhuma chamada de API é feita fora das funções do `fetcher_agent.py`. Chamadas manuais via `check_today.py` ou scripts avulsos durante a Copa contam contra o limite diário mas não são registradas em `api_usage.jsonl`. Se precisar testar uma API manualmente, use o modo de cache (`_ler_cache`) ou mocke a resposta.

> Razão: The Odds API tem 500 req/mês. Um erro de loop ou uma chamada manual esquecida pode consumir dias de quota em minutos.

---

**R14 — O cache de arquivo é respeitado integralmente**

Nenhuma função bypassa a verificação de cache (`_ler_cache`) para "garantir dados frescos". Se o cache tem menos de N horas, ele é usado. A única exceção permitida é reiniciar o processo — o que invalida o cache em memória, não o cache em arquivo.

> Razão: O cache existe para respeitar os limites de rate das APIs gratuitas. Bypassá-lo por conveniência é equivalente a queimar quota deliberadamente.

---

**R15 — A cascata de fontes é executada na ordem definida**

A ordem `football-data.org → openfootball → API-Football` para partidas e `The Odds API → fallback` para odds é fixa. Fontes não são reordenadas sem atualização do documento de arquitetura. Se uma fonte falhar, o log deve registrar explicitamente qual fonte foi usada e por que a anterior falhou.

> Razão: Cada fonte tem custo, qualidade e confiabilidade diferentes. Reordenar sem análise pode queimar quota de uma fonte premium quando uma gratuita seria suficiente.

---

**R16 — Match de times entre fontes externas e o banco é sempre feito por ID, nunca por nome**

Nomes de times variam entre fontes: "Brazil" vs "Brasil", "USA" vs "United States". Todo match de partida ou time contra o banco usa `time_id` (inteiro) como chave. Busca por `LIKE` em nome só é permitida como último recurso, e deve emitir `logger.warning` quando usada.

> Razão: Match por nome parcial é ambíguo e silencioso. Um LIKE errado salva odds na partida errada sem nenhum erro visível — o pior tipo de bug em um sistema de análise financeira.

---

## IV. Configuração e Ambiente

---

**R17 — Variáveis de ambiente nunca têm valor padrão funcional no código**

`os.getenv("API_KEY", "")` é o padrão correto — string vazia como fallback. É proibido colocar uma chave real, mesmo de desenvolvimento, como valor padrão em qualquer arquivo versionado. Chaves ausentes devem ser detectadas pelas funções que as usam e resultar em `logger.warning` + retorno `None`, nunca em `AttributeError` ou `KeyError`.

> Razão: Chaves em código-fonte vazam via histórico do git mesmo após remoção. String vazia como fallback garante falha limpa e detectável.

---

**R18 — `.env` nunca é versionado**

`.env` está em `.gitignore` e permanece assim. `.env.example` é o único arquivo de referência de configuração versionado, com todas as variáveis documentadas e sem nenhum valor real.

> Razão: Um `.env` commitado expõe todas as chaves de API ao histórico do repositório permanentemente — incluindo após tentativas de remoção com `git rm`.

---

**R19 — O módulo `config.py` é o único ponto de carga do `.env`**

`load_dotenv()` é chamado exclusivamente dentro de `config.py`. Nenhum outro módulo chama `load_dotenv()` diretamente. A verificação de existência do `.env` também fica em `config.py`, e é executada apenas quando uma variável é efetivamente solicitada — nunca no escopo global do módulo.

> Razão: `load_dotenv()` no escopo global de um módulo impede que qualquer `import` desse módulo funcione em ambientes de teste ou CI sem um `.env` presente.

---

## V. Segurança Operacional

---

**R20 — O sistema não executa, simula nem facilita apostas automaticamente**

Nenhuma biblioteca de automação web (Selenium, Playwright, PyAutoGUI ou equivalente) é adicionada às dependências. O painel exibe recomendações; toda ação de entrada em uma casa de apostas é manual e de responsabilidade exclusiva do usuário. Esta regra não tem exceções e não é negociável sob nenhum pretexto de "conveniência" ou "automação de fluxo".

> Razão: Além do risco financeiro de automação sem supervisão, casas de apostas proíbem automação nos termos de serviço. Violação pode resultar em banimento de conta e perda de fundos.

---

**R21 — O modelo não é recalibrado com menos de 20 análises concluídas no sistema**

Qualquer ajuste em `MEDIA_LIGA_COPA_REFERENCIA`, `DIXON_COLES_RHO`, `KELLY_FRACAO_PADRAO` ou `EV_MINIMO_PADRAO` baseado em resultados observados exige pelo menos 20 linhas em `historico_analises` com `resultado_partida != 'PENDENTE'` — contando o total do sistema, não por time. O threshold por time é inaplicável na Copa: cada seleção disputa no máximo 7 jogos. A unidade de amostra aqui é a **recomendação resolvida**, não a partida por time. Antes de atingir esse volume, os parâmetros são congelados nos valores iniciais.

> Razão: A Copa tem 104 jogos mas cada time joga no máximo 7 — nunca haverá 10 partidas concluídas por seleção. O threshold precisa ser sobre o total de apostas recomendadas e resolvidas pelo sistema. Com menos de 20 amostras resolvidas, a variância dos resultados ainda supera qualquer sinal real de calibração.

---

**R22 — Toda recomendação exibida ao usuário indica explicitamente sua incerteza**

O painel nunca exibe um EV ou probabilidade sem contexto. Junto a cada recomendação devem aparecer: número de jogos do time no banco, se a fonte é xG ou gols reais, e o lambda calculado. Esses dados são o que permite ao usuário avaliar se confia ou não no modelo para aquela partida específica.

> Razão: Um EV de 5% baseado em 1 jogo e gols reais é fundamentalmente diferente de um EV de 5% baseado em 6 jogos e xG real. Apresentá-los da mesma forma é desonesto com o usuário.

---

## VI. Interface e Comunicação

---

**R27 — Toda comunicação com o usuário é em português do Brasil**

Todos os textos visíveis no painel Streamlit — títulos, botões, mensagens de erro, avisos, tooltips, labels de tabela e notificações — são escritos em português do Brasil. Logs internos de desenvolvimento (`logger.info`, `logger.debug`, `logger.error`) podem permanecer em inglês técnico, pois são destinados ao operador do sistema. Qualquer string que o usuário final possa ler é PT-BR sem exceção.

> Razão: O sistema é operado por falantes de português. Misturar idiomas na interface cria fricção desnecessária e pode levar o usuário a ignorar avisos importantes por não compreendê-los completamente.

---

**R28 — Explicações para o usuário usam linguagem leiga, sem jargão técnico ou estatístico sem tradução**

Toda mensagem exibida no painel que contenha termos como EV, Kelly, lambda, Poisson, xG, overround ou Dixon-Coles deve vir acompanhada de uma explicação em linguagem simples. A explicação não substitui o termo técnico — ela aparece logo abaixo ou em um tooltip, para que usuários avançados e leigos possam usar o mesmo painel.

Exemplos de tradução obrigatória:

| Termo técnico | Como exibir para o usuário leigo |
|---|---|
| EV (Expected Value) | "Vantagem esperada — quanto este mercado vale além do risco" |
| Stake Kelly | "Sugestão de quanto apostar (% da sua banca)" |
| Lambda | "Gols esperados pelo modelo para este time" |
| xG (Expected Goals) | "Chances reais de gol criadas, independente de sorte na finalização" |
| Overround | "Margem embutida pela casa de apostas neste mercado" |
| Devigging | "Remoção da margem da casa para ver a probabilidade real" |
| Poisson | "Modelo matemático usado para estimar a probabilidade de cada placar" |
| Dixon-Coles | "Correção aplicada ao modelo para placares baixos, onde o Poisson erra mais" |
| Campo neutro | "Jogo sem mandante real — nenhum time tem vantagem de torcida ou gramado" |

> Razão: Um usuário que não entende o que o sistema está dizendo tende a ignorar avisos críticos ou a interpretar recomendações de forma errada. Clareza na comunicação é parte da segurança operacional do sistema.

---

## VII. Código e Manutenção

---

**R29 — `core_math.py` é puro — sem I/O, sem imports de outros módulos do projeto**

`core_math.py` importa apenas `numpy` e `scipy`. Nenhum import de `db`, `fetcher_agent`, `risk_agent` ou qualquer outro módulo interno. Toda lógica que precise de banco de dados ou de configuração pertence ao módulo que orquestra — nunca ao módulo de cálculo.

> Razão: Pureza matemática garante que `core_math` pode ser testado de forma completamente isolada, sem banco, sem `.env`, sem rede. Qualquer dependência externa quebra essa garantia.

---

**R30 — Toda função pública tem docstring com tipos de entrada e saída documentados**

Funções sem docstring não são mergeadas. A docstring mínima contém: o que a função faz, os parâmetros com tipo e semântica, e o que retorna. Funções matemáticas incluem a fórmula usada.

> Razão: Um sistema de análise quantitativa cujas funções não documentam suas premissas matemáticas é impossível de auditar. Auditabilidade é um requisito — não um capricho.

---

**R31 — Erros nunca são silenciados com `except: pass`**

Todo bloco `except` no mínimo executa `logger.debug(...)` com a exceção capturada. Blocos que capturam `Exception` genérica em laços de processamento (como os loops de `salvar_partidas_banco`) registram o erro e continuam — mas nunca descartam a informação silenciosamente.

> Razão: `except: pass` transforma bugs em comportamento misterioso. Em um sistema de pipeline com múltiplas fontes e fallbacks, saber *o que* falhou e *onde* é a única forma de depurar.

---

**R32 — Testes unitários de `core_math.py` passam antes de qualquer deploy**

O comando `pytest tests/test_core_math.py -v` deve retornar zero falhas antes de qualquer alteração em `core_math.py` ser colocada em produção. Se um teste falhar após uma mudança, a mudança não vai para produção até o teste ser corrigido ou o teste ser explicitamente marcado como desatualizado com justificativa documentada.

> Razão: `core_math.py` é a fundação de toda a análise. Uma regressão silenciosa nesse módulo propaga resultados incorretos por todo o pipeline sem nenhum erro visível.

---

## Checklist de operação diária

Use este checklist antes de iniciar o sistema em cada dia de Copa:

- [ ] `.env` existe e todas as 6 variáveis estão preenchidas
- [ ] `python db.py` executa sem erros (banco inicializado)
- [ ] `pytest tests/test_core_math.py -v` — zero falhas
- [ ] `python fetcher_agent.py` — ingestão de partidas e odds sem erro crítico
- [ ] Status de quota dentro dos limites (`status_quota()`)
- [ ] Painel Streamlit carrega sem exceção na aba de recomendações
- [ ] Pelo menos uma partida com odds aparece disponível para análise
- [ ] `python risk_agent.py` — log confirma lambdas diferentes para os dois times de cada partida

---

## Referência rápida de parâmetros do modelo

| Parâmetro | Valor padrão | Pode alterar? | Condição |
|---|---|---|---|
| `MEDIA_LIGA_COPA_REFERENCIA` | 1.25 | Sim | ≥ 20 análises concluídas |
| `DIXON_COLES_RHO` | -0.10 | Sim | ≥ 20 análises + backteste documentado |
| `KELLY_FRACAO_PADRAO` | 0.25 | Sim | Decisão consciente de risco |
| `KELLY_STAKE_MAX` | 0.03 | **Não** | Teto absoluto de proteção de banca |
| `EV_MINIMO_PADRAO` | 0.02 | Sim | Nunca abaixo de 0.01 |
| `MIN_GAMES_FOR_RELIABLE_STATS` | 3 | Sim | Referência para suavização |
| `MAX_JOGOS_COPA` | 7 | Não | Máximo físico da competição |
| `HOME_ADVANTAGE_COPA` | 0.0 | **Não** | Campo neutro — premissa estrutural |
| `DEF_STRENGTH_CAP` | 2.5 | Sim | Com base empírica documentada |
