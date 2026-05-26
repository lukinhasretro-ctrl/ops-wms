import os
from contextlib import contextmanager
from datetime import date, datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row
    def _get_conn():
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        return psycopg.connect(url, row_factory=dict_row)
    PH = "%s"
else:
    import sqlite3
    def _get_conn():
        con = sqlite3.connect("ops.db")
        con.row_factory = sqlite3.Row
        return con
    PH = "?"

@contextmanager
def db():
    con = _get_conn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def row(r) -> dict:
    d = dict(r)
    for k, v in d.items():
        if hasattr(v, 'isoformat'):
            d[k] = str(v)
    return d

def rows(cur) -> list:
    return [row(r) for r in cur.fetchall()]

def like():
    return "ILIKE" if DATABASE_URL else "LIKE"

def now_str():
    return datetime.now().isoformat(sep=' ', timespec='seconds')

def today_str():
    return date.today().isoformat()

def serial():
    return "SERIAL" if DATABASE_URL else "INTEGER"

def autoincrement():
    return "" if DATABASE_URL else "AUTOINCREMENT"

def date_default():
    return "(CURRENT_DATE::text)" if DATABASE_URL else "(date('now'))"

def datetime_default():
    return "(NOW()::text)" if DATABASE_URL else "(datetime('now'))"

def init():
    with db() as con:
        cur = con.cursor()
        s = serial(); ai = autoincrement()
        dd = date_default(); dt = datetime_default()

        # ── estoque: lotes e validade ──────────────────────────
        cur.execute(f"""CREATE TABLE IF NOT EXISTS lotes (
            id          {s} PRIMARY KEY {ai},
            sku         TEXT NOT NULL,
            descricao   TEXT DEFAULT '',
            lote        TEXT NOT NULL,
            validade    TEXT NOT NULL,
            quantidade  NUMERIC NOT NULL DEFAULT 0,
            unidade     TEXT DEFAULT 'UN',
            endereco    TEXT NOT NULL,
            fornecedor  TEXT DEFAULT '',
            data_receb  TEXT DEFAULT {dd},
            ativo       INTEGER DEFAULT 1
        )""")

        cur.execute(f"""CREATE TABLE IF NOT EXISTS movimentos (
            id          {s} PRIMARY KEY {ai},
            lote_id     INTEGER,
            tipo        TEXT,
            quantidade  NUMERIC,
            usuario     TEXT DEFAULT 'sistema',
            obs         TEXT DEFAULT '',
            pedido_ref  TEXT DEFAULT '',
            data_mov    TEXT DEFAULT {dt}
        )""")

        # ── pedidos e ondas ────────────────────────────────────
        cur.execute(f"""CREATE TABLE IF NOT EXISTS pedidos (
            id              {s} PRIMARY KEY {ai},
            num_pedido      TEXT NOT NULL,
            data_pedido     TEXT DEFAULT '',
            cliente         TEXT DEFAULT '',
            sku             TEXT NOT NULL,
            descricao       TEXT DEFAULT '',
            quantidade      NUMERIC NOT NULL DEFAULT 0,
            transportadora  TEXT DEFAULT '',
            servico         TEXT DEFAULT '',
            campanha        TEXT DEFAULT '',
            fornecedor      TEXT DEFAULT '',
            data_entrega    TEXT DEFAULT '',
            prioridade      INTEGER DEFAULT 2,
            status          TEXT DEFAULT 'pendente',
            onda_id         INTEGER,
            origem          TEXT DEFAULT 'manual',
            criado_em       TEXT DEFAULT {dt}
        )""")

        cur.execute(f"""CREATE TABLE IF NOT EXISTS ondas (
            id              {s} PRIMARY KEY {ai},
            nome            TEXT NOT NULL,
            criterios       TEXT DEFAULT '',
            total_pedidos   INTEGER DEFAULT 0,
            total_skus      INTEGER DEFAULT 0,
            total_itens     NUMERIC DEFAULT 0,
            status          TEXT DEFAULT 'aberta',
            criado_em       TEXT DEFAULT {dt},
            fechado_em      TEXT DEFAULT ''
        )""")

        # ── configurações ──────────────────────────────────────
        cur.execute(f"""CREATE TABLE IF NOT EXISTS config (
            chave   TEXT PRIMARY KEY,
            valor   TEXT,
            updated TEXT DEFAULT {dt}
        )""")
