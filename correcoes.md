# Correções — Futebol Quant-Agent

> Documento gerado a partir da análise estática do projeto. Organizado por prioridade: 🔴 crítico, 🟡 importante, 🟢 backlog.

---

## 🔴 CRÍTICO — Corrigir antes de usar em produção

---

### 1. Bug: `calcular_lambdas_copa()` recebe o mesmo `row` para mandante e visitante

**Arquivo:** `risk_agent.py`

**Problema:** A função é chamada com `row` duplicado. Como `row` é uma linha do JOIN entre `times_performance` (mandante e visitante), ela contém colunas ambíguas — e `calcular_lambdas_copa()` lê `xg_marcado_casa` do `row_mandante` e `xg_marcado_fora` do `row_visitante`, mas como ambos são o mesmo objeto, os lambdas de ataque e defesa dos dois times ficam idênticos. O modelo passa a tratar todos os jogos como disputas entre times equivalentes, zerando qualquer edge calculado.

**Código atual:**
```python
# risk_agent.py — processar_partidas_pendentes()
lambda_a, lambda_b = calcular_lambdas_copa(row, row, media_liga)
```

**Correção — query SQL com aliases separados:**
```sql
-- Substituir a query SQL em processar_partidas_pendentes()
SELECT
    p.partida_id,
    p.data_evento,
    tm.nome           AS nome_mandante,
    tv.nome           AS nome_visitante,
    tm.time_id        AS id_mandante,
    tv.time_id        AS id_visitante,
    tm.xg_marcado_casa AS tm_xg_marcado_casa,
    tm.xg_sofrido_casa AS tm_xg_sofrido_casa,
    tv.xg_marcado_fora AS tv_xg_marcado_fora,
    tv.xg_sofrido_fora AS tv_xg_sofrido_fora,
    tm.jogos_casa + tm.jogos_fora AS jogos_mandante,
    tv.jogos_casa + tv.jogos_fora AS jogos_visitante,
    MAX(o.odd_mandante)  AS odd_mandante,
    MAX(o.odd_empate)    AS odd_empate,
    MAX(o.odd_visitante) AS odd_visitante
FROM partidas_agenda p
JOIN times_performance tm ON p.time_mandante_id = tm.time_id
JOIN times_performance tv ON p.time_visitante_id = tv.time_id
JOIN odds_mercado o ON p.partida_id = o.partida_id
WHERE
    p.data_evento >= datetime('now', '-2 hours')
    AND NOT EXISTS (
        SELECT 1 FROM historico_analises h
        WHERE h.partida_id = p.partida_id
          AND h.resultado_partida = 'PENDENTE'
    )
GROUP BY p.partida_id
ORDER BY p.data_evento ASC
```

**Correção — separar os rows antes de passar para a função:**
```python
# risk_agent.py — dentro do loop `for row in rows:`

# Monta dicts separados para mandante e visitante
row_mandante = {
    "xg_marcado_casa": row["tm_xg_marcado_casa"],
    "xg_sofrido_casa": row["tm_xg_sofrido_casa"],
}
row_visitante = {
    "xg_marcado_fora": row["tv_xg_marcado_fora"],
    "xg_sofrido_fora": row["tv_xg_sofrido_fora"],
}

lambda_a, lambda_b = calcular_lambdas_copa(row_mandante, row_visitante, media_liga)
```

**Correção — adaptar `calcular_lambdas_copa()` para aceitar dicts simples:**
```python
# risk_agent.py
def calcular_lambdas_copa(
    row_mandante: dict,
    row_visitante: dict,
    media_liga: float = MEDIA_LIGA_COPA,
) -> tuple[float, float]:
    def _val(row: dict, col: str, fallback: float = MEDIA_LIGA_COPA) -> float:
        v = row.get(col)
        return float(v) if v and float(v) > 0 else fallback

    xg_m_a = _val(row_mandante, "xg_marcado_casa")
    xg_s_a = _val(row_mandante, "xg_sofrido_casa")
    xg_m_b = _val(row_visitante, "xg_marcado_fora")
    xg_s_b = _val(row_visitante, "xg_sofrido_fora")

    atk_a = core_math.calcular_forca_ataque(xg_m_a, media_liga)
    def_b = core_math.calcular_forca_defesa(xg_s_b, media_liga)
    atk_b = core_math.calcular_forca_ataque(xg_m_b, media_liga)
    def_a = core_math.calcular_forca_defesa(xg_s_a, media_liga)

    lambda_a = core_math.calcular_lambda_esperado(
        atk_a, def_b, media_liga, is_mandante=False, tournament="copa"
    )
    lambda_b = core_math.calcular_lambda_esperado(
        atk_b, def_a, media_liga, is_mandante=False, tournament="copa"
    )
    return lambda_a, lambda_b
```

> ⚠️ Aplicar a mesma separação em `diagnostico_partida()`, onde o mesmo bug existe.

---

### 2. Bug: `api/main.py` não existe — `run.py` falha na inicialização

**Arquivo:** `run.py`

**Problema:** O processo da API é iniciado com `uvicorn api.main:app`, mas o módulo `api/main.py` não existe no projeto. `run.py` falha imediatamente. `app.py` depende desse servidor para funcionar (`requests.post(f"{API_URL}/analyze")`).

**Código atual:**
```python
api = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", "8000"])
```

**Correção — criar `api/main.py` com FastAPI:**
```python
# api/__init__.py  (arquivo vazio)

# api/main.py
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import risk_agent
import fetcher_agent
import db

app = FastAPI(title="Futebol Quant-Agent API")


class AnalyzeRequest(BaseModel):
    ev_minimo: float = 0.02


class IngestRequest(BaseModel):
    fonte: str = "all"


@app.on_event("startup")
def startup():
    db.init_db()


@app.post("/analyze")
def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """Dispara análise de EV+ em background para não bloquear a resposta."""
    background_tasks.add_task(
        risk_agent.processar_partidas_pendentes, ev_minimo=req.ev_minimo
    )
    return {"status": "iniciado", "ev_minimo": req.ev_minimo}


@app.post("/ingest")
def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    """Dispara ingestão diária em background."""
    background_tasks.add_task(fetcher_agent.rotina_ingestao_diaria)
    return {"status": "iniciado", "fonte": req.fonte}


@app.get("/status")
def status():
    return fetcher_agent.status_quota()


@app.get("/recomendacoes")
def recomendacoes():
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM historico_analises WHERE resultado_partida = 'PENDENTE'"
        ).fetchall()
    return [dict(r) for r in rows]
```

---

### 3. Bug: Match fuzzy de odds por 6 caracteres causa partidas trocadas

**Arquivo:** `fetcher_agent.py` — `salvar_odds_banco()`

**Problema:** O nome do time é truncado em 6 caracteres para o `LIKE`. "United States" e "Uruguay" têm prefixos distintos, mas "Saudi Arabia" → `"Saudi "` pode conflitar com variantes. O maior risco real é quando a The Odds API retorna nomes em inglês que não batem exatamente com os nomes em PT salvos no banco — o LIKE com 6 chars vai ou não achar nada, ou achar o time errado silenciosamente.

**Código atual:**
```python
cur = conn.execute("""
    SELECT p.partida_id
    FROM partidas_agenda p
    JOIN times_performance tm ON p.time_mandante_id = tm.time_id
    JOIN times_performance tv ON p.time_visitante_id = tv.time_id
    WHERE tm.nome LIKE ? AND tv.nome LIKE ?
    LIMIT 1
""", (f"%{home_team[:6]}%", f"%{away_team[:6]}%"))
```

**Correção — adicionar mapeamento EN→PT e buscar por ID diretamente:**
```python
# fetcher_agent.py — adicionar constante de mapeamento
# (reutiliza OPENFOOTBALL_NAME_MAP que já existe no módulo)

def _resolver_team_id(nome_en: str) -> int:
    """Resolve nome em inglês (vindo da Odds API) para ID interno."""
    return OPENFOOTBALL_NAME_MAP.get(nome_en, 0)


def salvar_odds_banco(odds_data: list[dict]) -> int:
    salvas = 0
    sem_match = 0
    erros = 0

    with db.get_connection() as conn:
        for event in odds_data:
            try:
                home_team = event.get("home_team", "")
                away_team = event.get("away_team", "")

                home_id = _resolver_team_id(home_team)
                away_id = _resolver_team_id(away_team)

                if not home_id or not away_id:
                    sem_match += 1
                    logger.debug(
                        f"IDs não encontrados: '{home_team}' (id={home_id}) "
                        f"vs '{away_team}' (id={away_id})"
                    )
                    continue

                # Busca por ID — sem ambiguidade
                cur = conn.execute("""
                    SELECT p.partida_id
                    FROM partidas_agenda p
                    WHERE p.time_mandante_id = ? AND p.time_visitante_id = ?
                    LIMIT 1
                """, (home_id, away_id))
                row = cur.fetchone()

                if not row:
                    sem_match += 1
                    logger.debug(f"Partida não encontrada no banco: {home_id} vs {away_id}")
                    continue

                partida_id = row["partida_id"]

                for bkm in event.get("bookmakers", []):
                    casa = bkm.get("key", "unknown")
                    odd_m, odd_e, odd_v = 0.0, 0.0, 0.0
                    for mkt in bkm.get("markets", []):
                        if mkt.get("key") == "h2h":
                            for out in mkt.get("outcomes", []):
                                name = out.get("name", "")
                                price = float(out.get("price", 0.0))
                                if name == home_team:
                                    odd_m = price
                                elif name.lower() == "draw":
                                    odd_e = price
                                else:
                                    odd_v = price

                    if odd_m > 1.0 and odd_e > 1.0 and odd_v > 1.0:
                        db.inserir_odds(conn, {
                            "partida_id": partida_id,
                            "casa_aposta": casa,
                            "odd_mandante": odd_m,
                            "odd_empate": odd_e,
                            "odd_visitante": odd_v,
                        })
                        salvas += 1

            except Exception as e:
                erros += 1
                logger.debug(f"Erro salvando odds: {e}")

    logger.info(f"Odds: {salvas} salvas, {sem_match} sem match, {erros} erros.")
    return salvas
```

---

### 4. Bug: `_atualizar_resultados_historico_csv()` sem rollback em falha parcial

**Arquivo:** `copa2026_scraper.py`

**Problema:** Updates são acumulados no loop e o `conn.commit()` fica fora de qualquer bloco de proteção. Se o processo morrer no meio (erro de I/O, sinal do OS), o estado do banco fica inconsistente.

**Código atual:**
```python
for row in rows:
    conn.execute("UPDATE historico_analises SET ...")
    atualizados += 1
conn.commit()
```

**Correção — transação explícita com rollback:**
```python
def _atualizar_resultados_historico_csv(conn, jogos) -> int:
    atualizados = 0
    try:
        conn.execute("BEGIN")
        for p in jogos:
            if p["resultado"] == "PENDENTE":
                continue
            rows = conn.execute(
                """SELECT id, mercado_sugerido, odd_disponivel, stake_kelly
                   FROM historico_analises
                   WHERE partida_id = ? AND resultado_partida = 'PENDENTE'""",
                (p["partida_id"],),
            ).fetchall()
            for row in rows:
                acertou = row["mercado_sugerido"] == p["resultado"]
                lucro = round(
                    (row["stake_kelly"] * (row["odd_disponivel"] - 1.0))
                    if acertou
                    else (-row["stake_kelly"]),
                    5,
                )
                conn.execute(
                    "UPDATE historico_analises SET resultado_partida = ?, lucro_prejuizo = ? WHERE id = ?",
                    (p["resultado"], lucro, row["id"]),
                )
                atualizados += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Rollback em _atualizar_resultados_historico_csv: {e}")
        raise
    return atualizados
```

---

## 🟡 IMPORTANTE — Próximo ciclo de refatoração

---

### 5. `raise FileNotFoundError` no escopo global dos módulos impede testes

**Arquivos:** `fetcher_agent.py`, `llm_agent.py`, `check_today.py`

**Problema:** O bloco de verificação do `.env` está no nível de módulo. Qualquer `import fetcher_agent` em testes unitários ou em outros módulos vai falhar se `.env` não existir no diretório de trabalho.

**Código atual:**
```python
# Nível de módulo — executa no import
if not os.path.exists(".env"):
    raise FileNotFoundError("Arquivo .env não encontrado!")
load_dotenv()
```

**Correção — mover para um módulo central e inicializar sob demanda:**
```python
# config.py  (novo arquivo)
import os
from pathlib import Path
from dotenv import load_dotenv

_loaded = False

def load_config() -> None:
    """Carrega .env uma única vez. Levanta erro claro se não encontrado."""
    global _loaded
    if _loaded:
        return
    env_path = Path(".env")
    if not env_path.exists():
        raise FileNotFoundError(
            "Arquivo .env não encontrado. Copie .env.example para .env e preencha as chaves."
        )
    load_dotenv(env_path)
    _loaded = True


def get(key: str, default: str = "") -> str:
    load_config()
    return os.getenv(key, default)
```

```python
# fetcher_agent.py, llm_agent.py — substituir o bloco global por:
import config

# Remover o bloco if not os.path.exists(".env"): raise ...
# Remover o load_dotenv() global

FOOTBALL_DATA_KEY = config.get("FOOTBALL_DATA_KEY")
THE_ODDS_API_KEY  = config.get("THE_ODDS_API_KEY")
# etc.
```

Dessa forma, módulos podem ser importados em qualquer contexto. O erro só é levantado quando `config.get()` é chamado pela primeira vez — ou seja, quando a chave é realmente necessária.

---

### 6. Três dicionários de ID de times divergentes — fonte única de verdade

**Arquivos:** `copa2026_scraper.py` (→ `FIFA_ID_MAP`), `fetcher_agent.py` (→ `OPENFOOTBALL_NAME_MAP` + `OPENFOOTBALL_PT_MAP`)

**Problema:** Os mesmos dados de mapeamento time→ID são mantidos em três lugares. Já há inconsistência: `copa2026_scraper.py` tem `"Colombia": 33` e `"Colômbia": 33` na mesma dict (linha duplicada). Qualquer adição ou correção precisa ser feita em três lugares.

**Correção — centralizar em `teams.py`:**
```python
# teams.py  (novo arquivo)
"""
Fonte única de verdade para mapeamento de times da Copa 2026.
Chaves em inglês (padrão APIs internacionais).
Valores: (time_id, nome_pt)
"""

TEAMS: dict[str, tuple[int, str]] = {
    "Argentina":            (2,   "Argentina"),
    "Brazil":               (3,   "Brasil"),
    "France":               (4,   "França"),
    "Germany":              (5,   "Alemanha"),
    "Spain":                (8,   "Espanha"),
    "England":              (9,   "Inglaterra"),
    "Italy":                (10,  "Itália"),
    "Portugal":             (12,  "Portugal"),
    "Netherlands":          (13,  "Holanda"),
    "Belgium":              (14,  "Bélgica"),
    "Croatia":              (15,  "Croácia"),
    "Uruguay":              (16,  "Uruguai"),
    "Mexico":               (23,  "México"),
    "United States":        (24,  "Estados Unidos"),
    "Canada":               (25,  "Canadá"),
    "Serbia":               (27,  "Sérvia"),
    "Switzerland":          (29,  "Suíça"),
    "Poland":               (32,  "Polônia"),
    "Colombia":             (33,  "Colômbia"),
    "Sweden":               (34,  "Suécia"),
    "Ukraine":              (35,  "Ucrânia"),
    "Japan":                (37,  "Japão"),
    "South Korea":          (38,  "Coreia do Sul"),
    "Australia":            (39,  "Austrália"),
    "Cameroon":             (40,  "Camarões"),
    "Costa Rica":           (42,  "Costa Rica"),
    "Morocco":              (45,  "Marrocos"),
    "Iran":                 (46,  "Irã"),
    "Nigeria":              (47,  "Nigéria"),
    "Ghana":                (48,  "Gana"),
    "Senegal":              (49,  "Senegal"),
    "Egypt":                (55,  "Egito"),
    "Chile":                (60,  "Chile"),
    "Peru":                 (63,  "Peru"),
    "Paraguay":             (65,  "Paraguai"),
    "Ecuador":              (67,  "Equador"),
    "Saudi Arabia":         (68,  "Arábia Saudita"),
    "Qatar":                (75,  "Qatar"),
    "New Zealand":          (107, "Nova Zelândia"),
    "Panama":               (114, "Panamá"),
    "Jamaica":              (121, "Jamaica"),
    "Honduras":             (137, "Honduras"),
    "Bolivia":              (140, "Bolívia"),
    "Venezuela":            (141, "Venezuela"),
    "India":                (151, "Índia"),
    "Tunisia":              (155, "Tunísia"),
    "Algeria":              (156, "Argélia"),
    "Ivory Coast":          (157, "Costa do Marfim"),
    "Austria":              (158, "Áustria"),
    "Denmark":              (159, "Dinamarca"),
    "Norway":               (161, "Noruega"),
    "Scotland":             (162, "Escócia"),
    "Wales":                (163, "Gales"),
    "Hungary":              (164, "Hungria"),
    "Romania":              (165, "Romênia"),
    "Czech Republic":       (166, "República Tcheca"),
    "Slovakia":             (167, "Eslováquia"),
    "Greece":               (168, "Grécia"),
    "Albania":              (169, "Albânia"),
    "Iceland":              (170, "Islândia"),
    "Ireland":              (171, "Irlanda"),
    "Bosnia and Herzegovina":(172, "Bósnia e Herzegovina"),
    "Iraq":                 (173, "Iraque"),
    "Cuba":                 (175, "Cuba"),
    "North Korea":          (179, "Coreia do Norte"),
}

# Acessores convenientes
def get_id(nome_en: str) -> int:
    entry = TEAMS.get(nome_en)
    return entry[0] if entry else 0

def get_nome_pt(nome_en: str) -> str:
    entry = TEAMS.get(nome_en)
    return entry[1] if entry else nome_en

# Compatibilidade com código existente
NAME_TO_ID: dict[str, int] = {k: v[0] for k, v in TEAMS.items()}
NAME_PT_TO_ID: dict[str, int] = {v[1]: v[0] for v in TEAMS.values()}
ID_TO_NAME_PT: dict[int, str] = {v[0]: v[1] for v in TEAMS.values()}
```

Nos módulos existentes, substituir as referências às dicts locais:
```python
# fetcher_agent.py
import teams
# OPENFOOTBALL_NAME_MAP → teams.NAME_TO_ID
# OPENFOOTBALL_PT_MAP   → {k: teams.get_nome_pt(k) for k in teams.TEAMS}

# copa2026_scraper.py
import teams
# FIFA_ID_MAP → teams.NAME_PT_TO_ID
```

---

### 7. `historico_analises` acumula duplicatas sem constraint

**Arquivo:** `schema.sql`

**Problema:** Se a análise for rodada depois que um resultado for atualizado, uma nova linha `PENDENTE` será inserida para a mesma partida no mesmo mercado. Não há nada no schema impedindo isso.

**Correção — adicionar UNIQUE constraint no schema:**
```sql
-- schema.sql — substituir a definição de historico_analises
CREATE TABLE IF NOT EXISTS historico_analises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partida_id INTEGER,
    prob_mandante_real REAL,
    prob_empate_real REAL,
    prob_visitante_real REAL,
    mercado_sugerido TEXT,
    odd_disponivel REAL,
    ev_calculado REAL,
    stake_kelly REAL,
    resultado_partida TEXT DEFAULT 'PENDENTE',
    lucro_prejuizo REAL,
    FOREIGN KEY(partida_id) REFERENCES partidas_agenda(partida_id),
    -- Evita duplicata de análise para o mesmo mercado da mesma partida
    UNIQUE(partida_id, mercado_sugerido)
);
```

**Correção — usar `INSERT OR IGNORE` no `db.py`:**
```python
# db.py — registrar_analise_historico()
def registrar_analise_historico(conn: sqlite3.Connection, analise_data: dict) -> None:
    sql = """
    INSERT OR IGNORE INTO historico_analises (
        partida_id, prob_mandante_real, prob_empate_real, prob_visitante_real,
        mercado_sugerido, odd_disponivel, ev_calculado, stake_kelly, resultado_partida
    ) VALUES (
        :partida_id, :prob_mandante_real, :prob_empate_real, :prob_visitante_real,
        :mercado_sugerido, :odd_disponivel, :ev_calculado, :stake_kelly,
        COALESCE(:resultado_partida, 'PENDENTE')
    )
    """
    if "resultado_partida" not in analise_data:
        analise_data["resultado_partida"] = "PENDENTE"
    conn.execute(sql, analise_data)
    conn.commit()
```

> `INSERT OR IGNORE` respeita a constraint UNIQUE e descarta silenciosamente a duplicata.

---

### 8. `calcular_overround()` retorna `0.0` para odds inválidas em vez de sinalizar erro

**Arquivo:** `core_math.py`

**Problema:** Retornar `0.0` faz com que `remover_margem()` calcule como se não houvesse margem nenhuma, injetando probabilidades incorretas no pipeline de Kelly.

**Código atual:**
```python
def calcular_overround(odd_1, odd_x, odd_2):
    if odd_1 <= 0 or odd_x <= 0 or odd_2 <= 0:
        return 0.0
```

**Correção:**
```python
def calcular_overround(odd_1: float, odd_x: float, odd_2: float) -> float:
    """
    Margem embutida nas odds do mercado 1X2.
    Levanta ValueError se qualquer odd for inválida (≤ 1.0).
    """
    if odd_1 <= 1.0 or odd_x <= 1.0 or odd_2 <= 1.0:
        raise ValueError(
            f"Odds inválidas para cálculo de overround: "
            f"1={odd_1}, X={odd_x}, 2={odd_2}. Todas devem ser > 1.0."
        )
    return (1 / odd_1 + 1 / odd_x + 1 / odd_2) - 1
```

O caller em `risk_agent.py` já valida as odds antes de chamar (`if odd_1 <= 1.0 or ...`), então essa mudança não introduz quebras — apenas torna o contrato da função explícito.

---

### 9. `calcular_forca_defesa()` com cap arbitrário e aplicado no lugar errado

**Arquivo:** `core_math.py`

**Problema:** O cap de `2.0` é retornado apenas quando `xg_sofrido_medio <= 0`, mas valores muito baixos como `0.01` (times com poucos jogos) produzem `1.25 / 0.01 = 125.0` sem cap. O cap deveria ser aplicado no resultado final.

**Código atual:**
```python
def calcular_forca_defesa(xg_sofrido_medio: float, media_liga: float) -> float:
    if xg_sofrido_medio <= 0:
        return 2.0   # cap superior: defesa "perfeita"
    if media_liga <= 0:
        return 1.0
    return media_liga / xg_sofrido_medio
```

**Correção:**
```python
# Constante no topo de core_math.py
DEF_STRENGTH_CAP = 2.5   # limite superior empírico para força defensiva


def calcular_forca_defesa(
    xg_sofrido_medio: float,
    media_liga: float,
    cap: float = DEF_STRENGTH_CAP,
) -> float:
    """
    Força de defesa relativa. Escala invertida: menor xG sofrido = maior força.
    Def = média_liga / xG_sofrido, capped em `cap` para evitar explosão
    com amostras pequenas.
    """
    if media_liga <= 0:
        return 1.0
    # Floor mínimo de xG sofrido para evitar divisão por valor ínfimo
    xg_sofrido_safe = max(xg_sofrido_medio, 0.1)
    return min(media_liga / xg_sofrido_safe, cap)
```

---

### 10. Timezone inconsistente entre módulos

**Arquivos:** `db.py`, `fetcher_agent.py`, `check_today.py`

**Problema:** `db.py` usa `CURRENT_TIMESTAMP` do SQLite (UTC), `fetcher_agent.py` usa `datetime.now(timezone.utc)`, e `check_today.py` usa `datetime.datetime.now()` sem timezone. A query de `processar_partidas_pendentes()` filtra por `datetime('now', '-2 hours')` que é UTC — se `data_evento` foi salvo em horário local, partidas podem ser erroneamente excluídas ou incluídas.

**Correção — padronizar para UTC em todo o projeto:**
```python
# check_today.py — substituir:
hoje = datetime.datetime.now().strftime('%Y-%m-%d')
# por:
hoje = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
```

```python
# fetcher_agent.py — em _normalizar_partida_football_data():
# A string utcDate da API já vem em UTC (ex: "2026-06-12T18:00:00Z")
# A conversão atual remove o 'Z' mas não registra que é UTC.
# Garantir que o campo data_evento no banco sempre termine em 'Z' ou seja explicitamente UTC:
"data_evento": utc_date.replace("T", " ").rstrip("Z"),
# Documentar no schema que data_evento é sempre UTC.
```

```python
# Adicionar comentário no schema.sql:
-- NOTA: Todos os campos TIMESTAMP são armazenados em UTC.
-- Usar datetime('now') nas queries (SQLite retorna UTC por padrão).
```

---

## 🟢 BACKLOG — Qualidade e manutenibilidade

---

### 11. Adicionar índices no `schema.sql`

As queries de `risk_agent.py` fazem JOINs e filtros em campos sem índice.

```sql
-- schema.sql — adicionar após os CREATEs existentes

CREATE INDEX IF NOT EXISTS idx_partidas_data
    ON partidas_agenda(data_evento);

CREATE INDEX IF NOT EXISTS idx_partidas_mandante
    ON partidas_agenda(time_mandante_id);

CREATE INDEX IF NOT EXISTS idx_partidas_visitante
    ON partidas_agenda(time_visitante_id);

CREATE INDEX IF NOT EXISTS idx_odds_partida
    ON odds_mercado(partida_id);

CREATE INDEX IF NOT EXISTS idx_odds_timestamp
    ON odds_mercado(timestamp_captura);

CREATE INDEX IF NOT EXISTS idx_historico_partida
    ON historico_analises(partida_id);

CREATE INDEX IF NOT EXISTS idx_historico_resultado
    ON historico_analises(resultado_partida);
```

---

### 12. Testes unitários para `core_math.py`

`core_math.py` é matemática pura sem I/O — o candidato ideal para cobertura de testes. Os sanity checks do `__main__` devem virar testes automatizados.

```python
# tests/test_core_math.py
import pytest
import core_math


class TestForcas:
    def test_forca_ataque_media_retorna_1(self):
        assert core_math.calcular_forca_ataque(1.25, 1.25) == pytest.approx(1.0)

    def test_forca_ataque_zero_media_retorna_1(self):
        """Media zero não deve causar divisão por zero."""
        assert core_math.calcular_forca_ataque(1.0, 0.0) == 1.0

    def test_forca_defesa_cap_aplicado(self):
        """xG sofrido muito baixo não deve explodir a força defensiva."""
        forca = core_math.calcular_forca_defesa(0.01, 1.25)
        assert forca <= core_math.DEF_STRENGTH_CAP

    def test_forca_defesa_media_retorna_1(self):
        assert core_math.calcular_forca_defesa(1.25, 1.25) == pytest.approx(1.0)


class TestPoisson:
    def test_times_iguais_simetria(self):
        probs = core_math.calcular_probabilidades_partida(1.25, 1.25)
        assert abs(probs["MANDANTE"] - probs["VISITANTE"]) < 0.001

    def test_soma_probabilidades_aprox_1(self):
        probs = core_math.calcular_probabilidades_partida(1.5, 1.0)
        total = sum(probs.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_time_forte_maior_prob_vitoria(self):
        probs = core_math.calcular_probabilidades_partida(2.5, 0.8)
        assert probs["MANDANTE"] > probs["VISITANTE"]


class TestKelly:
    def test_ev_negativo_retorna_zero_stake(self):
        stake, ev = core_math.calcular_kelly_fracionado(0.30, 2.10)
        assert stake == 0.0
        assert ev < 0.0

    def test_stake_nunca_supera_cap(self):
        # p=0.99, odd=2.0 → Kelly puro seria enorme
        stake, ev = core_math.calcular_kelly_fracionado(0.99, 2.0)
        assert stake <= core_math.KELLY_STAKE_MAX

    def test_odd_invalida_retorna_zero(self):
        stake, ev = core_math.calcular_kelly_fracionado(0.55, 1.0)
        assert stake == 0.0 and ev == 0.0


class TestOverround:
    def test_overround_odds_justas(self):
        # Odds sem margem: 2.0, 3.0, 6.0 → overround ≈ 0.0
        # 1/2 + 1/3 + 1/6 = 1.0
        assert core_math.calcular_overround(2.0, 3.0, 6.0) == pytest.approx(0.0, abs=0.001)

    def test_overround_positivo_com_margem(self):
        assert core_math.calcular_overround(2.0, 3.4, 3.6) > 0.0

    def test_overround_odds_invalidas_levanta_erro(self):
        with pytest.raises(ValueError):
            core_math.calcular_overround(1.0, 3.4, 3.6)


class TestMonteCarlo:
    def test_mc_converge_para_poisson(self):
        lam_a, lam_b = 1.8, 1.0
        poisson = core_math.calcular_probabilidades_partida(lam_a, lam_b)
        mc = core_math.simular_distribuicao_gols(lam_a, lam_b, n_simulacoes=50_000)
        assert abs(poisson["MANDANTE"] - mc["MANDANTE"]) < 0.02
        assert abs(poisson["EMPATE"]   - mc["EMPATE"])   < 0.02
```

Para rodar:
```bash
pip install pytest --break-system-packages
pytest tests/ -v
```

---

### 13. `api_usage.jsonl` cresce indefinidamente — adicionar limpeza periódica

**Arquivo:** `fetcher_agent.py`

**Problema:** O arquivo é append-only sem rotação. `_contar_uso_api_hoje()` e `status_quota()` leem o arquivo completo a cada chamada.

**Correção — manter apenas os últimos 30 dias:**
```python
# fetcher_agent.py — adicionar função de limpeza

def _limpar_log_antigo(dias: int = 30) -> None:
    """Remove entradas de uso de API com mais de `dias` dias."""
    log_path = Path("data/api_usage.jsonl")
    if not log_path.exists():
        return

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    linhas_validas = []

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("ts", "") >= cutoff:
                    linhas_validas.append(line)
            except Exception:
                pass

    with open(log_path, "w", encoding="utf-8") as f:
        f.writelines(linhas_validas)
```

Chamar `_limpar_log_antigo()` no início de `rotina_ingestao_diaria()`.

---

### 14. Suavização de lambda com threshold contínuo em vez de binário

**Arquivo:** `risk_agent.py`

**Problema:** A suavização atual é binária: times com `jogos < MIN_GAMES_FOR_RELIABLE_STATS` recebem blend, os demais usam a força crua. Um time com exatamente 3 jogos recebe blend; com 4 jogos, não recebe nenhum — apesar de ainda ter alta variância.

**Código atual:**
```python
peso = min(jogos_m, jogos_v) / min_jogos
if jogos_m < min_jogos or jogos_v < min_jogos:
    lambda_a = peso * lambda_a + (1 - peso) * media_liga
    lambda_b = peso * lambda_b + (1 - peso) * media_liga
```

**Correção — peso contínuo até o máximo de jogos da Copa:**
```python
# Máximo de jogos possíveis na Copa (fase de grupos + mata-mata completo)
MAX_JOGOS_COPA = 7

def _peso_confianca(n_jogos: int, max_jogos: int = MAX_JOGOS_COPA) -> float:
    """Peso de confiança crescente com o número de jogos. Nunca chega a 1.0 para nunca ser crua."""
    return min(n_jogos / max_jogos, 1.0)

# Dentro do loop de processar_partidas_pendentes():
peso_m = _peso_confianca(jogos_m)
peso_v = _peso_confianca(jogos_v)
peso = min(peso_m, peso_v)  # usa o time com menos dados como limitante

lambda_a = peso * lambda_a + (1 - peso) * media_liga
lambda_b = peso * lambda_b + (1 - peso) * media_liga

logger.debug(
    f"Peso de confiança ({row['nome_mandante']} vs {row['nome_visitante']}): "
    f"peso={peso:.2f} (jogos_m={jogos_m}, jogos_v={jogos_v})"
)
```

---

## 🟡 IMPORTANTE — Acréscimos da segunda revisão

---

### 15. Modelo Poisson independente ignora correlação entre gols — fator Dixon-Coles ausente

**Arquivo:** `core_math.py`

**Problema:** O Poisson bivariado padrão assume que os gols dos dois times são eventos independentes. Na prática existe correlação negativa: quando um time lidera, tende a recuar e o adversário passa a atacar mais. O efeito mais mensurável ocorre nos placares baixos (0-0, 1-0, 0-1, 1-1), que são exatamente onde o Poisson mais subestima a probabilidade real — e onde a probabilidade de empate fica sistematicamente errada. Dixon e Coles (1997) introduziram um parâmetro `ρ` (rho) aplicado como fator de correção nesses quatro placares específicos.

**Correção — adicionar fator de correlação Dixon-Coles à matriz de Poisson:**
```python
# core_math.py

# Valor de rho estimado empiricamente para Copas do Mundo
# Intervalo típico: -0.13 a -0.08 (correlação negativa fraca)
DIXON_COLES_RHO = -0.10


def _tau(gols_a: int, gols_b: int, lambda_a: float, lambda_b: float, rho: float) -> float:
    """
    Fator de correção Dixon-Coles para placares baixos.
    Retorna 1.0 para todos os placares exceto (0,0), (1,0), (0,1), (1,1).
    """
    if gols_a == 0 and gols_b == 0:
        return 1.0 - lambda_a * lambda_b * rho
    elif gols_a == 1 and gols_b == 0:
        return 1.0 + lambda_b * rho
    elif gols_a == 0 and gols_b == 1:
        return 1.0 + lambda_a * rho
    elif gols_a == 1 and gols_b == 1:
        return 1.0 - rho
    return 1.0


def calcular_probabilidades_partida(
    lambda_a: float,
    lambda_b: float,
    max_gols: int = 8,
    rho: float = DIXON_COLES_RHO,
) -> dict:
    """
    Matriz de Poisson com correção Dixon-Coles para placares baixos.

    O fator rho (negativo) aumenta a probabilidade de empate e reduz
    ligeiramente a probabilidade de vitórias por placar mínimo,
    corrigindo a subestimativa clássica do Poisson independente.
    """
    goals = np.arange(max_gols)
    prob_a = poisson.pmf(goals, lambda_a)
    prob_b = poisson.pmf(goals, lambda_b)

    matrix = np.outer(prob_b, prob_a)

    # Aplica correção Dixon-Coles nos placares baixos
    for j in range(min(2, max_gols)):      # gols_b: 0 e 1
        for i in range(min(2, max_gols)):  # gols_a: 0 e 1
            matrix[j, i] *= _tau(i, j, lambda_a, lambda_b, rho)

    # Renormaliza para garantir que a soma seja 1.0
    total = matrix.sum()
    if total > 0:
        matrix /= total

    return {
        "MANDANTE":  float(np.triu(matrix, k=1).sum()),
        "EMPATE":    float(np.diag(matrix).sum()),
        "VISITANTE": float(np.tril(matrix, k=-1).sum()),
    }
```

> Atualizar `simular_distribuicao_gols()` para receber `rho` e passá-lo como parâmetro opcional, para que Monte Carlo e Poisson analítico usem a mesma premissa e continuem comparáveis.

---

### 16. `fetcher_agent.py` salva gols reais como se fossem xG — mistura conceitual silenciosa

**Arquivo:** `fetcher_agent.py` — `extrair_stats_api_football()`

**Problema:** A API-Football retorna médias de **gols marcados**, não xG. O código mapeia esses valores diretamente para os campos `xg_marcado_casa` / `xg_sofrido_casa`. O modelo inteiro é construído sobre a premissa de que esses campos contêm **Expected Goals** — uma métrica de qualidade de chance independente de conversão. Gols reais embutem variância de finalização: um time sortudo com alta conversão vai parecer artificialmente mais forte; um time azarado vai parecer mais fraco. Para a Copa, com poucos jogos por time, esse viés pode ser significativo.

**Código atual:**
```python
# extrair_stats_api_football() — sem nenhum aviso sobre a natureza dos dados
"xg_marcado_casa": float(data.get("goals", {}).get("for", {})
                         .get("average", {}).get("home", 0.0) or 0.0),
```

**Correção — adicionar campo de rastreamento de fonte e aviso no log:**
```python
# schema.sql — adicionar coluna à times_performance
ALTER TABLE times_performance ADD COLUMN fonte_metrica TEXT DEFAULT 'xg';
-- Valores possíveis: 'xg' (Expected Goals real) | 'gols' (gols realizados como proxy)
```

```python
# fetcher_agent.py — extrair_stats_api_football()
result = {
    "time_id": time_id,
    "nome": data.get("team", {}).get("name", f"Time {time_id}"),
    "liga": data.get("league", {}).get("name", "Copa"),
    # Gols reais usados como proxy de xG (API-Football não fornece xG no tier gratuito)
    "xg_marcado_casa": float(data.get("goals", {}).get("for", {})
                             .get("average", {}).get("home", 0.0) or 0.0),
    "xg_sofrido_casa": float(data.get("goals", {}).get("against", {})
                             .get("average", {}).get("home", 0.0) or 0.0),
    "xg_marcado_fora": float(data.get("goals", {}).get("for", {})
                             .get("average", {}).get("away", 0.0) or 0.0),
    "xg_sofrido_fora": float(data.get("goals", {}).get("against", {})
                             .get("average", {}).get("away", 0.0) or 0.0),
    "jogos_casa": int(data.get("fixtures", {}).get("played", {}).get("home", 0) or 0),
    "jogos_fora": int(data.get("fixtures", {}).get("played", {}).get("away", 0) or 0),
    "fonte_metrica": "gols",   # ← rastreia que estes são gols reais, não xG
}

logger.warning(
    f"[API-Football] Time {time_id}: usando gols reais como proxy de xG. "
    f"Resultados do modelo têm maior variância para este time."
)
```

```python
# risk_agent.py — adicionar aviso quando a partida usa fonte 'gols'
# (após buscar os rows do banco, verificar o campo fonte_metrica)
if row.get("fonte_metrica_m") == "gols" or row.get("fonte_metrica_v") == "gols":
    logger.warning(
        f"{row['nome_mandante']} vs {row['nome_visitante']}: "
        f"um ou ambos os times usam gols reais como proxy de xG. "
        f"Considere EV com margem de segurança maior."
    )
```

---

### 17. Filtro de janela temporal em `processar_partidas_pendentes()` descarta partidas silenciosamente

**Arquivo:** `risk_agent.py`

**Problema:** A query filtra `p.data_evento >= datetime('now', '-2 hours')`, o que significa que partidas cujo kickoff foi há mais de 2 horas são descartadas sem nenhum log de aviso. Em cenários normais de operação — ingestão de odds a cada 6h, análise rodando no início do dia — é perfeitamente possível que uma janela de análise pré-jogo seja perdida sem que o usuário saiba.

**Código atual:**
```python
WHERE
    p.data_evento >= datetime('now', '-2 hours')
    ...
```

**Correção — tornar o threshold configurável e logar partidas descartadas:**
```python
# risk_agent.py — constante no topo do módulo
JANELA_PRE_JOGO_HORAS = 2   # partidas com kickoff há mais de N horas são ignoradas


def processar_partidas_pendentes(
    ev_minimo: float = EV_MINIMO_PADRAO,
    fracao_kelly: float = core_math.KELLY_FRACAO_PADRAO,
    janela_horas: float = JANELA_PRE_JOGO_HORAS,
) -> list[dict]:
    ...
    with db.get_connection() as conn:
        # Verifica se há partidas sendo descartadas pela janela temporal
        descartadas = conn.execute("""
            SELECT COUNT(*) as total
            FROM partidas_agenda p
            WHERE p.data_evento < datetime('now', :janela)
              AND p.data_evento >= datetime('now', '-24 hours')
              AND NOT EXISTS (
                  SELECT 1 FROM historico_analises h
                  WHERE h.partida_id = p.partida_id
              )
        """, {"janela": f"-{int(janela_horas)} hours"}).fetchone()

        if descartadas and descartadas["total"] > 0:
            logger.warning(
                f"{descartadas['total']} partida(s) descartada(s) por janela temporal "
                f"(kickoff há mais de {janela_horas}h). "
                f"Considere rodar a análise mais cedo ou aumentar JANELA_PRE_JOGO_HORAS."
            )

        sql = """
        SELECT ...
        FROM partidas_agenda p
        ...
        WHERE
            p.data_evento >= datetime('now', :janela)
            ...
        """
        rows = conn.execute(sql, {"janela": f"-{int(janela_horas)} hours"}).fetchall()
```

---

### 18. `app.py` pode quebrar antes de qualquer interação se o banco não existir

**Arquivo:** `app.py`

**Problema:** `carregar_recomendacoes()` é chamada incondicionalmente no carregamento da página, abrindo uma conexão com o banco. Se o banco não foi inicializado ainda (`db.init_db()` nunca foi chamado), a exceção derruba o painel inteiro com uma mensagem genérica de SQLite — sem nenhuma indicação de que o problema é a ausência do banco.

**Código atual:**
```python
def carregar_recomendacoes():
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM historico_analises WHERE resultado_partida = 'PENDENTE'"
        ).fetchall()]

def main():
    st.title("⚽ Football Quant Analyst")
    tab1, _, _, _ = st.tabs([...])
    ...
    recs = carregar_recomendacoes()   # chamada imediata, sem proteção
```

**Correção:**
```python
def main():
    # Garante que o banco existe antes de qualquer operação
    try:
        db.init_db()
    except Exception as e:
        st.error(f"❌ Erro ao inicializar o banco de dados: {e}")
        st.stop()

    st.title("⚽ Football Quant Analyst")
    tab1, tab_hedge, tab_backtest, tab_config = st.tabs(
        ["📊 Recomendações EV+", "🛡️ Hedge", "📈 Backtest", "🔧 Config"]
    )

    with tab1:
        if st.button("🔄 Gerar Análise"):
            with st.spinner("Analisando partidas..."):
                try:
                    requests.post(f"{API_URL}/analyze", json={"ev_minimo": 0.02}, timeout=5)
                    st.success("Análise iniciada em background.")
                except requests.exceptions.ConnectionError:
                    st.error("❌ API não está rodando. Execute: python run.py")

        if st.button("📥 Importar Copa 2026"):
            with st.spinner("Importando dados..."):
                try:
                    requests.post(f"{API_URL}/ingest", json={"fonte": "all"}, timeout=5)
                    st.success("Ingestão iniciada em background.")
                except requests.exceptions.ConnectionError:
                    st.error("❌ API não está rodando. Execute: python run.py")

        try:
            recs = carregar_recomendacoes()
        except Exception as e:
            st.warning(f"Sem recomendações disponíveis. ({e})")
            recs = []

        if not recs:
            st.info("Nenhuma oportunidade EV+ pendente. Clique em 'Gerar Análise'.")
        for rec in recs:
            st.write(f"{rec['mercado_sugerido']} @ {rec['odd_disponivel']}")
```

---

## 🟢 BACKLOG — Acréscimos da segunda revisão

---

### 19. Prompt do `llm_agent.py` não reforça o caráter consultivo do sistema

**Arquivo:** `llm_agent.py`

**Problema:** O prompt enviado à Mistral instrui o modelo a agir como "analista quantitativo de elite" e a escrever recomendações de estratégia. Não há nenhuma instrução dizendo que o sistema é estritamente consultivo e não executa apostas. Dependendo do tom do texto gerado e de como ele for exibido no painel, pode criar a impressão de que o sistema está "recomendando" uma aposta de forma afirmativa, não apenas calculando valor esperado.

**Correção — acrescentar instrução explícita no prompt:**
```python
# llm_agent.py — gerar_analise_especialista()

prompt = f"""
Você é um analista quantitativo de futebol de elite, focado na Copa do Mundo da FIFA.
O nosso modelo matemático rigoroso (baseado em Poisson e Kelly) identificou as seguintes apostas
com Valor Esperado Positivo (EV+) para os jogos atuais:

{contexto_str}

Por favor, escreva um breve parágrafo (máximo de 4-5 frases) resumindo essa estratégia para o investidor.
Seja analítico, frio, focado no longo prazo e mencione que a matemática indica valor nessas cotações.
Não recomende ir 'all-in', reforce a gestão de banca e a disciplina quantitativa.

IMPORTANTE: Este sistema é estritamente consultivo. Nunca sugira que o sistema executa apostas
automaticamente, pois toda decisão e execução manual são de responsabilidade exclusiva do usuário.
Reforce isso brevemente ao final da análise.

Aja como o 'Agente Principal' do sistema.
"""
```

---

### 20. `.env.example` ausente — bloqueio de onboarding

**Problema:** O README instrui o usuário a `cp .env.example .env`, mas o arquivo não está no repositório. Com 6 variáveis de ambiente necessárias, qualquer pessoa que clone o projeto fica sem saber o que configurar.

**Correção — criar `.env.example`:**
```bash
# .env.example
# Copie este arquivo para .env e preencha os valores antes de rodar o projeto.

# ── football-data.org ─────────────────────────────────────────────────
# Gratuito. Crie uma conta em https://www.football-data.org/
FOOTBALL_DATA_KEY=seu_token_aqui

# ── The Odds API ──────────────────────────────────────────────────────
# 500 req/mês no plano gratuito. Cadastro em https://the-odds-api.com/
THE_ODDS_API_KEY=seu_token_aqui
THE_ODDS_API_BASE_URL=https://api.the-odds-api.com/v4

# ── API-Football (fallback) ───────────────────────────────────────────
# 100 req/dia no plano gratuito. Cadastro em https://www.api-football.com/
API_FOOTBALL_KEY=seu_token_aqui
API_FOOTBALL_BASE_URL=https://v3.football.api-sports.io

# ── Mistral AI (análise qualitativa — opcional) ───────────────────────
# Sem esta chave, a aba de análise qualitativa fica desativada.
# Cadastro em https://console.mistral.ai/
MISTRAL_API_KEY=seu_token_aqui
```

---

## Resumo das mudanças por arquivo

| Arquivo | Tipo | Mudança |
|---|---|---|
| `risk_agent.py` | 🔴 Bug | Separar `row` mandante/visitante na query e na chamada de `calcular_lambdas_copa` |
| `api/main.py` | 🔴 Novo | Criar módulo FastAPI com endpoints `/analyze`, `/ingest`, `/status`, `/recomendacoes` |
| `fetcher_agent.py` | 🔴 Bug | Match de odds por ID de time em vez de LIKE em 6 chars |
| `copa2026_scraper.py` | 🔴 Bug | Transação explícita com rollback em `_atualizar_resultados_historico_csv` |
| `config.py` | 🟡 Novo | Centralizar carga do `.env` fora do escopo global dos módulos |
| `teams.py` | 🟡 Novo | Fonte única de verdade para mapeamento time→ID |
| `schema.sql` | 🟡 Melhoria | UNIQUE constraint em `historico_analises(partida_id, mercado_sugerido)` + índices + coluna `fonte_metrica` |
| `db.py` | 🟡 Melhoria | `INSERT OR IGNORE` em `registrar_analise_historico` |
| `core_math.py` | 🟡 Bug + Melhoria | `calcular_overround` levanta `ValueError`; cap correto em `calcular_forca_defesa`; fator Dixon-Coles (rho) na matriz de Poisson |
| `fetcher_agent.py` | 🟡 Melhoria | Rastrear `fonte_metrica` (xG vs gols reais) ao salvar dados da API-Football |
| `risk_agent.py` | 🟡 Melhoria | Janela temporal configurável com log de partidas descartadas |
| `app.py` | 🟡 Bug | `db.init_db()` no startup do painel; tratamento de erro em `carregar_recomendacoes` e nas chamadas à API |
| `check_today.py` | 🟡 Melhoria | Timezone UTC explícito |
| `tests/test_core_math.py` | 🟢 Novo | Testes unitários para `core_math` |
| `fetcher_agent.py` | 🟢 Melhoria | Limpeza periódica do `api_usage.jsonl` |
| `risk_agent.py` | 🟢 Melhoria | Peso de confiança contínuo para suavização de lambda |
| `llm_agent.py` | 🟢 Melhoria | Instrução explícita de caráter consultivo no prompt da Mistral |
| `.env.example` | 🟢 Novo | Arquivo de referência com todas as variáveis de ambiente necessárias |
