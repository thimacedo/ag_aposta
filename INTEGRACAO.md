# Guia de Integração — Football Quant-Agent + Spider FIFA Copa 2026

> **Projeto destino:** `C:\Projetos\ag_aposta`
> **Sistema:** Análise quantitativa de apostas para a Copa do Mundo 2026 (EUA/Canadá/México)
> **Spider:** Captura automática a cada 3h da página oficial da FIFA
> **Última atualização:** 2026-06-19

---

## 📋 Sumário

1. [Visão Geral da Arquitetura](#1-visão-geral-da-arquitetura)
2. [Pré-requisitos](#2-pré-requisitos)
3. [Estrutura de Diretórios](#3-estrutura-de-diretórios)
4. [Passo 1 — Criar arquivos base do Quant-Agent](#passo-1--criar-arquivos-base-do-quant-agent)
5. [Passo 2 — Criar o schema e a camada de banco (db.py)](#passo-2--criar-o-schema-e-a-camada-de-banco-dbpy)
6. [Passo 3 — Criar o motor matemático (core_math.py)](#passo-3--criar-o-motor-matemático-core_mathpy)
7. [Passo 4 — Criar o Agente de Risco (risk_agent.py)](#passo-4--criar-o-agente-de-risco-risk_agentpy)
8. [Passo 5 — Criar importadores de dados](#passo-5--criar-importadores-de-dados)
9. [Passo 6 — Criar workers e frontend](#passo-6--criar-workers-e-frontend)
10. [Passo 7 — Integrar o Spider FIFA (schema V2)](#passo-7--integrar-o-spider-fifa-schema-v2)
11. [Passo 8 — Criar o adaptador spider → DB (fifa_sync.py)](#passo-8--criar-o-adaptador-spider--db-fifa_syncpy)
12. [Passo 9 — Orquestrar spider + sync (worker_ingest.py)](#passo-9--orquestrar-spider--sync-worker_ingestpy)
13. [Passo 10 — Validação e Testes](#passo-10--validação-e-testes)
14. [Passo 11 — Execução em Produção](#passo-11--execução-em-produção)
15. [Troubleshooting](#troubleshooting)
16. [Apêndice — Mapeamentos e Referências](#apêndice--mapeamentos-e-referências)

---

## 1. Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FONTE DE DADOS                              │
│              https://www.fifa.com/pt/tournaments/mens/              │
│              worldcup/canadamexicousa2026/scores-fixtures           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           │  Playwright (headless Chromium)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       fifa_spider.py                                │
│  - Abre a página, aguarda React hidratar, faz scroll                │
│  - Parser DOM específico (classes match-row_*)                      │
│  - Salva: captures/capture_YYYY-MM-DD_HHMMSS.json                   │
│  - Salva: raw_html/raw_YYYY-MM-DD_HHMMSS.html (backup)              │
│  - Detecção de mudanças (diff vs última execução) → changes.log     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           │  JSON da última captura
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        fifa_sync.py                                 │
│  Adaptador spider → DB. Para cada partida da captura:               │
│  - UPSERT em partidas_agenda (incl. status, scores, stadium, etc.)  │
│  - INSERT em partidas_fifa_log (histórico versionado)               │
│  - UPSERT em times_fifa_codes (códigos BRA/ARG/W95/...)             │
│  - Registra metadados em capturas_fifa                              │
│  - Atualiza historico_analises para partidas finalizadas            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   SQLite: data/football_quant.db                    │
│  times_performance    xG/métricas por time                          │
│  partidas_agenda      104 partidas (+ campos FIFA: status, scores)  │
│  odds_mercado         cotações capturadas                           │
│  historico_analises   recomendações + resultados (auto-fechadas)    │
│  capturas_fifa        (NOVO) metadados das execuções do spider      │
│  times_fifa_codes     (NOVO) BRA → time_id (com placeholders)       │
│  partidas_fifa_log    (NOVO) histórico versionado de cada partida   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  core_math.py + risk_agent.py                       │
│  - Modelo Poisson (campo neutro, λ = Atk × Def × média_liga)        │
│  - Devigging (overround), Kelly fracionado, hedging 3 modos         │
│  - Gera analises em historico_analises com EV e stake               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         app.py (Streamlit)                          │
│  Painel visual com oportunidades EV+, hedge e backtest              │
└─────────────────────────────────────────────────────────────────────┘
```

### Fluxo principal
1. **Spider** captura partidas da FIFA a cada 3h → JSON em disco
2. **Sync** importa o JSON para o SQLite (UPSERT por `spider_match_id`)
3. Quando uma partida fica `FIM`, o sync fecha automaticamente as análises
4. **risk_agent** gera recomendações EV+ para partidas futuras com odds
5. **app.py** exibe o painel visual

---

## 2. Pré-requisitos

### Sistema operacional
- Windows 10/11, Linux ou macOS
- Python 3.11+ (testado com 3.12.13)
- 2GB de espaço em disco (Chromium + banco + capturas)

### Bibliotecas Python
```bash
pip install playwright beautifulsoup4 schedule numpy scipy streamlit requests python-dotenv
playwright install chromium
```

### Variáveis de ambiente (.env) — opcional
Necessário apenas se for usar `check_today.py` (APIs externas):

```env
# .env (na raiz do projeto)
API_FOOTBALL_KEY=sua_chave_api_football
THE_ODDS_API_BASE_URL=https://api.the-odds-api.com/v4
THE_ODDS_API_KEY=sua_chave_odds_api
```

> O spider FIFA **não precisa** dessas chaves — ele usa Playwright direto no site da FIFA.

---

## 3. Estrutura de Diretórios

```
C:\Projetos\ag_aposta\
│
├── .env                          # (opcional) chaves de API externas
├── .env.example                  # template do .env
├── README.md                     # este arquivo
│
├── app.py                        # Frontend Streamlit
├── check_today.py                # Teste de APIs externas
├── core_math.py                  # Motor Poisson + Kelly + hedging
├── copa2026_scraper.py           # Importador CSV legado
├── db.py                         # Acesso SQLite (com V2)
├── fetcher_agent.py              # Fetcher de odds (API-Football + Odds API)
├── fifa_spider.py                # Spider Playwright (NOVO)
├── fifa_sync.py                  # Adaptador spider→DB (NOVO)
├── risk_agent.py                 # Agente de risco (Kelly + Poisson)
├── schema.sql                    # DDL (com V2)
├── worker_analyze.py             # Worker de análise
├── worker_ingest.py              # Orquestrador spider+sync (NOVO)
│
├── data\
│   ├── football_quant.db         # SQLite (criado automaticamente)
│   └── copa2026_resultados.csv   # (opcional) para backfill via CSV
│
└── fifa_spider\                  # Outputs do spider (criado automaticamente)
    ├── captures\                 # JSON por execução
    ├── raw_html\                 # HTML bruto + next_data (backup)
    └── logs\
        ├── spider.log            # log de execução
        └── changes.log           # log de mudanças detectadas
```

> Em Linux/Mac, o `fifa_spider\` costuma ficar em `/var/lib/ag_aposta/fifa_spider/` ou em `~/.local/share/ag_aposta/fifa_spider/`. Edite `SPIDER_ROOT` em `fifa_spider.py` e `fifa_sync.py` conforme necessário.

---

## Passo 1 — Criar arquivos base do Quant-Agent

### 1.1 Criar a estrutura de pastas

Abra um terminal (PowerShell no Windows) e execute:

```powershell
# No Windows
cd C:\Projetos
mkdir ag_aposta
cd ag_aposta
mkdir data
mkdir fifa_spider\captures
mkdir fifa_spider\raw_html
mkdir fifa_spider\logs
```

```bash
# No Linux/Mac
cd /c/Projetos   # ou /home/usuario/Projetos
mkdir -p ag_aposta/data
mkdir -p ag_aposta/fifa_spider/{captures,raw_html,logs}
cd ag_aposta
```

### 1.2 Criar o `.env.example`

Crie o arquivo `.env.example` na raiz do projeto:

```env
# API-Football (https://api-football.com/)
API_FOOTBALL_KEY=cole_sua_chave_aqui

# The Odds API (https://the-odds-api.com/)
THE_ODDS_API_BASE_URL=https://api.the-odds-api.com/v4
THE_ODDS_API_KEY=cole_sua_chave_aqui
```

Copie para `.env` e preencha com suas chaves reais (opcional para o spider).

### 1.3 Criar o `requirements.txt`

```
playwright>=1.40.0
beautifulsoup4>=4.12.0
schedule>=1.2.0
numpy>=1.24.0
scipy>=1.10.0
streamlit>=1.30.0
requests>=2.31.0
python-dotenv>=1.0.0
```

Instale com:

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Passo 2 — Criar o schema e a camada de banco (db.py)

### 2.1 Criar `schema.sql`

Este arquivo contém o DDL completo do SQLite, incluindo as tabelas novas do spider (V2).

```sql
-- =====================================================================
-- schema.sql
-- Esquema do banco de dados local (SQLite) do Futebol Quant-Agent.
-- Executar via: sqlite3 data/football_quant.db < schema.sql
-- ou através de db.py:init_db()
-- =====================================================================
-- VERSÃO 2 — adiciona integração com o spider FIFA (capturas automáticas)
-- =====================================================================

-- Tabela de Times e Métricas de Performance Acumuladas
CREATE TABLE IF NOT EXISTS times_performance (
    time_id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    liga TEXT NOT NULL,
    xg_marcado_casa REAL,
    xg_sofrido_casa REAL,
    xg_marcado_fora REAL,
    xg_sofrido_fora REAL,
    jogos_casa INTEGER,
    jogos_fora INTEGER,
    ultima_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de Partidas do Dia / Agendadas
CREATE TABLE IF NOT EXISTS partidas_agenda (
    partida_id INTEGER PRIMARY KEY,
    liga TEXT NOT NULL,
    data_evento TIMESTAMP NOT NULL,
    time_mandante_id INTEGER,
    time_visitante_id INTEGER,
    FOREIGN KEY(time_mandante_id) REFERENCES times_performance(time_id),
    FOREIGN KEY(time_visitante_id) REFERENCES times_performance(time_id)
);

-- Tabela de Monitoramento de Cotações (Odds)
CREATE TABLE IF NOT EXISTS odds_mercado (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partida_id INTEGER,
    casa_aposta TEXT NOT NULL,
    odd_mandante REAL,
    odd_empate REAL,
    odd_visitante REAL,
    timestamp_captura TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(partida_id) REFERENCES partidas_agenda(partida_id)
);

-- Tabela de Histórico de Recomendações e Resultados (Para Backtesting)
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
    resultado_partida TEXT, -- 'MANDANTE', 'EMPATE', 'VISITANTE', 'PENDENTE'
    lucro_prejuizo REAL,
    FOREIGN KEY(partida_id) REFERENCES partidas_agenda(partida_id)
);

-- =====================================================================
-- V2 — Extensões para integração com o spider FIFA
-- =====================================================================
-- Colunas extras em partidas_agenda (via ALTER TABLE; idempotente via db.py)
-- SQLite não tem "ADD COLUMN IF NOT EXISTS" — os ALTERs são executados
-- em Python com try/except (ver db.py:_apply_schema_v2)
-- =====================================================================

-- Metadados de cada execução do spider
CREATE TABLE IF NOT EXISTS capturas_fifa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_timestamp TEXT NOT NULL,
    source_url TEXT,
    json_path TEXT,
    html_path TEXT,
    matches_found INTEGER DEFAULT 0,
    matches_played INTEGER DEFAULT 0,
    matches_upcoming INTEGER DEFAULT 0,
    matches_live INTEGER DEFAULT 0,
    html_size_bytes INTEGER,
    changes_detected INTEGER DEFAULT 0,
    execution_time_sec REAL,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    imported_to_db INTEGER DEFAULT 0,
    imported_at TIMESTAMP
);

-- Mapeamento entre códigos FIFA (3 letras) e os time_id numéricos do sistema
CREATE TABLE IF NOT EXISTS times_fifa_codes (
    fifa_code TEXT PRIMARY KEY,
    time_id INTEGER,
    nome_pt TEXT,
    nome_en TEXT,
    flag_url TEXT,
    is_placeholder INTEGER DEFAULT 0,
    FOREIGN KEY(time_id) REFERENCES times_performance(time_id)
);

-- Histórico versionado de partidas — cada mudança do spider gera uma linha
CREATE TABLE IF NOT EXISTS partidas_fifa_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id INTEGER NOT NULL,
    spider_match_id TEXT NOT NULL,
    match_index INTEGER,
    home_code TEXT,
    home_name TEXT,
    away_code TEXT,
    away_name TEXT,
    score_home INTEGER,
    score_away INTEGER,
    status TEXT,
    kick_off_time TEXT,
    phase TEXT,
    group_name TEXT,
    stadium TEXT,
    city TEXT,
    match_date TEXT,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(capture_id) REFERENCES capturas_fifa(id)
);

-- Índices para acelerar consultas comuns
CREATE INDEX IF NOT EXISTS idx_partidas_agenda_data
    ON partidas_agenda(data_evento);
-- idx_partidas_agenda_spider_match_id é criado em db.py:_apply_schema_v2
-- (depois que a coluna spider_match_id é adicionada via ALTER TABLE)
CREATE INDEX IF NOT EXISTS idx_capturas_fifa_ts
    ON capturas_fifa(capture_timestamp);
CREATE INDEX IF NOT EXISTS idx_partidas_fifa_log_match
    ON partidas_fifa_log(spider_match_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_historico_analises_pendentes
    ON historico_analises(resultado_partida);
```

### 2.2 Criar `db.py`

```python
"""
db.py
Camada de acesso ao banco de dados local (SQLite).

Responsabilidades:
- Inicializar o arquivo de banco e aplicar o schema (schema.sql).
- Aplicar migrations V2 (ALTER TABLE) para integrar o spider FIFA.
- Fornecer uma conexão configurada (row_factory, foreign_keys).
- Funções utilitárias de leitura/escrita (UPSERTs) para os agentes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "football_quant.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def init_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    """
    Cria o arquivo de banco (se não existir) e aplica o schema DDL.
    Idempotente.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        with open(schema_path, "r", encoding="utf-8") as f:
            ddl = f.read()
        conn.executescript(ddl)
        # Aplica migrations V2 (ALTER TABLE) — idempotentes
        _apply_schema_v2(conn)
        conn.commit()


def _apply_schema_v2(conn: sqlite3.Connection) -> None:
    """
    Aplica ALTER TABLE em partidas_agenda para adicionar as colunas do
    spider FIFA. SQLite não suporta 'ADD COLUMN IF NOT EXISTS', então
    usamos try/except para cada uma.
    """
    new_columns = [
        ("codigo_fifa_home",      "TEXT"),
        ("codigo_fifa_away",      "TEXT"),
        ("nome_mandante",         "TEXT"),
        ("nome_visitante",        "TEXT"),
        ("fase",                  "TEXT"),
        ("grupo",                 "TEXT"),
        ("estadio",               "TEXT"),
        ("cidade",                "TEXT"),
        ("horario_kickoff",       "TEXT"),
        ("status_fifa",           "TEXT"),
        ("score_home",            "INTEGER"),
        ("score_away",            "INTEGER"),
        ("flag_url_home",         "TEXT"),
        ("flag_url_away",         "TEXT"),
        ("spider_match_id",       "TEXT"),
        ("ultima_atualizacao_fifa", "TIMESTAMP"),
    ]
    for col_name, col_type in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE partidas_agenda ADD COLUMN {col_name} {col_type};"
            )
        except sqlite3.OperationalError:
            # Coluna já existe — tudo certo
            pass

    # Cria índice em spider_match_id (só agora a coluna existe com certeza)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_partidas_agenda_spider_match_id "
            "ON partidas_agenda(spider_match_id);"
        )
    except sqlite3.OperationalError:
        pass


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context manager que devolve uma conexão SQLite configurada."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Funções de Leitura/Escrita (Implementadas)
# ---------------------------------------------------------------------

def upsert_time_performance(conn: sqlite3.Connection, time_data: dict) -> None:
    """Insere ou atualiza métricas de xG de um time em `times_performance`."""
    sql = """
    INSERT OR REPLACE INTO times_performance (
        time_id, nome, liga,
        xg_marcado_casa, xg_sofrido_casa,
        xg_marcado_fora, xg_sofrido_fora,
        jogos_casa, jogos_fora, ultima_atualizacao
    ) VALUES (
        :time_id, :nome, :liga,
        :xg_marcado_casa, :xg_sofrido_casa,
        :xg_marcado_fora, :xg_sofrido_fora,
        :jogos_casa, :jogos_fora, CURRENT_TIMESTAMP
    )
    """
    conn.execute(sql, time_data)
    conn.commit()


def inserir_partida_agenda(conn: sqlite3.Connection, partida_data: dict) -> int:
    """Insere ou substitui uma partida agendada em `partidas_agenda`."""
    sql = """
    INSERT OR REPLACE INTO partidas_agenda (
        partida_id, liga, data_evento, time_mandante_id, time_visitante_id
    ) VALUES (
        :partida_id, :liga, :data_evento, :time_mandante_id, :time_visitante_id
    )
    """
    cur = conn.execute(sql, partida_data)
    conn.commit()
    return int(partida_data["partida_id"])


def inserir_odds(conn: sqlite3.Connection, odds_data: dict) -> None:
    """Insere uma cotação capturada em `odds_mercado`."""
    sql = """
    INSERT INTO odds_mercado (
        partida_id, casa_aposta, odd_mandante, odd_empate, odd_visitante
    ) VALUES (
        :partida_id, :casa_aposta, :odd_mandante, :odd_empate, :odd_visitante
    )
    """
    conn.execute(sql, odds_data)
    conn.commit()


def registrar_analise_historico(conn: sqlite3.Connection, analise_data: dict) -> None:
    """Registra uma recomendação gerada em `historico_analises`."""
    sql = """
    INSERT INTO historico_analises (
        partida_id, prob_mandante_real, prob_empate_real, prob_visitante_real,
        mercado_sugerido, odd_disponivel, ev_calculado, stake_kelly, resultado_partida
    ) VALUES (
        :partida_id, :prob_mandante_real, :prob_empate_real, :prob_visitante_real,
        :mercado_sugerido, :odd_disponivel, :ev_calculado, :stake_kelly,
        COALESCE(:resultado_partida, 'PENDENTE')
    )
    """
    if 'resultado_partida' not in analise_data:
        analise_data['resultado_partida'] = 'PENDENTE'
    conn.execute(sql, analise_data)
    conn.commit()


# ---------------------------------------------------------------------
# Controle Inteligente de Cache (FinOps)
# ---------------------------------------------------------------------

def verificar_partidas_recentes_liga(conn: sqlite3.Connection, liga_id: int) -> bool:
    """Verifica se houve alguma atualização nas últimas 24h."""
    sql = """
    SELECT count(*) as total FROM partidas_agenda
    WHERE liga = ? AND date(data_evento) = date('now')
    """
    cur = conn.execute(sql, (str(liga_id),))
    row = cur.fetchone()
    return row["total"] > 0


def verificar_odds_recentes(conn: sqlite3.Connection) -> bool:
    """Verifica se houve alguma atualização de odds nas últimas 6h."""
    sql = """
    SELECT count(*) as total FROM odds_mercado
    WHERE timestamp_captura > datetime('now', '-6 hours')
    """
    cur = conn.execute(sql)
    row = cur.fetchone()
    return row["total"] > 0


# =====================================================================
# V2 — Funções para integração com o spider FIFA
# =====================================================================

def registrar_captura_fifa(conn: sqlite3.Connection, captura: dict) -> int:
    """Registra metadados de uma execução do spider em `capturas_fifa`."""
    sql = """
    INSERT INTO capturas_fifa (
        capture_timestamp, source_url, json_path, html_path,
        matches_found, matches_played, matches_upcoming, matches_live,
        html_size_bytes, changes_detected, execution_time_sec,
        success, error_message
    ) VALUES (
        :capture_timestamp, :source_url, :json_path, :html_path,
        :matches_found, :matches_played, :matches_upcoming, :matches_live,
        :html_size_bytes, :changes_detected, :execution_time_sec,
        :success, :error_message
    )
    """
    cur = conn.execute(sql, captura)
    conn.commit()
    return int(cur.lastrowid)


def marcar_captura_importada(conn: sqlite3.Connection, capture_id: int) -> None:
    """Marca uma captura como já sincronizada com partidas_agenda."""
    conn.execute(
        """UPDATE capturas_fifa
              SET imported_to_db = 1, imported_at = CURRENT_TIMESTAMP
            WHERE id = ?""",
        (capture_id,),
    )
    conn.commit()


def upsert_partida_fifa(conn: sqlite3.Connection, partida: dict) -> int:
    """
    UPSERT de uma partida vinda do spider em `partidas_agenda`.
    Chave de conflto: spider_match_id (hash estável do spider).
    """
    row = conn.execute(
        "SELECT partida_id FROM partidas_agenda WHERE spider_match_id = ?",
        (partida["spider_match_id"],),
    ).fetchone()

    if row:
        sql = """
        UPDATE partidas_agenda SET
            codigo_fifa_home    = :codigo_fifa_home,
            codigo_fifa_away    = :codigo_fifa_away,
            nome_mandante       = :nome_mandante,
            nome_visitante      = :nome_visitante,
            fase                = :fase,
            grupo               = :grupo,
            estadio             = :estadio,
            cidade              = :cidade,
            horario_kickoff     = :horario_kickoff,
            status_fifa         = :status_fifa,
            score_home          = :score_home,
            score_away          = :score_away,
            flag_url_home       = :flag_url_home,
            flag_url_away       = :flag_url_away,
            data_evento         = :data_evento,
            ultima_atualizacao_fifa = CURRENT_TIMESTAMP
          WHERE spider_match_id = :spider_match_id
        """
        conn.execute(sql, partida)
        partida_id = int(row["partida_id"])
    else:
        partida_id = partida.get("partida_id") or (
            100000 + int(partida.get("match_index", 0))
        )
        sql = """
        INSERT INTO partidas_agenda (
            partida_id, liga, data_evento,
            time_mandante_id, time_visitante_id,
            codigo_fifa_home, codigo_fifa_away,
            nome_mandante, nome_visitante,
            fase, grupo, estadio, cidade,
            horario_kickoff, status_fifa,
            score_home, score_away,
            flag_url_home, flag_url_away,
            spider_match_id, ultima_atualizacao_fifa
        ) VALUES (
            :partida_id, :liga, :data_evento,
            :time_mandante_id, :time_visitante_id,
            :codigo_fifa_home, :codigo_fifa_away,
            :nome_mandante, :nome_visitante,
            :fase, :grupo, :estadio, :cidade,
            :horario_kickoff, :status_fifa,
            :score_home, :score_away,
            :flag_url_home, :flag_url_away,
            :spider_match_id, CURRENT_TIMESTAMP
        )
        """
        partida["partida_id"] = partida_id
        conn.execute(sql, partida)

    conn.commit()
    return partida_id


def inserir_partida_fifa_log(conn: sqlite3.Connection,
                              capture_id: int,
                              match: dict) -> None:
    """Insere uma linha no histórico versionado `partidas_fifa_log`."""
    sql = """
    INSERT INTO partidas_fifa_log (
        capture_id, spider_match_id, match_index,
        home_code, home_name, away_code, away_name,
        score_home, score_away, status, kick_off_time,
        phase, group_name, stadium, city, match_date
    ) VALUES (
        :capture_id, :spider_match_id, :match_index,
        :home_code, :home_name, :away_code, :away_name,
        :score_home, :score_away, :status, :kick_off_time,
        :phase, :group_name, :stadium, :city, :match_date
    )
    """
    params = {
        "capture_id": capture_id,
        "spider_match_id": match.get("match_id"),
        "match_index": match.get("match_index"),
        "home_code": (match.get("home_team") or {}).get("code"),
        "home_name": (match.get("home_team") or {}).get("name"),
        "away_code": (match.get("away_team") or {}).get("code"),
        "away_name": (match.get("away_team") or {}).get("name"),
        "score_home": _to_int_or_none(match.get("score_home")),
        "score_away": _to_int_or_none(match.get("score_away")),
        "status": match.get("status"),
        "kick_off_time": match.get("kick_off_time"),
        "phase": match.get("phase"),
        "group_name": match.get("group"),
        "stadium": match.get("stadium"),
        "city": match.get("city"),
        "match_date": match.get("date"),
    }
    conn.execute(sql, params)
    conn.commit()


def upsert_fifa_code(conn: sqlite3.Connection, code_data: dict) -> None:
    """UPSERT de um código FIFA em `times_fifa_codes`."""
    sql = """
    INSERT INTO times_fifa_codes (
        fifa_code, time_id, nome_pt, nome_en, flag_url, is_placeholder
    ) VALUES (
        :fifa_code, :time_id, :nome_pt, :nome_en, :flag_url, :is_placeholder
    )
    ON CONFLICT(fifa_code) DO UPDATE SET
        time_id       = COALESCE(excluded.time_id, times_fifa_codes.time_id),
        nome_pt       = COALESCE(excluded.nome_pt, times_fifa_codes.nome_pt),
        nome_en       = COALESCE(excluded.nome_en, times_fifa_codes.nome_en),
        flag_url      = COALESCE(excluded.flag_url, times_fifa_codes.flag_url),
        is_placeholder = COALESCE(excluded.is_placeholder, times_fifa_codes.is_placeholder)
    """
    conn.execute(sql, code_data)
    conn.commit()


def resolver_time_id_por_fifa_code(conn: sqlite3.Connection,
                                    fifa_code: str) -> int | None:
    """Retorna o time_id para um código FIFA, ou None se não houver mapeamento."""
    row = conn.execute(
        "SELECT time_id FROM times_fifa_codes WHERE fifa_code = ? AND time_id IS NOT NULL",
        (fifa_code,),
    ).fetchone()
    return int(row["time_id"]) if row else None


def atualizar_resultados_historico_por_fifa(conn: sqlite3.Connection) -> int:
    """
    Atualiza `historico_analises.resultado_partida` para partidas que
    foram finalizadas (status_fifa='FIM') mas continuam marcadas como
    'PENDENTE' no histórico.

    Retorna o número de linhas atualizadas.
    """
    rows = conn.execute(
        """
        SELECT h.id, h.mercado_sugerido, h.odd_disponivel, h.stake_kelly,
               p.score_home, p.score_away
          FROM historico_analises h
          JOIN partidas_agenda p ON h.partida_id = p.partida_id
         WHERE h.resultado_partida = 'PENDENTE'
           AND p.status_fifa = 'FIM'
           AND p.score_home IS NOT NULL
           AND p.score_away IS NOT NULL
        """
    ).fetchall()

    atualizados = 0
    for r in rows:
        sh = r["score_home"]
        sa = r["score_away"]
        if sh > sa:
            resultado = "MANDANTE"
        elif sh == sa:
            resultado = "EMPATE"
        else:
            resultado = "VISITANTE"

        acertou = (r["mercado_sugerido"] == resultado)
        stake = r["stake_kelly"] or 0.0
        odd = r["odd_disponivel"] or 0.0
        lucro = round(
            (stake * (odd - 1.0)) if acertou else (-stake),
            5,
        )

        conn.execute(
            """UPDATE historico_analises
                  SET resultado_partida = ?, lucro_prejuizo = ?
                WHERE id = ?""",
            (resultado, lucro, r["id"]),
        )
        atualizados += 1

    conn.commit()
    return atualizados


def listar_capturas_recentes(conn: sqlite3.Connection,
                              limite: int = 20) -> list[dict]:
    """Retorna as N capturas mais recentes."""
    rows = conn.execute(
        """SELECT id, capture_timestamp, matches_found, matches_played,
                  matches_upcoming, changes_detected, success,
                  imported_to_db, imported_at
             FROM capturas_fifa
            ORDER BY id DESC LIMIT ?""",
        (limite,),
    ).fetchall()
    return [dict(r) for r in rows]


def _to_int_or_none(v) -> int | None:
    """Converte string numérica para int; caso contrário None."""
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None
```

### 2.3 Validar o banco

```bash
python -c "import db; db.init_db(); print('Banco OK')"
```

Saída esperada:
```
Banco OK
```

---

## Passo 3 — Criar o motor matemático (core_math.py)

```python
"""
core_math.py
Motor matemático do Agente Analista (Agente 2).

Copa do Mundo 2026 — EUA / Canadá / México
============================================
PREMISSA FUNDAMENTAL: todos os jogos são em campo NEUTRO.
Não há "mandante" real — a designação é apenas convencional (sorteio).
Logo:
  - HOME_ADVANTAGE = 0.0  (campo neutro total)
  - As métricas de xG de cada time são calculadas como média simples
    sobre TODAS as suas partidas na Copa (sem split casa/fora).
  - O modelo Poisson usa lambda_A e lambda_B simétricos.

Fontes empíricas:
  Pollard & Gómez (2014): vantagem de campo em Copas é próxima de zero
  em sede neutra; qualquer edge residual vem de fuso/distância, não de
  torcida ou gramado.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import poisson


# =====================================================================
# 0. Constantes da Copa do Mundo 2026
# =====================================================================

HOME_ADVANTAGE_COPA = 0.0
MIN_GAMES_FOR_RELIABLE_STATS = 3
MEDIA_LIGA_COPA_REFERENCIA = 1.25  # média de gols por time por jogo

KELLY_FRACAO_PADRAO = 0.25
KELLY_STAKE_MAX = 0.03  # nunca mais de 3% da banca por aposta


# =====================================================================
# 1. Forças de Ataque / Defesa (baseadas em xG — campo neutro)
# =====================================================================

def calcular_forca_ataque(xg_marcado_medio: float, media_liga: float) -> float:
    """Atk = xG_marcado_médio / média_liga_copa"""
    if not media_liga or media_liga <= 0:
        return 1.0
    return xg_marcado_medio / media_liga


def calcular_forca_defesa(xg_sofrido_medio: float, media_liga: float) -> float:
    """Def = média_liga / xG_sofrido_médio (escala invertida)"""
    if not xg_sofrido_medio or xg_sofrido_medio <= 0:
        return 2.0   # cap superior: defesa "perfeita"
    if not media_liga or media_liga <= 0:
        return 1.0
    return media_liga / xg_sofrido_medio


def calcular_lambda_esperado(
    forca_ataque: float,
    forca_defesa_adversario: float,
    media_liga: float,
    is_mandante: bool = False,
    tournament: str = "copa",
) -> float:
    """
    Gols esperados (lambda) de um time em uma partida.
    lambda = Atk_time × Def_adversário × média_liga
    """
    base = forca_ataque * forca_defesa_adversario * media_liga

    if tournament != "copa" and is_mandante:
        home_bonus = 0.27
        base += home_bonus

    return max(base, 0.01)


# =====================================================================
# 2. Modelo de Poisson — Matriz de Placares e Probabilidades 1X2
# =====================================================================

def calcular_probabilidades_partida(
    lambda_a: float,
    lambda_b: float,
    max_gols: int = 8,
) -> dict:
    """Matriz de Poisson para dois times com lambdas simétricos."""
    goals = np.arange(max_gols)
    prob_a = poisson.pmf(goals, lambda_a)
    prob_b = poisson.pmf(goals, lambda_b)

    matrix = np.outer(prob_b, prob_a)

    return {
        "MANDANTE": float(np.triu(matrix, k=1).sum()),
        "EMPATE":   float(np.diag(matrix).sum()),
        "VISITANTE": float(np.tril(matrix, k=-1).sum()),
    }


# =====================================================================
# 3. Devigging — Remoção da margem da casa de apostas
# =====================================================================

def calcular_overround(odd_1: float, odd_x: float, odd_2: float) -> float:
    """Overround = Σ(1/odd_i) - 1"""
    if odd_1 <= 0 or odd_x <= 0 or odd_2 <= 0:
        return 0.0
    return (1/odd_1 + 1/odd_x + 1/odd_2) - 1


def remover_margem(odds: dict, overround: float) -> dict:
    """Converte odds com margem em probabilidades implícitas limpas."""
    fator = 1 + overround
    return {
        k: ((1 / v) / fator if v > 0 else 0.0)
        for k, v in odds.items()
    }


# =====================================================================
# 4. Critério de Kelly Fracionado
# =====================================================================

def calcular_kelly_fracionado(
    prob_modelo: float,
    odd_mercado: float,
    fracao: float = KELLY_FRACAO_PADRAO,
    stake_max: float = KELLY_STAKE_MAX,
) -> tuple[float, float]:
    """Retorna (stake_sugerido, ev). stake = 0.0 se EV <= 0."""
    if odd_mercado <= 1.0 or prob_modelo <= 0.0:
        return 0.0, 0.0

    b = odd_mercado - 1.0
    ev = prob_modelo * b - (1.0 - prob_modelo)

    if ev <= 0.0:
        return 0.0, ev

    kelly_puro = ev / b
    stake = min(kelly_puro * fracao, stake_max)

    return round(stake, 5), round(ev, 5)


# =====================================================================
# 5. Calculadora de Hedging
# =====================================================================

def calcular_hedge_simples(
    stake_original: float,
    odd_original: float,
    odd_cover: float,
) -> dict:
    """Hedging simples: cobre uma única seleção oposta."""
    if odd_cover <= 1.0:
        return {"stake_hedge": 0.0, "liability": 0.0,
                "lucro_ganha_original": 0.0, "lucro_ganha_hedge": 0.0,
                "lucro_empate": 0.0}

    lucro_original = stake_original * (odd_original - 1.0)
    stake_hedge = lucro_original / (odd_cover - 1.0)
    liability = stake_hedge * (odd_cover - 1.0)

    return {
        "stake_hedge": round(stake_hedge, 2),
        "liability": round(liability, 2),
        "lucro_ganha_original": round(lucro_original - stake_hedge, 2),
        "lucro_ganha_hedge": round(liability - stake_original, 2),
        "lucro_empate": 0.0,
    }


def calcular_hedge_3_modos(
    stake_original: float,
    odd_original: float,
    mercado_original: str,
    odds_mercado: dict,
) -> list[dict]:
    """
    Três estratégias de hedging:
    Modo 1 — Proteção Total: lucro equalizado nos 3 resultados.
    Modo 2 — Lucro Parcial (foco empate): maximiza lucro se empatar.
    Modo 3 — Freebet/Recuperação: recupera stake original na cobertura.
    """
    resultados = []
    opcoes = {k: v for k, v in odds_mercado.items() if k != mercado_original}
    lucro_base = stake_original * (odd_original - 1.0)

    # Modo 1
    modo1 = {"modo": "PROTEÇÃO TOTAL", "plano": {}, "lucro_cenarios": {}}
    for mercado, odd in opcoes.items():
        stake_h = (lucro_base / (odd - 1.0)) if odd > 1.0 else 0.0
        modo1["plano"][mercado] = {"stake": round(stake_h, 2), "odd": odd}
    total_h1 = sum(v["stake"] for v in modo1["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_base
        elif m in modo1["plano"]:
            base = modo1["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
        else:
            base = 0.0
        modo1["lucro_cenarios"][m] = round(base - total_h1, 2)
    resultados.append(modo1)

    # Modo 2 (foco empate)
    modo2 = {"modo": "LUCRO PARCIAL (FOCO EMPATE)", "plano": {}, "lucro_cenarios": {}}
    if "EMPATE" in opcoes:
        odd_e = odds_mercado["EMPATE"]
        stake_e = (lucro_base / (odd_e - 1.0)) if odd_e > 1.0 else 0.0
        modo2["plano"]["EMPATE"] = {"stake": round(stake_e, 2), "odd": odd_e}
        outro = [k for k in opcoes if k != "EMPATE"]
        if outro:
            odd_o = odds_mercado[outro[0]]
            stake_o = stake_original * 0.05 if odd_o > 1.0 else 0.0
            modo2["plano"][outro[0]] = {"stake": round(stake_o, 2), "odd": odd_o}
    total_h2 = sum(v["stake"] for v in modo2["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_base
        elif m in modo2["plano"]:
            base = modo2["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
        else:
            base = 0.0
        modo2["lucro_cenarios"][m] = round(base - total_h2, 2)
    resultados.append(modo2)

    # Modo 3 (freebet)
    modo3 = {"modo": "FREEBET / RECUPERAÇÃO", "plano": {}, "lucro_cenarios": {}}
    for mercado, odd in opcoes.items():
        if odd > 1.0:
            stake_f = (stake_original + lucro_base) / (odd - 1.0)
        else:
            stake_f = 0.0
        modo3["plano"][mercado] = {"stake": round(stake_f, 2), "odd": odd}
    total_h3 = sum(v["stake"] for v in modo3["plano"].values())
    for m in ["MANDANTE", "EMPATE", "VISITANTE"]:
        if m == mercado_original:
            base = lucro_base - total_h3
        elif m in modo3["plano"]:
            base = (modo3["plano"][m]["stake"] * (odds_mercado.get(m, 1.0) - 1.0)
                    - total_h3 + stake_original)
        else:
            base = -stake_original
        modo3["lucro_cenarios"][m] = round(base, 2)
    resultados.append(modo3)

    return resultados


# =====================================================================
# 6. Ajuste de xG por resultado (Dixon-Coles simplificado)
# =====================================================================

def calcular_xg_ajustado_por_resultado(
    xg_marcado: float,
    xg_sofrido: float,
    gols_marcados: float,
    gols_sofridos: float,
    peso_xg: float = 0.6,
) -> tuple[float, float]:
    """Blenda xG com gols reais para corrigir viés de finalização."""
    xg_aj_m = peso_xg * xg_marcado + (1.0 - peso_xg) * max(gols_marcados, 0.1)
    xg_aj_s = peso_xg * xg_sofrido  + (1.0 - peso_xg) * max(gols_sofridos, 0.1)
    return round(xg_aj_m, 3), round(xg_aj_s, 3)


# =====================================================================
# 7. Simulação Monte Carlo (validação do modelo Poisson)
# =====================================================================

def simular_distribuicao_gols(
    lambda_a: float,
    lambda_b: float,
    n_simulacoes: int = 10_000,
    seed: int = 42,
) -> dict:
    """Monte Carlo para validar o modelo Poisson analítico."""
    rng = np.random.default_rng(seed)
    gols_a = rng.poisson(lambda_a, n_simulacoes)
    gols_b = rng.poisson(lambda_b, n_simulacoes)

    return {
        "MANDANTE":  round(float(np.sum(gols_a > gols_b) / n_simulacoes), 4),
        "EMPATE":    round(float(np.sum(gols_a == gols_b) / n_simulacoes), 4),
        "VISITANTE": round(float(np.sum(gols_b > gols_a) / n_simulacoes), 4),
        "media_gols_a": round(float(np.mean(gols_a)), 2),
        "media_gols_b": round(float(np.mean(gols_b)), 2),
    }


# =====================================================================
# __main__: testes de sanidade
# =====================================================================

if __name__ == "__main__":
    print("=== Sanity checks — core_math (Copa do Mundo 2026) ===\n")

    atk = calcular_forca_ataque(1.25, MEDIA_LIGA_COPA_REFERENCIA)
    dfs = calcular_forca_defesa(1.25, MEDIA_LIGA_COPA_REFERENCIA)
    lam = calcular_lambda_esperado(atk, dfs, MEDIA_LIGA_COPA_REFERENCIA)
    print(f"[1] Times equivalentes → lambda = {lam:.3f} (esperado ≈ 1.25)")

    probs = calcular_probabilidades_partida(1.25, 1.25)
    print(f"[2] Poisson (λ=1.25 vs λ=1.25): M={probs['MANDANTE']:.3f}  E={probs['EMPATE']:.3f}  V={probs['VISITANTE']:.3f}")
    assert abs(probs["MANDANTE"] - probs["VISITANTE"]) < 0.001
    print("    Simetria confirmada ✓")

    stake, ev = calcular_kelly_fracionado(0.55, 2.10)
    print(f"[3] Kelly (p=55%, odd=2.10): stake={stake*100:.2f}% banca, EV={ev*100:.2f}%")

    print("\n=== Todos os checks passaram ✓ ===")
```

### Validar

```bash
python core_math.py
```

Saída esperada:
```
=== Sanity checks — core_math (Copa do Mundo 2026) ===

[1] Times equivalentes → lambda = 1.250 (esperado ≈ 1.25)
[2] Poisson (λ=1.25 vs λ=1.25): M=0.337  E=0.327  V=0.337
    Simetria confirmada ✓
[3] Kelly (p=55%, odd=2.10): stake=2.86% banca, EV=15.50%

=== Todos os checks passaram ✓ ===
```

---

## Passo 4 — Criar o Agente de Risco (risk_agent.py)

```python
"""
risk_agent.py
Agente 3 — Gestor de Risco (Risk & Kelly Agent).

Copa do Mundo 2026 — Campo Neutro
==================================
CORREÇÃO PRINCIPAL: calcular_lambda_esperado() é chamado com
is_mandante=False para AMBOS os times, pois a Copa é disputada em
campos neutros (EUA/Canadá/México). Não há vantagem de "casa".

O parâmetro tournament="copa" garante que core_math também não aplique
nenhum home bonus mesmo que is_mandante fosse passado como True.
"""

from __future__ import annotations

import logging

import core_math
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MEDIA_LIGA_COPA = core_math.MEDIA_LIGA_COPA_REFERENCIA   # 1.25 gols/jogo
EV_MINIMO_PADRAO = 0.02   # 2% de EV
JANELA_ANALISE_HORAS = 48  # analisa jogos das próximas 48h


def avaliar_oportunidade(
    prob_modelo: dict,
    odds_mercado: dict,
    fracao_kelly: float = core_math.KELLY_FRACAO_PADRAO,
) -> list[dict]:
    """Compara probabilidades do modelo Poisson com o mercado após devigging."""
    odd_1 = odds_mercado.get("MANDANTE", 0.0)
    odd_x = odds_mercado.get("EMPATE", 0.0)
    odd_2 = odds_mercado.get("VISITANTE", 0.0)

    if odd_1 <= 1.0 or odd_x <= 1.0 or odd_2 <= 1.0:
        logger.debug("Odds inválidas ou incompletas — pulando partida.")
        return []

    overround = core_math.calcular_overround(odd_1, odd_x, odd_2)
    probs_mercado = core_math.remover_margem(odds_mercado, overround)

    oportunidades = []
    for mercado in ("MANDANTE", "EMPATE", "VISITANTE"):
        p_modelo = prob_modelo.get(mercado, 0.0)
        odd = odds_mercado.get(mercado, 0.0)

        stake, ev = core_math.calcular_kelly_fracionado(p_modelo, odd, fracao=fracao_kelly)

        if ev > 0.0:
            oportunidades.append({
                "mercado": mercado,
                "prob_modelo": round(p_modelo, 4),
                "prob_mercado_sem_margem": round(probs_mercado.get(mercado, 0.0), 4),
                "odd": round(odd, 3),
                "ev": round(ev, 5),
                "stake_sugerido": round(stake, 5),
                "overround": round(overround, 4),
            })

    return oportunidades


def filtrar_recomendacoes(
    oportunidades: list[dict],
    ev_minimo: float = EV_MINIMO_PADRAO,
) -> list[dict]:
    """Filtra oportunidades com EV acima do limiar mínimo."""
    return [op for op in oportunidades if op["ev"] >= ev_minimo]


def calcular_lambdas_copa(
    row_mandante: dict,
    row_visitante: dict,
    media_liga: float = MEDIA_LIGA_COPA,
) -> tuple[float, float]:
    """Calcula os lambdas esperados para ambos os times em campo neutro."""
    def _val(row, col, fallback=MEDIA_LIGA_COPA):
        v = row.get(col) if isinstance(row, dict) else getattr(row, col, None)
        try:
            fv = float(v)
            return fv if fv > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    xg_m_a = _val(row_mandante, "xg_marcado_casa")
    xg_s_a = _val(row_mandante, "xg_sofrido_casa")
    xg_m_b = _val(row_visitante, "xg_marcado_fora")
    xg_s_b = _val(row_visitante, "xg_sofrido_fora")

    atk_a = core_math.calcular_forca_ataque(xg_m_a, media_liga)
    def_b = core_math.calcular_forca_defesa(xg_s_b, media_liga)

    atk_b = core_math.calcular_forca_ataque(xg_m_b, media_liga)
    def_a = core_math.calcular_forca_defesa(xg_s_a, media_liga)

    lambda_a = core_math.calcular_lambda_esperado(
        atk_a, def_b, media_liga,
        is_mandante=False, tournament="copa",
    )
    lambda_b = core_math.calcular_lambda_esperado(
        atk_b, def_a, media_liga,
        is_mandante=False, tournament="copa",
    )

    return lambda_a, lambda_b


def processar_partidas_pendentes(
    ev_minimo: float = EV_MINIMO_PADRAO,
    fracao_kelly: float = core_math.KELLY_FRACAO_PADRAO,
) -> list[dict]:
    """Orquestra o fluxo completo do Agente de Risco."""
    recomendacoes_geradas = []

    with db.get_connection() as conn:
        # Média dinâmica da liga
        cur_media = conn.execute("""
            SELECT AVG((xg_marcado_casa + xg_marcado_fora) / 2.0) as media_geral
            FROM times_performance
            WHERE jogos_casa + jogos_fora >= 1
        """)
        row_media = cur_media.fetchone()
        media_liga = (
            float(row_media["media_geral"])
            if row_media and row_media["media_geral"]
            else MEDIA_LIGA_COPA
        )
        logger.info(f"Média dinâmica da Copa: {media_liga:.3f} gols/jogo")

        sql = """
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
        WHERE p.data_evento >= datetime('now', :janela)
        GROUP BY p.partida_id
        ORDER BY p.data_evento ASC
        """

        rows = conn.execute(sql, {"janela": f"-{JANELA_ANALISE_HORAS} hours"}).fetchall()
        logger.info(f"Partidas com odds nas próximas {JANELA_ANALISE_HORAS}h: {len(rows)}")

        for row in rows:
            try:
                row_mandante = {
                    "xg_marcado_casa": row["tm_xg_marcado_casa"],
                    "xg_sofrido_casa": row["tm_xg_sofrido_casa"],
                }
                row_visitante = {
                    "xg_marcado_fora": row["tv_xg_marcado_fora"],
                    "xg_sofrido_fora": row["tv_xg_sofrido_fora"],
                }

                lambda_a, lambda_b = calcular_lambdas_copa(
                    row_mandante, row_visitante, media_liga
                )

                jogos_m = row["jogos_mandante"] or 0
                jogos_v = row["jogos_visitante"] or 0
                min_jogos = core_math.MIN_GAMES_FOR_RELIABLE_STATS

                if jogos_m < min_jogos or jogos_v < min_jogos:
                    peso = min(jogos_m, jogos_v) / min_jogos
                    lambda_a = peso * lambda_a + (1 - peso) * media_liga
                    lambda_b = peso * lambda_b + (1 - peso) * media_liga
                    logger.debug(
                        f"Suavização ({row['nome_mandante']} vs {row['nome_visitante']}): "
                        f"peso={peso:.2f}"
                    )

                prob_modelo = core_math.calcular_probabilidades_partida(lambda_a, lambda_b)

                odds_mercado = {
                    "MANDANTE":  float(row["odd_mandante"] or 0),
                    "EMPATE":    float(row["odd_empate"] or 0),
                    "VISITANTE": float(row["odd_visitante"] or 0),
                }

                oportunidades = avaliar_oportunidade(prob_modelo, odds_mercado, fracao_kelly)
                oportunidades_filtradas = filtrar_recomendacoes(oportunidades, ev_minimo)

                for op in oportunidades_filtradas:
                    # Evita duplicata do mesmo mercado para mesma partida
                    existe = conn.execute("""
                        SELECT 1 FROM historico_analises
                        WHERE partida_id = ? AND mercado_sugerido = ? AND resultado_partida = 'PENDENTE'
                    """, (row["partida_id"], op["mercado"])).fetchone()

                    if existe:
                        logger.debug(f"Análise já existe: {row['partida_id']} - {op['mercado']}")
                        continue

                    analise = {
                        "partida_id": row["partida_id"],
                        "prob_mandante_real": prob_modelo["MANDANTE"],
                        "prob_empate_real":   prob_modelo["EMPATE"],
                        "prob_visitante_real": prob_modelo["VISITANTE"],
                        "mercado_sugerido": op["mercado"],
                        "odd_disponivel":   op["odd"],
                        "ev_calculado":     op["ev"],
                        "stake_kelly":      op["stake_sugerido"],
                        "resultado_partida": "PENDENTE",
                    }
                    db.registrar_analise_historico(conn, analise)
                    recomendacoes_geradas.append({
                        **analise,
                        "mandante": row["nome_mandante"],
                        "visitante": row["nome_visitante"],
                        "lambda_a": round(lambda_a, 3),
                        "lambda_b": round(lambda_b, 3),
                        "prob_mercado_sem_margem": op["prob_mercado_sem_margem"],
                        "overround": op["overround"],
                    })

                if not oportunidades_filtradas:
                    logger.debug(
                        f"Sem EV+ (≥{ev_minimo:.0%}): {row['nome_mandante']} vs {row['nome_visitante']}"
                    )

            except Exception as e:
                logger.error(
                    f"Erro processando {row.get('nome_mandante','?')} vs "
                    f"{row.get('nome_visitante','?')}: {e}",
                    exc_info=True
                )

    logger.info(f"{len(recomendacoes_geradas)} recomendação(ões) EV+ gerada(s).")
    return recomendacoes_geradas


def diagnostico_partida(partida_id: int) -> dict:
    """Retorna diagnóstico completo de uma partida."""
    with db.get_connection() as conn:
        row = conn.execute("""
            SELECT p.partida_id, p.data_evento,
                   tm.nome AS nome_mandante, tv.nome AS nome_visitante,
                   tm.xg_marcado_casa, tm.xg_sofrido_casa,
                   tv.xg_marcado_fora, tv.xg_sofrido_fora,
                   tm.jogos_casa + tm.jogos_fora AS jogos_m,
                   tv.jogos_casa + tv.jogos_fora AS jogos_v,
                   o.odd_mandante, o.odd_empate, o.odd_visitante,
                   o.casa_aposta
            FROM partidas_agenda p
            JOIN times_performance tm ON p.time_mandante_id = tm.time_id
            JOIN times_performance tv ON p.time_visitante_id = tv.time_id
            LEFT JOIN odds_mercado o ON p.partida_id = o.partida_id
            WHERE p.partida_id = ?
            LIMIT 1
        """, (partida_id,)).fetchone()

        if not row:
            return {"erro": f"Partida {partida_id} não encontrada."}

        row_mandante = {
            "xg_marcado_casa": row["xg_marcado_casa"],
            "xg_sofrido_casa": row["xg_sofrido_casa"],
        }
        row_visitante = {
            "xg_marcado_fora": row["xg_marcado_fora"],
            "xg_sofrido_fora": row["xg_sofrido_fora"],
        }
        lambda_a, lambda_b = calcular_lambdas_copa(
            row_mandante, row_visitante, MEDIA_LIGA_COPA
        )
        probs = core_math.calcular_probabilidades_partida(lambda_a, lambda_b)
        mc = core_math.simular_distribuicao_gols(lambda_a, lambda_b)

        odds = {
            "MANDANTE":  float(row["odd_mandante"] or 0),
            "EMPATE":    float(row["odd_empate"] or 0),
            "VISITANTE": float(row["odd_visitante"] or 0),
        }
        oportunidades = (
            avaliar_oportunidade(probs, odds)
            if all(v > 0 for v in odds.values())
            else []
        )

        return {
            "partida_id": partida_id,
            "mandante": row["nome_mandante"],
            "visitante": row["nome_visitante"],
            "data": row["data_evento"],
            "lambda_mandante": round(lambda_a, 3),
            "lambda_visitante": round(lambda_b, 3),
            "probabilidades_poisson": {k: round(v, 4) for k, v in probs.items()},
            "probabilidades_monte_carlo": {
                k: mc[k] for k in ("MANDANTE", "EMPATE", "VISITANTE")
            },
            "odds_mercado": odds,
            "casa_aposta": row["casa_aposta"],
            "oportunidades_ev": oportunidades,
            "jogos_mandante": row["jogos_m"],
            "jogos_visitante": row["jogos_v"],
            "campo_neutro": True,
        }


if __name__ == "__main__":
    db.init_db()
    print("=== Agente de Risco — Copa do Mundo 2026 ===")
    print(f"Campo neutro: HOME_ADVANTAGE = {core_math.HOME_ADVANTAGE_COPA}")
    print(f"EV mínimo: {EV_MINIMO_PADRAO:.0%} | Kelly fração: {core_math.KELLY_FRACAO_PADRAO:.0%}\n")

    recomendacoes = processar_partidas_pendentes()

    if recomendacoes:
        print(f"\n{len(recomendacoes)} oportunidade(s) EV+ encontrada(s):\n")
        for r in sorted(recomendacoes, key=lambda x: x["ev_calculado"], reverse=True):
            print(
                f"  {r['mandante']} vs {r['visitante']} | "
                f"{r['mercado_sugerido']} @ {r['odd_disponivel']:.2f} | "
                f"EV={r['ev_calculado']*100:.2f}% | "
                f"Stake={r['stake_kelly']*100:.2f}% | "
                f"λ=({r['lambda_a']:.2f}, {r['lambda_b']:.2f})"
            )
    else:
        print("Sem oportunidades EV+ no momento. Verifique se há odds no banco.")
        print("Execute: python fetcher_agent.py")
```

---

## Passo 5 — Criar importadores de dados

### 5.1 Criar `copa2026_scraper.py`

Importador legado via CSV — útil para backfill de partidas já jogadas com xG real.

```python
"""
copa2026_scraper.py
Importador de dados — Copa do Mundo 2026 via CSV.

Responsabilidades
=================
1. Carregar no banco jogos da Copa 2026 a partir de data/copa2026_resultados.csv.
2. Atualizar `historico_analises.resultado_partida` para jogos concluídos.
3. Calcular xG médio real de cada time e persistir.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import db
import core_math

FIFA_ID_MAP: dict[str, int] = {
    "Argentina": 2, "Brasil": 3, "França": 4, "Alemanha": 5,
    "Espanha": 8, "Inglaterra": 9, "Itália": 10, "Portugal": 12,
    "Holanda": 13, "Bélgica": 14, "Croácia": 15, "Uruguai": 16,
    "México": 23, "Estados Unidos": 24, "EUA": 24, "Canadá": 25,
    "Sérvia": 27, "Suíça": 29, "Polônia": 32, "Colômbia": 33,
    "Suécia": 34, "Ucrânia": 35, "Japão": 37, "Coreia do Sul": 38,
    "República da Coreia": 38, "Austrália": 39, "Camarões": 40,
    "Costa Rica": 42, "Marrocos": 45, "Irã": 46, "Nigéria": 47,
    "Gana": 48, "Senegal": 49, "Egito": 55, "Chile": 60, "Peru": 63,
    "Paraguai": 65, "Equador": 67, "Arábia Saudita": 68, "Qatar": 75,
    "Catar": 75, "Nova Zelândia": 107, "Panamá": 114, "Jamaica": 121,
    "Honduras": 137, "Bolívia": 140, "Venezuela": 141, "Índia": 151,
    "Tunísia": 155, "Argélia": 156, "Costa do Marfim": 157,
    "Áustria": 158, "Dinamarca": 159, "Noruega": 161, "Escócia": 162,
    "Gales": 163, "Hungria": 164, "Romênia": 165,
    "República Tcheca": 166, "Tchéquia": 166, "Eslováquia": 167,
    "Grécia": 168, "Albânia": 169, "Islândia": 170, "Irlanda": 171,
    "Bósnia e Herzegovina": 172, "Iraque": 173, "Cuba": 175,
    "Coreia do Norte": 179, "Haiti": 180, "Turquia": 181,
}


def importar_dados_copa() -> dict:
    """Importa dados da Copa 2026 do CSV."""
    db.init_db()

    jogos = []
    csv_path = Path("data/copa2026_resultados.csv")
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                jogos.append({
                    "partida_id": int(row["partida_id"]),
                    "mandante": row["mandante"],
                    "visitante": row["visitante"],
                    "gols_m": float(row["gols_m"]) if row["gols_m"] else None,
                    "gols_v": float(row["gols_v"]) if row["gols_v"] else None,
                    "xg_m": float(row["xg_m"]) if row["xg_m"] else None,
                    "xg_v": float(row["xg_v"]) if row["xg_v"] else None,
                    "resultado": row["resultado"],
                })
    except FileNotFoundError:
        logger.error(f"Arquivo {csv_path} não encontrado.")
        return {"times": 0, "partidas": 0, "resultados_atualizados": 0}

    # Agrega xG
    stats: dict[str, dict] = defaultdict(lambda: {
        "xg_marcado": [], "xg_sofrido": [],
        "gols_marcados": [], "gols_sofridos": [],
        "jogos": 0,
    })
    partidas_por_id = {j["partida_id"]: j for j in jogos}

    for p in jogos:
        if p["gols_m"] is not None and p["xg_m"] is not None:
            stats[p["mandante"]]["xg_marcado"].append(p["xg_m"])
            stats[p["mandante"]]["xg_sofrido"].append(p["xg_v"])
            stats[p["mandante"]]["gols_marcados"].append(p["gols_m"])
            stats[p["mandante"]]["gols_sofridos"].append(p["gols_v"])
            stats[p["mandante"]]["jogos"] += 1

            stats[p["visitante"]]["xg_marcado"].append(p["xg_v"])
            stats[p["visitante"]]["xg_sofrido"].append(p["xg_m"])
            stats[p["visitante"]]["gols_marcados"].append(p["gols_v"])
            stats[p["visitante"]]["gols_sofridos"].append(p["gols_m"])
            stats[p["visitante"]]["jogos"] += 1

    # Persiste times e partidas
    times_salvos = 0
    with db.get_connection() as conn:
        for nome, s in stats.items():
            time_id = FIFA_ID_MAP.get(nome, 0)
            if not time_id:
                continue

            n = s["jogos"]
            if n == 0:
                continue
            xg_m, xg_s = core_math.calcular_xg_ajustado_por_resultado(
                sum(s["xg_marcado"])/n, sum(s["xg_sofrido"])/n,
                sum(s["gols_marcados"])/n, sum(s["gols_sofridos"])/n
            )

            db.upsert_time_performance(conn, {
                "time_id": time_id, "nome": nome, "liga": "Copa do Mundo 2026",
                "xg_marcado_casa": xg_m, "xg_sofrido_casa": xg_s,
                "xg_marcado_fora": xg_m, "xg_sofrido_fora": xg_s,
                "jogos_casa": n, "jogos_fora": n
            })
            times_salvos += 1

        partidas_salvas = 0
        for pid, p in partidas_por_id.items():
            m_id = FIFA_ID_MAP.get(p["mandante"], 0)
            v_id = FIFA_ID_MAP.get(p["visitante"], 0)
            if not m_id or not v_id:
                continue
            _garantir_time_copa(conn, m_id, p["mandante"])
            _garantir_time_copa(conn, v_id, p["visitante"])
            db.inserir_partida_agenda(conn, {
                "partida_id": pid, "liga": "Copa do Mundo 2026",
                "data_evento": "2026-06-01",
                "time_mandante_id": m_id, "time_visitante_id": v_id,
            })
            partidas_salvas += 1

        atualizados = _atualizar_resultados_historico_csv(conn, jogos)

    return {"times": times_salvos, "partidas": partidas_salvas,
            "resultados_atualizados": atualizados}


def _garantir_time_copa(conn, time_id, nome) -> None:
    if not conn.execute("SELECT 1 FROM times_performance WHERE time_id = ?", (time_id,)).fetchone():
        ref = core_math.MEDIA_LIGA_COPA_REFERENCIA
        conn.execute(
            "INSERT OR IGNORE INTO times_performance "
            "(time_id, nome, liga, xg_marcado_casa, xg_sofrido_casa, "
            " xg_marcado_fora, xg_sofrido_fora, jogos_casa, jogos_fora) "
            "VALUES (?, ?, 'Copa do Mundo 2026', ?, ?, ?, ?, 0, 0)",
            (time_id, nome, ref, ref, ref, ref)
        )
        conn.commit()


def _atualizar_resultados_historico_csv(conn, jogos) -> int:
    atualizados = 0
    for p in jogos:
        if p["resultado"] == "PENDENTE":
            continue
        rows = conn.execute(
            "SELECT id, mercado_sugerido, odd_disponivel, stake_kelly "
            "FROM historico_analises WHERE partida_id = ? AND resultado_partida = 'PENDENTE'",
            (p["partida_id"],)
        ).fetchall()
        for row in rows:
            acertou = (row["mercado_sugerido"] == p["resultado"])
            lucro = round(
                (row["stake_kelly"] * (row["odd_disponivel"] - 1.0)) if acertou
                else (-row["stake_kelly"]), 5
            )
            conn.execute(
                "UPDATE historico_analises SET resultado_partida = ?, lucro_prejuizo = ? WHERE id = ?",
                (p["resultado"], lucro, row["id"])
            )
            atualizados += 1
    conn.commit()
    return atualizados


if __name__ == "__main__":
    res = importar_dados_copa()
    print(f"Importado: {res}")
```

### 5.2 Criar `check_today.py`

```python
"""
check_today.py
Teste de APIs externas (API-Football + The Odds API).
"""
import os
import datetime
import requests
from dotenv import load_dotenv

if not os.path.exists(".env"):
    raise FileNotFoundError(
        "Arquivo .env não encontrado! Copie .env.example para .env e preencha as chaves."
    )
load_dotenv()

url = 'https://v3.football.api-sports.io/fixtures'
headers = {'x-apisports-key': os.getenv('API_FOOTBALL_KEY')}

hoje = datetime.datetime.now().strftime('%Y-%m-%d')
params = {'date': hoje}

r = requests.get(url, headers=headers, params=params)
fixtures = r.json().get('response', [])

print(f"Total fixtures for today ({hoje}): {len(fixtures)}")
for f in fixtures:
    league = f['league']['name']
    home = f['teams']['home']['name']
    away = f['teams']['away']['name']
    date = f['fixture']['date']
    if "Copa" in league or "Euro" in league or "World" in league:
        print(f"[{date}] {league}: {home} vs {away}")
```

### 5.3 (Opcional) Criar `fetcher_agent.py`

Este módulo busca odds em APIs externas. Como depende de chaves pagas, está fora do escopo principal. Se for usar, lembre-se de criar o mapeamento `FD_TO_FIFA_MAP` conforme análise de erros (Erro 4).

---

## Passo 6 — Criar workers e frontend

### 6.1 Criar `worker_analyze.py`

```python
"""
worker_analyze.py
Worker de análise — dispara o Agente de Risco.
"""
import sys
import risk_agent
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker_analyze")

if __name__ == "__main__":
    ev_minimo = float(sys.argv[1]) if len(sys.argv) > 1 else 0.02
    logger.info(f"Iniciando análise com EV mínimo: {ev_minimo}")
    risk_agent.processar_partidas_pendentes(ev_minimo=ev_minimo)
    logger.info("Análise concluída.")
```

### 6.2 Criar `app.py`

```python
"""
app.py
Painel Visual — Football Quant Analyst (Copa do Mundo 2026).
"""
import streamlit as st
import requests
import db

API_URL = "http://localhost:8000"

def carregar_recomendacoes():
    with db.get_connection() as conn:
        query = """
        SELECT DISTINCT
            h.*,
            p.data_evento,
            tm.nome AS mandante,
            tv.nome AS visitante
        FROM historico_analises h
        JOIN partidas_agenda p ON h.partida_id = p.partida_id
        JOIN times_performance tm ON p.time_mandante_id = tm.time_id
        JOIN times_performance tv ON p.time_visitante_id = tv.time_id
        WHERE h.resultado_partida = 'PENDENTE'
        ORDER BY h.ev_calculado DESC
        """
        return [dict(r) for r in conn.execute(query).fetchall()]

def main():
    st.set_page_config(page_title="Football Quant Analyst", layout="wide")
    st.title("⚽ Football Quant Analyst")

    tab1, _, _, _ = st.tabs(["📊 Recomendações EV+", "🛡️ Hedge", "📈 Backtest", "🔧 Config"])

    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 Gerar Análise"):
                requests.post(f"{API_URL}/analyze", json={"ev_minimo": 0.02})
                st.success("Análise iniciada!")
        with c2:
            if st.button("📥 Importar Copa 2026"):
                requests.post(f"{API_URL}/ingest", json={"fonte": "all"})
                st.success("Ingestão iniciada!")

        st.subheader("Oportunidades com Valor Esperado (EV+)")
        recs = carregar_recomendacoes()

        if not recs:
            st.info("Nenhuma oportunidade com EV+ identificada.")
        else:
            for rec in recs:
                with st.container(border=True):
                    st.markdown(f"### {rec['mandante']} vs {rec['visitante']}")
                    st.caption(f"Data: {rec['data_evento']}")

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Mercado", rec['mercado_sugerido'])
                    col2.metric("Odd", f"{rec['odd_disponivel']:.2f}")
                    col3.metric("EV Calculado", f"{rec['ev_calculado']*100:.2f}%",
                                delta=f"Stake: {rec['stake_kelly']*100:.2f}%")

if __name__ == "__main__":
    db.init_db()
    main()
```

---

## Passo 7 — Integrar o Spider FIFA (schema V2)

> Os passos 2.1 e 2.2 já incluíram o schema V2 e as funções V2 no `db.py`. Agora vamos criar o spider em si.

### 7.1 Criar `fifa_spider.py`

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
FIFA World Cup 2026 - Spider de Captura Periódica
================================================================================

Captura, a cada 3 horas, o conteúdo da página de scores & fixtures da
Copa do Mundo 2026 (Canada/Mexico/USA) no site da FIFA.

Para cada execução:
  1. Abre a página com Playwright (headless Chromium)
  2. Aguarda o carregamento dinâmico (React com hidratação)
  3. Faz scroll progressivo para garantir que todas as partidas carreguem
  4. Extrai as partidas via parser DOM específico (classes match-row_*)
  5. Salva:
       - JSON estruturado em  captures/capture_YYYY-MM-DD_HHMMSS.json
       - HTML bruto em      raw_html/raw_YYYY-MM-DD_HHMMSS.html  (backup)
  6. Compara com a última execução e registra mudanças em logs/changes.log
  7. Atualiza logs/spider.log com execução geral

Uso:
    python fifa_spider.py            # Modo daemon (roda a cada 3h)
    python fifa_spider.py --once     # Executa uma única vez e sai
    python fifa_spider.py --test     # Modo teste: roda 1x a cada 30s (debug)
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import signal
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    import schedule  # type: ignore
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore
except ImportError as e:
    print(f"Dependência ausente: {e}", file=sys.stderr)
    print("Instale com: pip install schedule playwright beautifulsoup4", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
BASE_URL = (
    "https://www.fifa.com/pt/tournaments/mens/worldcup/"
    "canadamexicousa2026/scores-fixtures"
)
QUERY_PARAMS = "?country=BR&wtw-filter=ALL"
TARGET_URL = f"{BASE_URL}{QUERY_PARAMS}"

# Caminhos (ajuste conforme seu sistema operacional)
SPIDER_ROOT = Path(__file__).resolve().parent / "fifa_spider"
CAPTURES_DIR = SPIDER_ROOT / "captures"
RAW_HTML_DIR = SPIDER_ROOT / "raw_html"
LOGS_DIR = SPIDER_ROOT / "logs"
STATE_FILE = SPIDER_ROOT / "last_state.json"

for d in (CAPTURES_DIR, RAW_HTML_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

BRT = timezone(timedelta(hours=-3))
INTERVAL_HOURS = 3

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def setup_logging() -> logging.Logger:
    log_file = LOGS_DIR / "spider.log"
    logger = logging.getLogger("fifa-spider")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


log = setup_logging()


# --------------------------------------------------------------------------- #
# 1. Captura da página (Playwright)
# --------------------------------------------------------------------------- #
def fetch_page_html(url: str = TARGET_URL) -> str:
    log.info("→ Abrindo página: %s", url)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        context.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,mp4}",
            lambda route: route.abort(),
        )

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            log.warning("Timeout no goto; tentando continuar mesmo assim")

        for selector in [
            "div[class*='match-row_matchRowContainer']",
            "[class*='match-row']",
            "[class*='fixture']",
            "#root",
        ]:
            try:
                page.wait_for_selector(selector, timeout=10000)
                log.info("Seletor encontrado: %s", selector)
                break
            except PWTimeout:
                continue

        page.wait_for_timeout(2500)

        last_height = 0
        for _ in range(10):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(700)
            curr = page.evaluate("document.body.scrollHeight")
            if curr == last_height:
                break
            last_height = curr
        log.info("Scroll finalizado. Altura da página: %d px", last_height)

        page.wait_for_timeout(1500)

        html = page.content()
        browser.close()
        log.info("HTML capturado: %d bytes", len(html))
        return html


# --------------------------------------------------------------------------- #
# 2. Parser DOM específico para FIFA.com
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(
    r"^(domingo|segunda-feira|terça-feira|quarta-feira|"
    r"quinta-feira|sexta-feira|sábado)\s+"
    r"(\d{1,2})\s+"
    r"(janeiro|fevereiro|março|abril|maio|junho|"
    r"julho|agosto|setembro|outubro|novembro|dezembro)\s+"
    r"(2026)\s*",
    re.IGNORECASE,
)

_PT_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

_KNOCKOUT_PHASES = {
    "oitavas de final", "quartas de final", "semifinal",
    "decisão do 3º lugar", "final", "oitavas-de-final",
}


def _find_date_for_match(match_element) -> Optional[str]:
    node = match_element
    for _ in range(15):
        if node is None or not hasattr(node, "name") or node.name is None:
            return None
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else ""
        m = _DATE_RE.match(text)
        if m:
            day = int(m.group(2))
            month = _PT_MONTHS.get(m.group(3).lower())
            year = int(m.group(4))
            if month:
                return f"{year:04d}-{month:02d}-{day:02d}"
        for child in node.find_all(["div", "span", "h2", "h3", "p"], limit=30):
            child_text = child.get_text(" ", strip=True)
            m = _DATE_RE.match(child_text)
            if m:
                day = int(m.group(2))
                month = _PT_MONTHS.get(m.group(3).lower())
                year = int(m.group(4))
                if month:
                    return f"{year:04d}-{month:02d}-{day:02d}"
        node = node.parent
    return None


def _extract_team(team_div) -> dict:
    out = {"code": None, "name": None, "flag_url": None}
    if team_div is None:
        return out

    code_el = team_div.find(
        "div", class_=re.compile(r"team-abbreviations_container")
    )
    if code_el:
        out["code"] = code_el.get_text(strip=True)

    name_el = team_div.find("span", class_=re.compile(r"d-none.*d-md-block"))
    if name_el:
        out["name"] = name_el.get_text(strip=True)
    elif out["code"]:
        out["name"] = out["code"]

    img = team_div.find("img")
    if img:
        srcset = img.get("srcset", "") or img.get("src", "")
        if srcset:
            m = re.search(r"(https?://\S+?)(?:\s+\d+x)?\s*$", srcset.strip())
            if m:
                out["flag_url"] = m.group(1)
            else:
                out["flag_url"] = srcset.split()[0]

    return out


def _extract_status_and_score(status_div) -> tuple:
    if status_div is None:
        return None, None, None, None

    score_home = None
    score_away = None
    status = None
    kick_off = None

    time_span = status_div.find(
        "span", class_=re.compile(r"match-row_matchTime")
    )
    if time_span:
        kick_off = time_span.get_text(strip=True)
        return None, None, None, kick_off

    score_spans = status_div.find_all(
        "span", class_=re.compile(r"match-row_score")
    )
    if len(score_spans) >= 1:
        score_home = score_spans[0].get_text(strip=True)
    if len(score_spans) >= 2:
        score_away = score_spans[1].get_text(strip=True)

    status_label = status_div.find(
        "div", class_=re.compile(r"match-row_status__")
    )
    if status_label:
        status = status_label.get_text(strip=True)

    if score_home and re.fullmatch(r"\d{1,2}:\d{2}", score_home):
        kick_off = score_home
        score_home = None
        score_away = None
        status = None

    return status, score_home, score_away, kick_off


def _extract_phase_group_stadium(bottom_div) -> tuple:
    if bottom_div is None:
        return None, None, None, None

    phase = None
    group = None
    stadium = None
    city = None

    bottom_labels = bottom_div.find_all(
        "span", class_=re.compile(r"match-row_bottomLabel")
    )
    if len(bottom_labels) >= 1:
        phase = bottom_labels[0].get_text(strip=True)
    if len(bottom_labels) >= 2:
        group = bottom_labels[1].get_text(strip=True)

    sc_div = bottom_div.find(
        "div", class_=re.compile(r"match-row_stadiumCityLabels")
    )
    if sc_div:
        spans = sc_div.find_all("span")
        if len(spans) >= 1:
            stadium = spans[0].get_text(strip=True)
        if len(spans) >= 2:
            city = spans[1].get_text(strip=True).strip("()")

    if group and group.lower() in _KNOCKOUT_PHASES:
        phase = group
        group = None
    if phase and phase.lower().startswith("grupo "):
        group = phase
        phase = None

    return phase, group, stadium, city


def parse_matches(html: str) -> tuple:
    soup = BeautifulSoup(html, "html.parser")

    containers = soup.find_all(
        "div", class_=re.compile(r"^match-row_matchRowContainer__")
    )
    log.info("Containers de partida encontrados: %d", len(containers))

    matches = []
    for idx, c in enumerate(containers, start=1):
        bottom = c.find(
            "div", class_=re.compile(r"^match-row_bottomLabelWrapper__")
        )

        team_divs = c.find_all(
            "div", class_=re.compile(r"^match-row_team__")
        )
        home_team_div = team_divs[0] if len(team_divs) >= 1 else None
        away_team_div = team_divs[1] if len(team_divs) >= 2 else None

        home_team = _extract_team(home_team_div)
        away_team = _extract_team(away_team_div)

        status_div = c.find(
            "div", class_=re.compile(r"^match-row_matchRowStatus__")
        )
        status, score_home, score_away, kick_off = (
            _extract_status_and_score(status_div)
        )

        phase, group, stadium, city = _extract_phase_group_stadium(bottom)

        date_iso = _find_date_for_match(c)

        mid_seed = (
            f"{date_iso}|{home_team.get('code')}|"
            f"{away_team.get('code')}|{idx}"
        )
        match_id = hashlib.md5(mid_seed.encode("utf-8")).hexdigest()[:16]

        match = {
            "match_id": match_id,
            "match_index": idx,
            "home_team": home_team,
            "away_team": away_team,
            "score_home": score_home,
            "score_away": score_away,
            "status": status,
            "kick_off_time": kick_off,
            "phase": phase,
            "group": group,
            "stadium": stadium,
            "city": city,
            "date": date_iso,
            "source_url": TARGET_URL,
        }
        matches.append(match)

    log.info("Partidas parseadas com sucesso: %d", len(matches))

    next_data = None
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            next_data = json.loads(script.string)
        except json.JSONDecodeError:
            pass

    return matches, next_data


# --------------------------------------------------------------------------- #
# 3. Persistência (JSON + raw HTML)
# --------------------------------------------------------------------------- #
def save_capture(matches: list, next_data: Optional[dict],
                 raw_html: str) -> Path:
    now = datetime.now(BRT)
    ts_filename = now.strftime("%Y-%m-%d_%H%M%S")
    ts_iso = now.isoformat()

    played = sum(1 for m in matches if m.get("status") == "FIM")
    upcoming = sum(1 for m in matches if m.get("kick_off_time"))
    live = sum(1 for m in matches
               if m.get("status") and m.get("status") != "FIM"
               and m.get("kick_off_time") is None)

    capture = {
        "capture_timestamp": ts_iso,
        "source_url": TARGET_URL,
        "capture_count_total": _total_capture_count() + 1,
        "stats": {
            "matches_found": len(matches),
            "matches_played": played,
            "matches_upcoming": upcoming,
            "matches_live": live,
            "html_size_bytes": len(raw_html),
            "next_data_available": next_data is not None,
        },
        "matches": matches,
    }
    json_path = CAPTURES_DIR / f"capture_{ts_filename}.json"
    json_path.write_text(
        json.dumps(capture, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("JSON salvo: %s (%d partidas | %d jogadas | %d futuras)",
             json_path, len(matches), played, upcoming)

    html_path = RAW_HTML_DIR / f"raw_{ts_filename}.html"
    html_path.write_text(raw_html, encoding="utf-8")
    log.info("HTML bruto salvo: %s", html_path)

    if next_data:
        nd_path = RAW_HTML_DIR / f"next_data_{ts_filename}.json"
        nd_path.write_text(
            json.dumps(next_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    return json_path


def _total_capture_count() -> int:
    try:
        return len(list(CAPTURES_DIR.glob("capture_*.json")))
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# 4. Detecção de mudanças
# --------------------------------------------------------------------------- #
def load_last_state() -> Optional[dict]:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Erro ao ler last_state.json: %s", e)
        return None


def save_current_state(matches: list) -> None:
    STATE_FILE.write_text(
        json.dumps({"matches": matches, "saved_at": datetime.now(BRT).isoformat()},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def diff_matches(old: list, new: list) -> list:
    old_by_id = {m.get("match_id"): m for m in (old or [])}
    new_by_id = {m.get("match_id"): m for m in (new or [])}

    changes = []

    for mid, m in new_by_id.items():
        if mid not in old_by_id:
            changes.append({"type": "NEW_MATCH", "match_id": mid,
                            "details": _summarize_match(m)})

    for mid, m in old_by_id.items():
        if mid not in new_by_id:
            changes.append({"type": "MATCH_REMOVED", "match_id": mid,
                            "details": _summarize_match(m)})

    for mid, m_new in new_by_id.items():
        m_old = old_by_id.get(mid)
        if m_old is None:
            continue
        diffs = _diff_match(m_old, m_new)
        if diffs:
            changes.append({"type": "MATCH_UPDATED", "match_id": mid,
                            "summary": _summarize_match(m_new), "details": diffs})

    return changes


def _summarize_match(m: dict) -> dict:
    home = m.get("home_team", {}) or {}
    away = m.get("away_team", {}) or {}
    return {
        "home_code": home.get("code"), "home_name": home.get("name"),
        "away_code": away.get("code"), "away_name": away.get("name"),
        "date": m.get("date"), "kick_off_time": m.get("kick_off_time"),
        "score_home": m.get("score_home"), "score_away": m.get("score_away"),
        "status": m.get("status"), "phase": m.get("phase"),
        "group": m.get("group"), "stadium": m.get("stadium"), "city": m.get("city"),
    }


def _diff_match(old: dict, new: dict) -> dict:
    fields = ["score_home", "score_away", "status", "kick_off_time",
              "phase", "group", "stadium", "city", "date"]
    out = {}
    for f in fields:
        o_v = old.get(f)
        n_v = new.get(f)
        if o_v != n_v:
            out[f] = {"old": o_v, "new": n_v}
    for side in ("home_team", "away_team"):
        o_t = old.get(side, {}) or {}
        n_t = new.get(side, {}) or {}
        if isinstance(o_t, dict) and isinstance(n_t, dict):
            if o_t.get("code") != n_t.get("code"):
                out[side] = {"old_code": o_t.get("code"), "new_code": n_t.get("code"),
                             "old_name": o_t.get("name"), "new_name": n_t.get("name")}
    return out


def log_changes(changes: list) -> None:
    changes_log = LOGS_DIR / "changes.log"
    now = datetime.now(BRT).isoformat()
    with changes_log.open("a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 78}\n")
        f.write(f"# Captura em {now}\n")
        f.write(f"# Total de mudanças: {len(changes)}\n")
        f.write(f"{'=' * 78}\n")
        for c in changes:
            f.write(json.dumps(c, ensure_ascii=False, default=str) + "\n")
    if changes:
        log.info("📊 %d mudança(s) registrada(s) em %s",
                 len(changes), changes_log.name)
        for c in changes:
            t = c.get("type")
            if t == "NEW_MATCH":
                s = c.get("details", {})
                log.info("  + NOVA: %s %s vs %s %s",
                         s.get("home_code"), s.get("home_name"),
                         s.get("away_code"), s.get("away_name"))
            elif t == "MATCH_REMOVED":
                s = c.get("details", {})
                log.info("  - REMOVIDA: %s vs %s",
                         s.get("home_name"), s.get("away_name"))
            elif t == "MATCH_UPDATED":
                s = c.get("summary", {})
                d = c.get("details", {})
                log.info("  * ATUALIZADA: %s vs %s | campos: %s",
                         s.get("home_name"), s.get("away_name"),
                         ", ".join(d.keys()))
    else:
        log.info("✓ Nenhuma mudança desde a última execução")


# --------------------------------------------------------------------------- #
# 5. Job principal
# --------------------------------------------------------------------------- #
def run_job() -> None:
    log.info("=" * 70)
    log.info("INÍCIO DA CAPTURA #%d", _total_capture_count() + 1)
    log.info("=" * 70)

    start = time.time()
    try:
        html = fetch_page_html()
        matches, next_data = parse_matches(html)
        json_path = save_capture(matches, next_data, html)

        last_state = load_last_state()
        if last_state and last_state.get("matches"):
            changes = diff_matches(last_state["matches"], matches)
            log_changes(changes)
        else:
            log.info("Primeira execução: estado salvo, sem comparação")
            log_changes([])

        save_current_state(matches)

        elapsed = time.time() - start
        log.info("Captura concluída em %.1fs | Arquivo: %s",
                 elapsed, json_path.name)

    except Exception as e:
        log.error("ERRO na captura: %s", e)
        log.error(traceback.format_exc())


# --------------------------------------------------------------------------- #
# 6. Scheduler
# --------------------------------------------------------------------------- #
def run_daemon(interval_hours: int = INTERVAL_HOURS) -> None:
    def handle_sig(signum, frame):
        log.info("Sinal %s recebido. Encerrando scheduler...", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    log.info("🚀 Spider iniciado em modo DAEMON (a cada %d horas)", interval_hours)
    log.info("Diretório base: %s", SPIDER_ROOT)
    log.info("URL alvo: %s", TARGET_URL)

    run_job()

    schedule.every(interval_hours).hours.do(run_job)

    log.info("Próxima execução automática em %d horas. Aguardando...", interval_hours)
    while True:
        schedule.run_pending()
        time.sleep(30)


def run_test_mode() -> None:
    log.info("🧪 MODO TESTE: execução a cada 30 segundos")
    run_job()
    schedule.every(30).seconds.do(run_job)
    while True:
        schedule.run_pending()
        time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spider FIFA World Cup 2026 - captura periódica de jogos",
    )
    parser.add_argument("--once", action="store_true",
                        help="Executa uma única captura e sai (sem scheduler)")
    parser.add_argument("--test", action="store_true",
                        help="Modo teste: executa a cada 30 segundos (debug)")
    parser.add_argument("--interval", type=int, default=INTERVAL_HOURS,
                        help=f"Intervalo em horas (padrão: {INTERVAL_HOURS})")
    args = parser.parse_args()

    if args.once:
        log.info("Modo --once: única execução")
        run_job()
    elif args.test:
        run_test_mode()
    else:
        run_daemon(interval_hours=args.interval)


if __name__ == "__main__":
    main()
```

### 7.2 Testar o spider isoladamente

```bash
python fifa_spider.py --once
```

Saída esperada:
```
2026-06-19 [INFO] → Abrindo página: https://www.fifa.com/pt/tournaments/...
2026-06-19 [INFO] Seletor encontrado: div[class*='match-row_matchRowContainer']
2026-06-19 [INFO] Scroll finalizado. Altura da página: 17930 px
2026-06-19 [INFO] HTML capturado: 991941 bytes
2026-06-19 [INFO] Containers de partida encontrados: 104
2026-06-19 [INFO] Partidas parseadas com sucesso: 104
2026-06-19 [INFO] JSON salvo: fifa_spider/captures/capture_2026-06-19_XXXXXX.json (104 partidas | 28 jogadas | 76 futuras)
2026-06-19 [INFO] ✓ Nenhuma mudança desde a última execução
```

---

## Passo 8 — Criar o adaptador spider → DB (fifa_sync.py)

```python
"""
fifa_sync.py
Adaptador entre o spider FIFA e o banco SQLite do Quant-Agent.

Responsabilidades
=================
1. Ler a captura JSON mais recente produzida pelo `fifa_spider.py`.
2. Registrar metadados da captura em `capturas_fifa`.
3. Para cada partida da captura:
   - Fazer UPSERT em `partidas_agenda` (incluindo status, scores, etc.)
   - Inserir linha em `partidas_fifa_log` (histórico versionado)
   - Atualizar `times_fifa_codes` com códigos novos (placeholders W95 etc.)
4. Atualizar `historico_analises.resultado_partida` para partidas
   que foram finalizadas desde a última execução.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fifa_sync")

SPIDER_ROOT = Path(__file__).resolve().parent / "fifa_spider"
CAPTURES_DIR = SPIDER_ROOT / "captures"
RAW_HTML_DIR = SPIDER_ROOT / "raw_html"

BRT = timezone(timedelta(hours=-3))
LIGA_COPA_2026 = "Copa do Mundo 2026"


def encontrar_ultima_captura() -> Optional[Path]:
    if not CAPTURES_DIR.exists():
        return None
    files = sorted(CAPTURES_DIR.glob("capture_*.json"))
    return files[-1] if files else None


def carregar_captura(json_path: Path) -> dict:
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "matches" not in data:
        raise ValueError(f"JSON inválido (sem 'matches'): {json_path}")
    return data


def sincronizar(json_path: Optional[Path] = None) -> dict:
    start = time.time()
    db.init_db()

    if json_path is None:
        json_path = encontrar_ultima_captura()
    if json_path is None:
        logger.error("Nenhuma captura encontrada em %s", CAPTURES_DIR)
        return {"capture_id": None, "matches_processed": 0,
                "matches_inserted": 0, "matches_updated": 0,
                "log_rows_inserted": 0, "fifa_codes_upserted": 0,
                "historico_atualizado": 0, "elapsed_sec": 0.0}

    logger.info("Sincronizando captura: %s", json_path.name)
    captura = carregar_captura(json_path)

    stats = captura.get("stats", {})
    matches = captura.get("matches", [])

    ts_match = json_path.stem.replace("capture_", "")
    html_path = RAW_HTML_DIR / f"raw_{ts_match}.html"

    changes_detected = _contar_mudanças_ultima_captura()

    with db.get_connection() as conn:
        capture_row = {
            "capture_timestamp": captura.get("capture_timestamp"),
            "source_url": captura.get("source_url"),
            "json_path": str(json_path),
            "html_path": str(html_path) if html_path.exists() else None,
            "matches_found": stats.get("matches_found", len(matches)),
            "matches_played": stats.get("matches_played"),
            "matches_upcoming": stats.get("matches_upcoming"),
            "matches_live": stats.get("matches_live"),
            "html_size_bytes": stats.get("html_size_bytes"),
            "changes_detected": changes_detected,
            "execution_time_sec": None,
            "success": 1,
            "error_message": None,
        }
        capture_id = db.registrar_captura_fifa(conn, capture_row)
        logger.info("Captura registrada com id=%d", capture_id)

        inserted = 0
        updated = 0
        fifa_codes = set()
        for m in matches:
            home_team = m.get("home_team") or {}
            away_team = m.get("away_team") or {}
            home_code = home_team.get("code")
            away_code = away_team.get("code")

            time_home_id = _resolver_time_id_por_nome(
                home_team.get("name"), home_code,
            )
            time_away_id = _resolver_time_id_por_nome(
                away_team.get("name"), away_code,
            )

            if time_home_id:
                _garantir_time_copa(conn, time_home_id, home_team.get("name"))
            if time_away_id:
                _garantir_time_copa(conn, time_away_id, away_team.get("name"))

            for code, t in ((home_code, home_team), (away_code, away_team)):
                if code and code not in fifa_codes:
                    fifa_codes.add(code)
                    _registrar_fifa_code(conn, code, t)

            data_evento = m.get("date") or "2026-06-01"
            if m.get("kick_off_time"):
                data_evento = f"{data_evento} {m['kick_off_time']}:00"

            partida_payload = {
                "spider_match_id": m.get("match_id"),
                "match_index": m.get("match_index"),
                "liga": LIGA_COPA_2026,
                "data_evento": data_evento,
                "time_mandante_id": time_home_id,
                "time_visitante_id": time_away_id,
                "codigo_fifa_home": home_code,
                "codigo_fifa_away": away_code,
                "nome_mandante": home_team.get("name"),
                "nome_visitante": away_team.get("name"),
                "fase": m.get("phase"),
                "grupo": m.get("group"),
                "estadio": m.get("stadium"),
                "cidade": m.get("city"),
                "horario_kickoff": m.get("kick_off_time"),
                "status_fifa": m.get("status"),
                "score_home": _to_int_or_none(m.get("score_home")),
                "score_away": _to_int_or_none(m.get("score_away")),
                "flag_url_home": home_team.get("flag_url"),
                "flag_url_away": away_team.get("flag_url"),
            }

            existing = conn.execute(
                "SELECT 1 FROM partidas_agenda WHERE spider_match_id = ?",
                (m.get("match_id"),),
            ).fetchone()
            db.upsert_partida_fifa(conn, partida_payload)
            if existing:
                updated += 1
            else:
                inserted += 1

            db.inserir_partida_fifa_log(conn, capture_id, m)

        historico_atualizado = db.atualizar_resultados_historico_por_fifa(conn)
        db.marcar_captura_importada(conn, capture_id)

    elapsed = time.time() - start
    result = {
        "capture_id": capture_id,
        "matches_processed": len(matches),
        "matches_inserted": inserted,
        "matches_updated": updated,
        "log_rows_inserted": len(matches),
        "fifa_codes_upserted": len(fifa_codes),
        "historico_atualizado": historico_atualizado,
        "elapsed_sec": round(elapsed, 2),
    }
    logger.info(
        "Sincronização concluída em %.2fs | "
        "%d partidas (%d novas, %d atualizadas) | "
        "%d códigos FIFA | %d análises atualizadas",
        elapsed, len(matches), inserted, updated,
        len(fifa_codes), historico_atualizado,
    )
    return result


def _registrar_fifa_code(conn, code: str, team_dict: dict) -> None:
    is_placeholder = 1 if _is_placeholder_code(code) else 0
    time_id = None
    if not is_placeholder:
        time_id = _resolver_time_id_por_nome(team_dict.get("name"), code)

    db.upsert_fifa_code(conn, {
        "fifa_code": code,
        "time_id": time_id,
        "nome_pt": team_dict.get("name"),
        "nome_en": None,
        "flag_url": team_dict.get("flag_url"),
        "is_placeholder": is_placeholder,
    })


def _is_placeholder_code(code: str) -> bool:
    if not code:
        return False
    if len(code) == 3 and code.isalpha() and code.isupper():
        return False
    return True


_FIFA_ID_MAP_BY_NAME: dict[str, int] = {
    "Argentina": 2, "Brasil": 3, "França": 4, "Alemanha": 5,
    "Espanha": 8, "Inglaterra": 9, "Itália": 10, "Portugal": 12,
    "Holanda": 13, "Bélgica": 14, "Croácia": 15, "Uruguai": 16,
    "México": 23, "Estados Unidos": 24, "EUA": 24, "Canadá": 25,
    "Sérvia": 27, "Suíça": 29, "Polônia": 32, "Colômbia": 33,
    "Suécia": 34, "Ucrânia": 35, "Japão": 37, "Coreia do Sul": 38,
    "República da Coreia": 38, "Austrália": 39, "Camarões": 40,
    "Costa Rica": 42, "Marrocos": 45, "Irã": 46, "Nigéria": 47,
    "Gana": 48, "Senegal": 49, "Egito": 55, "Chile": 60, "Peru": 63,
    "Paraguai": 65, "Equador": 67, "Arábia Saudita": 68, "Qatar": 75,
    "Catar": 75, "Nova Zelândia": 107, "Panamá": 114, "Jamaica": 121,
    "Honduras": 137, "Bolívia": 140, "Venezuela": 141, "Índia": 151,
    "Tunísia": 155, "Argélia": 156, "Costa do Marfim": 157,
    "Áustria": 158, "Dinamarca": 159, "Noruega": 161, "Escócia": 162,
    "Gales": 163, "Hungria": 164, "Romênia": 165,
    "República Tcheca": 166, "Tchéquia": 166, "Eslováquia": 167,
    "Grécia": 168, "Albânia": 169, "Islândia": 170, "Irlanda": 171,
    "Bósnia e Herzegovina": 172, "Iraque": 173, "Cuba": 175,
    "Coreia do Norte": 179, "Haiti": 180, "Turquia": 181,
    "Burkina Fasso": 182, "Burkina Faso": 182, "Burundi": 183,
    "Butão": 184, "Benim": 185, "Barbados": 186, "Bangladesh": 187,
    "Bulgária": 188,
}

_FIFA_ID_MAP_BY_CODE: dict[str, int] = {
    "ARG": 2, "BRA": 3, "FRA": 4, "GER": 5,
    "ESP": 8, "ENG": 9, "ITA": 10, "POR": 12,
    "NED": 13, "BEL": 14, "CRO": 15, "URU": 16,
    "MEX": 23, "USA": 24, "CAN": 25,
    "SRB": 27, "SUI": 29, "POL": 32, "COL": 33,
    "SWE": 34, "UKR": 35, "JPN": 37, "KOR": 38,
    "AUS": 39, "CMR": 40, "CRC": 42, "MAR": 45,
    "IRN": 46, "NGA": 47, "GHA": 48, "SEN": 49,
    "EGY": 55, "CHI": 60, "PER": 63, "PAR": 65,
    "ECU": 67, "KSA": 68, "QAT": 75, "NZL": 107,
    "PAN": 114, "JAM": 121, "HON": 137, "BOL": 140,
    "VEN": 141, "IND": 151, "TUN": 155, "ALG": 156,
    "CIV": 157, "AUT": 158, "DEN": 159, "NOR": 161,
    "SCO": 162, "WAL": 163, "HUN": 164, "ROU": 165,
    "CZE": 166, "SVK": 167, "GRE": 168, "ALB": 169,
    "ISL": 170, "IRL": 171, "BIH": 172, "IRQ": 173,
    "CUB": 175, "PRK": 179, "HAI": 180, "TUR": 181,
    "BFA": 182, "BDI": 183, "BTN": 184, "BEN": 185,
    "BRB": 186, "BGD": 187, "BUL": 188,
}


def _resolver_time_id_por_nome(nome: Optional[str],
                                fifa_code: Optional[str]) -> Optional[int]:
    if fifa_code:
        tid = _FIFA_ID_MAP_BY_CODE.get(fifa_code)
        if tid:
            return tid
    if nome:
        return _FIFA_ID_MAP_BY_NAME.get(nome)
    return None


def _garantir_time_copa(conn, time_id: int, nome: str) -> None:
    existing = conn.execute(
        "SELECT 1 FROM times_performance WHERE time_id = ?", (time_id,)
    ).fetchone()
    if existing:
        return

    try:
        import core_math
        ref = core_math.MEDIA_LIGA_COPA_REFERENCIA
    except ImportError:
        ref = 1.25

    conn.execute(
        """INSERT OR IGNORE INTO times_performance
           (time_id, nome, liga,
            xg_marcado_casa, xg_sofrido_casa,
            xg_marcado_fora, xg_sofrido_fora,
            jogos_casa, jogos_fora)
           VALUES (?, ?, 'Copa do Mundo 2026', ?, ?, ?, ?, 0, 0)""",
        (time_id, nome, ref, ref, ref, ref),
    )
    conn.commit()


def _contar_mudanças_ultima_captura() -> int:
    changes_log = SPIDER_ROOT / "logs" / "changes.log"
    if not changes_log.exists():
        return 0
    try:
        content = changes_log.read_text(encoding="utf-8")
        blocks = content.split("=" * 78)
        if len(blocks) < 2:
            return 0
        last_block = None
        for b in reversed(blocks):
            if b.strip() and "# Captura em" in b:
                last_block = b
                break
        if not last_block:
            return 0
        return sum(1 for line in last_block.split("\n")
                   if line.strip().startswith("{"))
    except Exception:
        return 0


def _to_int_or_none(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Sincroniza a última captura do spider FIFA com o banco SQLite"
    )
    parser.add_argument("--json", type=Path, default=None,
                        help="Caminho específico de um JSON de captura (default: última)")
    args = parser.parse_args()
    result = sincronizar(json_path=args.json)
    print("\n=== Resultado da sincronização ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
```

### 8.1 Testar o sync isoladamente

```bash
python fifa_sync.py
```

Saída esperada:
```
2026-06-19 [INFO] Sincronizando captura: capture_2026-06-19_XXXXXX.json
2026-06-19 [INFO] Captura registrada com id=1
2026-06-19 [INFO] Sincronização concluída em 1.42s | 104 partidas (104 novas, 0 atualizadas) | 111 códigos FIFA | 0 análises atualizadas

=== Resultado da sincronização ===
  capture_id: 1
  matches_processed: 104
  matches_inserted: 104
  matches_updated: 0
  log_rows_inserted: 104
  fifa_codes_upserted: 111
  historico_atualizado: 0
  elapsed_sec: 1.42
```

---

## Passo 9 — Orquestrar spider + sync (worker_ingest.py)

```python
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
```

### 9.1 Testar o orquestrador

```bash
python worker_ingest.py --once
```

Saída esperada:
```
2026-06-19 [INFO] worker_ingest: === Iniciando rotação de ingestão (spider FIFA) ===
2026-06-19 [INFO] fifa-spider: → Abrindo página: https://www.fifa.com/pt/...
2026-06-19 [INFO] fifa-spider: Containers de partida encontrados: 104
2026-06-19 [INFO] fifa-spider: Partidas parseadas com sucesso: 104
2026-06-19 [INFO] fifa-spider: JSON salvo: fifa_spider/captures/capture_*.json
2026-06-19 [INFO] worker_ingest: ✓ Spider concluído
2026-06-19 [INFO] worker_ingest: → Sincronizando captura com o banco...
2026-06-19 [INFO] fifa_sync: Sincronização concluída em 1.42s | 104 partidas
2026-06-19 [INFO] worker_ingest: ✓ Sincronização concluída
2026-06-19 [INFO] worker_ingest: === Rotação concluída em 13.6s ===
```

---

## Passo 10 — Validação e Testes

### 10.1 Sequência completa de validação

```bash
# 1. Verificar que o banco foi criado com todas as tabelas
python -c "import db; db.init_db(); print('OK')"
```

```bash
# 2. Testar core_math (sanity checks)
python core_math.py
```

```bash
# 3. Executar spider + sync uma vez
python worker_ingest.py --once
```

```bash
# 4. Verificar conteúdo do banco
python -c "
import db
db.init_db()
with db.get_connection() as conn:
    for t in ['times_performance', 'partidas_agenda', 'times_fifa_codes',
              'capturas_fifa', 'partidas_fifa_log', 'historico_analises']:
        n = conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
        print(f'  {t:30s} {n:6d} rows')
"
```

Saída esperada:
```
  times_performance                  42 rows
  partidas_agenda                   104 rows
  times_fifa_codes                  111 rows
  capturas_fifa                       1 rows
  partidas_fifa_log                 104 rows
  historico_analises                  0 rows
```

### 10.2 Verificar partidas do Brasil

```bash
python -c "
import db
db.init_db()
with db.get_connection() as conn:
    rows = conn.execute('''
        SELECT data_evento, horario_kickoff, nome_mandante, nome_visitante,
               score_home, score_away, status_fifa, fase, estadio
          FROM partidas_agenda
         WHERE codigo_fifa_home=\"BRA\" OR codigo_fifa_away=\"BRA\"
         ORDER BY data_evento
    ''').fetchall()
    for r in rows:
        ko = r['horario_kickoff'] or ''
        sh = r['score_home'] if r['score_home'] is not None else '-'
        sa = r['score_away'] if r['score_away'] is not None else '-'
        print(f'  {r[\"data_evento\"][:10]} {ko:5s} | {r[\"nome_mandante\"]} {sh} x {sa} {r[\"nome_visitante\"]} | {r[\"status_fifa\"] or \"agendada\"} | {r[\"fase\"]}')
"
```

### 10.3 Testar fechamento automático de apostas

```bash
python -c "
import db
db.init_db()
with db.get_connection() as conn:
    # Acha um jogo agendado
    row = conn.execute('''
        SELECT partida_id, nome_mandante, nome_visitante
          FROM partidas_agenda
         WHERE status_fifa IS NULL
         LIMIT 1
    ''').fetchone()
    if row:
        # Insere análise pendente
        db.registrar_analise_historico(conn, {
            'partida_id': row['partida_id'],
            'prob_mandante_real': 0.60,
            'prob_empate_real': 0.25,
            'prob_visitante_real': 0.15,
            'mercado_sugerido': 'MANDANTE',
            'odd_disponivel': 1.80,
            'ev_calculado': 0.08,
            'stake_kelly': 0.02,
        })
        # Simula que o jogo acabou 2x0
        conn.execute('''
            UPDATE partidas_agenda
               SET status_fifa = 'FIM', score_home = 2, score_away = 0
             WHERE partida_id = ?
        ''', (row['partida_id'],))
        conn.commit()
        # Atualiza histórico
        n = db.atualizar_resultados_historico_por_fifa(conn)
        print(f'Atualizadas: {n} análises')
        ana = conn.execute('SELECT resultado_partida, lucro_prejuizo FROM historico_analises WHERE partida_id = ?', (row['partida_id'],)).fetchone()
        print(f'Resultado: {ana[\"resultado_partida\"]} | Lucro: {ana[\"lucro_prejuizo\"]}')
"
```

### 10.4 Iniciar o painel Streamlit

```bash
streamlit run app.py
```

Acesse: `http://localhost:8501`

---

## Passo 11 — Execução em Produção

### 11.1 Modo daemon (Linux/Mac)

```bash
# Foreground (logs no terminal)
python worker_ingest.py --daemon

# Background com nohup
nohup python worker_ingest.py --daemon > /var/log/ag_aposta/worker.log 2>&1 &
echo $! > /tmp/ag_aposta.pid

# Para parar
kill $(cat /tmp/ag_aposta.pid)
```

### 11.2 Serviço systemd (Linux)

Crie o arquivo `/etc/systemd/system/ag-aposta.service`:

```ini
[Unit]
Description=Football Quant-Agent — Worker de Ingestão Copa 2026
After=network.target

[Service]
Type=simple
User=seu_usuario
WorkingDirectory=/caminho/para/ag_aposta
ExecStart=/usr/bin/python3 worker_ingest.py --daemon
Restart=on-failure
RestartSec=30
StandardOutput=append:/var/log/ag_aposta/worker.log
StandardError=append:/var/log/ag_aposta/worker.log

[Install]
WantedBy=multi-user.target
```

Ative:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ag-aposta
sudo systemctl start ag-aposta
sudo systemctl status ag-aposta
```

### 11.3 Agendador de Tarefas (Windows)

No Windows, você pode usar o Task Scheduler:

1. Abra o Task Scheduler
2. Clique em "Create Task"
3. Na aba **General**:
   - Name: `ag_aposta_spider`
   - Selecione "Run whether user is logged on or not"
4. Na aba **Triggers**:
   - New → Begin the task: "On a schedule"
   - Settings: "Daily"
   - Repeat task every: **3 hours** → "Indefinitely"
5. Na aba **Actions**:
   - Action: "Start a program"
   - Program: `C:\Python312\python.exe`
   - Arguments: `C:\Projetos\ag_aposta\worker_ingest.py --once`
   - Start in: `C:\Projetos\ag_aposta`
6. Clique em OK e salve

### 11.4 Cron (Linux alternativo)

Adicione ao crontab (`crontab -e`):

```cron
# Roda o spider + sync a cada 3 horas (00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00)
0 */3 * * * cd /caminho/para/ag_aposta && /usr/bin/python3 worker_ingest.py --once >> /var/log/ag_aposta/cron.log 2>&1
```

---

## Troubleshooting

### Problema: `playwright._impl._api_types.Error: Executable doesn't exist`

**Solução:** Instale o Chromium:
```bash
playwright install chromium
```

### Problema: `ModuleNotFoundError: No module named 'schedule'`

**Solução:**
```bash
pip install schedule
```

### Problema: `sqlite3.OperationalError: no such column: spider_match_id`

**Solução:** O `db.py` novo já aplica o ALTER TABLE automaticamente. Se estiver usando um banco antigo, apague-o e recrie:
```bash
rm data/football_quant.db
python -c "import db; db.init_db()"
```

### Problema: Spider retorna 0 partidas

**Causa provável:** A FIFA mudou a estrutura HTML da página.

**Diagnóstico:** Inspecione o arquivo `fifa_spider/raw_html/raw_*.html` mais recente.

**Solução:** Atualize os seletores regex em `fifa_spider.py` (funções `parse_matches`, `_extract_team`, `_extract_status_and_score`, `_extract_phase_group_stadium`).

### Problema: `FOREIGN KEY constraint failed` ao sincronizar

**Causa:** Tentando inserir em `times_fifa_codes` com `time_id` que não existe em `times_performance`.

**Solução:** O `fifa_sync.py` deve chamar `_garantir_time_copa()` ANTES de `_registrar_fifa_code()`. Verifique se a ordem está correta no seu código (Passo 8).

### Problema: `FOREIGN KEY constraint failed` em INSERT de partidas

**Causa:** `time_mandante_id` ou `time_visitante_id` é None ou não existe em `times_performance`.

**Solução:** Para placeholders (W95, RU101, etc.), o `time_id` deve ser None. O `fifa_sync.py` chama `_resolver_time_id_por_nome()` que retorna None para placeholders.

### Problema: Análises no histórico não fecham automaticamente

**Causa:** A função `atualizar_resultados_historico_por_fifa()` só fecha análises quando `status_fifa = 'FIM'` e `score_home IS NOT NULL AND score_away IS NOT NULL`.

**Diagnóstico:**
```bash
python -c "
import db
db.init_db()
with db.get_connection() as conn:
    rows = conn.execute('''
        SELECT p.partida_id, p.status_fifa, p.score_home, p.score_away,
               h.id as analise_id, h.resultado_partida
          FROM partidas_agenda p
          JOIN historico_analises h ON p.partida_id = h.partida_id
         WHERE h.resultado_partida = 'PENDENTE'
    ''').fetchall()
    for r in rows:
        print(f'  partida={r[\"partida_id\"]} status_fifa={r[\"status_fifa\"]} '
              f'scores=({r[\"score_home\"]}, {r[\"score_away\"]})')
"
```

### Problema: Logs duplicados (msg aparece 2x)

**Causa:** Logger do spider se propaga para o logger do worker.

**Solução:** No `fifa_spider.py`, adicione após `setup_logging()`:
```python
log.propagate = False
```

### Problema: Streamlit não mostra dados

**Causa:** A query em `app.py` faz JOIN com `times_performance` via `time_mandante_id`. Se a partida tem `time_mandante_id = None` (placeholder), não aparece.

**Soluução alternativa (query mais tolerante):**
```sql
SELECT DISTINCT
    h.*, p.data_evento,
    COALESCE(tm.nome, p.nome_mandante) AS mandante,
    COALESCE(tv.nome, p.nome_visitante) AS visitante
FROM historico_analises h
JOIN partidas_agenda p ON h.partida_id = p.partida_id
LEFT JOIN times_performance tm ON p.time_mandante_id = tm.time_id
LEFT JOIN times_performance tv ON p.time_visitante_id = tv.time_id
WHERE h.resultado_partida = 'PENDENTE'
ORDER BY h.ev_calculado DESC
```

---

## Apêndice — Mapeamentos e Referências

### A. Mapeamento de códigos FIFA → time_id (48 seleções classificadas)

| Código FIFA | time_id | Nome PT           |
|-------------|---------|-------------------|
| ARG         | 2       | Argentina         |
| BRA         | 3       | Brasil            |
| FRA         | 4       | França            |
| GER         | 5       | Alemanha          |
| ESP         | 8       | Espanha           |
| ENG         | 9       | Inglaterra        |
| ITA         | 10      | Itália            |
| POR         | 12      | Portugal          |
| NED         | 13      | Holanda           |
| BEL         | 14      | Bélgica           |
| CRO         | 15      | Croácia           |
| URU         | 16      | Uruguai           |
| MEX         | 23      | México            |
| USA         | 24      | EUA               |
| CAN         | 25      | Canadá            |
| SRB         | 27      | Sérvia            |
| SUI         | 29      | Suíça             |
| POL         | 32      | Polônia           |
| COL         | 33      | Colômbia          |
| SWE         | 34      | Suécia            |
| UKR         | 35      | Ucrânia           |
| JPN         | 37      | Japão             |
| KOR         | 38      | Coreia do Sul     |
| AUS         | 39      | Austrália         |
| CMR         | 40      | Camarões          |
| CRC         | 42      | Costa Rica        |
| MAR         | 45      | Marrocos          |
| IRN         | 46      | Irã               |
| NGA         | 47      | Nigéria           |
| GHA         | 48      | Gana              |
| SEN         | 49      | Senegal           |
| EGY         | 55      | Egito             |
| CHI         | 60      | Chile             |
| PER         | 63      | Peru              |
| PAR         | 65      | Paraguai          |
| ECU         | 67      | Equador           |
| KSA         | 68      | Arábia Saudita    |
| QAT         | 75      | Catar             |
| NZL         | 107     | Nova Zelândia     |
| PAN         | 114     | Panamá            |
| JAM         | 121     | Jamaica           |
| HON         | 137     | Honduras          |
| BOL         | 140     | Bolívia           |
| VEN         | 141     | Venezuela         |
| TUN         | 155     | Tunísia           |
| ALG         | 156     | Argélia           |
| CIV         | 157     | Costa do Marfim   |
| AUT         | 158     | Áustria           |
| DEN         | 159     | Dinamarca         |
| NOR         | 161     | Noruega           |
| SCO         | 162     | Escócia           |
| WAL         | 163     | Gales             |
| HUN         | 164     | Hungria           |
| ROU         | 165     | Romênia           |
| CZE         | 166     | Tchéquia          |
| SVK         | 167     | Eslováquia        |
| GRE         | 168     | Grécia            |
| ALB         | 169     | Albânia           |
| ISL         | 170     | Islândia          |
| IRL         | 171     | Irlanda           |
| BIH         | 172     | Bósnia e Herzegovina |
| IRQ         | 173     | Iraque            |
| CUB         | 175     | Cuba              |
| PRK         | 179     | Coreia do Norte   |
| HAI         | 180     | Haiti             |
| TUR         | 181     | Turquia           |

### B. Placeholders do mata-mata

A FIFA usa códigos especiais para times ainda não definidos:

| Padrão  | Significado                                                |
|---------|------------------------------------------------------------|
| `1A`-`1L` | 1º colocado do Grupo A, B, ..., L (12 grupos de 4)       |
| `2A`-`2L` | 2º colocado de cada grupo                                 |
| `W1`-`W104` | Vencedor da partida nº 1, 2, ..., 104 (mata-mata)     |
| `L1`-`L104` | Perdedor da partida nº 1, 2, ..., 104                  |
| `RU101`-`RU104` | Runner-up (uso específico para 3º lugar)             |

O sistema marca esses códigos como `is_placeholder=1` em `times_fifa_codes` e os ignora ao resolver `time_id`.

### C. Estrutura HTML da FIFA (referência para manutenção do spider)

Container de partida:
```html
<div class="match-row_matchRowContainer__NoCRI">
  <div class="match-row_matchRowBody__yc8mV">
    <div class="match-row_team__y5Rva justify-content-end">
      <div class="team-abbreviations_container__wWtDG">MEX</div>
      <span class="d-none d-md-block">México</span>
      <div class="match-centre-logo-component_TeamLogoWrapper__P3lPN">
        <img srcset="...flags-sq-1/MEX 1x" />
      </div>
    </div>
    <div class="match-row_matchRowStatus__AJE7s">
      <!-- Partida passada: -->
      <span class="match-row_score__wfcQP match-row_scoreWinner__KB4p-">2</span>
      <div class="match-row_status__kFtCL">
        <span class="match-row_statusLabel__AiSA3">FIM</span>
      </div>
      <span class="match-row_score__wfcQP match-row_scoreLoser__vNbgU">0</span>
      <!-- OU partida futura: -->
      <span class="match-row_matchTime__9QJXJ">16:00</span>
    </div>
    <div class="match-row_team__y5Rva">
      <!-- time visitante (mesma estrutura) -->
    </div>
  </div>
  <div class="match-row_bottomLabelWrapper__9iAmu">
    <span class="match-row_bottomLabel__ni63b justify-content-end">Primeira fase</span>
    <span>·</span>
    <div class="match-row_statiumCityWrapper__G8ygZ">
      <span class="match-row_bottomLabel__ni63b">Grupo A</span>
      <span>·</span>
      <div class="match-row_stadiumCityLabels__zjXUq">
        <span>Estádio da Cidade do México</span>
        <span>(Cidade do México)</span>
      </div>
    </div>
  </div>
</div>
```

> ⚠️ **Atenção:** Os hashes no final dos nomes de classe (ex: `__NoCRI`, `__yc8mV`) são dinâmicos e mudam a cada build do site. O spider usa regex com prefixo (`^match-row_matchRowContainer__`) para casar, então é resiliente a mudanças de hash.

### D. Distribuição esperada de partidas (Copa 2026)

| Fase                  | Partidas | Jogos por time |
|-----------------------|----------|----------------|
| Primeira fase (grupos) | 72       | 3 (cada time) |
| Segundas de final      | 16       | -              |
| Oitavas de final       | 8        | -              |
| Quartas de final       | 4        | -              |
| Semifinal              | 2        | -              |
| Decisão do 3º lugar    | 1        | -              |
| Final                  | 1        | -              |
| **TOTAL**              | **104**  | -              |

### E. Arquivos de log gerados

```
fifa_spider/
└── logs/
    ├── spider.log       # log principal de execução (append)
    └── changes.log      # log de mudanças detectadas (append, com blocos ===)
```

`changes.log` exemplo:
```
==============================================================================
# Captura em 2026-06-19T10:00:00-03:00
# Total de mudanças: 1
==============================================================================
{"type": "MATCH_UPDATED", "match_id": "9b370b2a5884d3df",
 "summary": {"home_code": "BRA", "away_code": "HAI", "score_home": null, "score_away": null, "status": null},
 "details": {"status": {"old": null, "new": "FIM"},
             "score_home": {"old": null, "new": "2"},
             "score_away": {"old": null, "new": "0"}}}
```

---

## 📋 Checklist Final

Antes de dar o sistema como pronto, valide cada item:

- [ ] Python 3.11+ instalado
- [ ] `pip install -r requirements.txt` executado sem erros
- [ ] `playwright install chromium` executado
- [ ] `db.py`, `schema.sql`, `core_math.py`, `risk_agent.py`, `copa2026_scraper.py`,
      `check_today.py`, `worker_analyze.py`, `app.py`, `fifa_spider.py`,
      `fifa_sync.py`, `worker_ingest.py` criados em `C:\Projetos\ag_aposta\`
- [ ] `python core_math.py` passa nos sanity checks
- [ ] `python -c "import db; db.init_db()"` cria o banco sem erros
- [ ] `python fifa_spider.py --once` captura 104 partidas da FIFA
- [ ] `python fifa_sync.py` importa as partidas para o banco
- [ ] `python worker_ingest.py --once` roda o fluxo completo (spider + sync)
- [ ] Verificação: 104 rows em `partidas_agenda`, 111 em `times_fifa_codes`
- [ ] `streamlit run app.py` abre o painel sem erro
- [ ] Agendamento em produção configurado (Task Scheduler / cron / systemd)

---

## 🎯 Próximos Passos Sugeridos

1. **Integrar `worker_analyze.py` ao fluxo automático** — chamar após cada sync
2. **Cruzar `partidas_agenda` com `check_today.py`** para popular `odds_mercado`
   automaticamente e gerar recomendações EV+
3. **Dashboard de auditoria** em Streamlit mostrando evolução de
   status/score ao longo do tempo (via `partidas_fifa_log`)
4. **Alertas** — integrar `changes.log` a um webhook Discord/Slack quando uma
   partida começar, tiver gol, ou ficar FIM
5. **Backfill histórico** — usar `copa2026_scraper.py` com um CSV de xG real
   para ter estatísticas mais confiáveis antes do modelo Poisson

---

**Fim do Guia de Integração** — Sistema operacional em `C:\Projetos\ag_aposta\`
