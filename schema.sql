-- =====================================================================
-- schema.sql
-- Esquema do banco de dados local (SQLite) do Futebol Quant-Agent.
-- Executar via: sqlite3 data/quant_bet.db < schema.sql
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

-- Tabela de Histórico de Recomendações e Resultados
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
    FOREIGN KEY(partida_id) REFERENCES partidas_agenda(partida_id)
);

-- =====================================================================
-- V2 — Extensões para integração com o spider FIFA
-- =====================================================================
-- Colunas extras em partidas_agenda são adicionadas via ALTER TABLE
-- em db.py:_apply_schema_v2 (SQLite não tem ADD COLUMN IF NOT EXISTS)

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

-- Mapeamento entre códigos FIFA (3 letras) e time_id
CREATE TABLE IF NOT EXISTS times_fifa_codes (
    fifa_code TEXT PRIMARY KEY,
    time_id INTEGER,
    nome_pt TEXT,
    nome_en TEXT,
    flag_url TEXT,
    is_placeholder INTEGER DEFAULT 0,
    FOREIGN KEY(time_id) REFERENCES times_performance(time_id)
);

-- Histórico versionado de partidas — cada captura do spider gera uma linha
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

-- Índices
CREATE INDEX IF NOT EXISTS idx_partidas_agenda_data
    ON partidas_agenda(data_evento);
CREATE INDEX IF NOT EXISTS idx_capturas_fifa_ts
    ON capturas_fifa(capture_timestamp);
CREATE INDEX IF NOT EXISTS idx_partidas_fifa_log_match
    ON partidas_fifa_log(spider_match_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_historico_analises_pendentes
    ON historico_analises(resultado_partida);
