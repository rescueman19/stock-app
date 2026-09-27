"""SQLite 이력 저장: 실행 기록, 조건 충족 종목, 진입·이탈 이벤트"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT, market TEXT, data_date TEXT, params TEXT);
CREATE TABLE IF NOT EXISTS hits(
    run_id INTEGER, ticker TEXT, name TEXT, drawdown REAL, streak INTEGER);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, event_date TEXT, market TEXT,
    ticker TEXT, name TEXT, event TEXT, drawdown REAL, params TEXT);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    return c


def last_run(market: str, params: str) -> Optional[tuple[int, str]]:
    """같은 조건으로 저장된 마지막 실행 (run_id, 데이터 기준일)"""
    with _conn() as c:
        r = c.execute("SELECT id, data_date FROM runs WHERE market=? AND params=? ORDER BY id DESC LIMIT 1",
                      (market, params)).fetchone()
    return (r[0], r[1]) if r else None


def last_hits(market: str, params: str) -> Optional[pd.DataFrame]:
    """마지막 실행의 조건 충족 종목 (이력이 없으면 None)"""
    lr = last_run(market, params)
    if lr is None:
        return None
    with _conn() as c:
        return pd.read_sql("SELECT ticker, name, drawdown FROM hits WHERE run_id=?", c, params=(lr[0],))


def save_run(market: str, params: str, data_date: str, hits: pd.DataFrame) -> dict:
    """실행 결과 저장 후 신규 진입·이탈 종목 반환. 같은 기준일·조건이면 저장하지 않음"""
    lr = last_run(market, params)
    if lr is not None and lr[1] == data_date:
        return {"saved": False, "first": False, "new": hits.iloc[0:0], "exited": pd.DataFrame()}
    prev = last_hits(market, params)
    first = prev is None
    prev = prev if prev is not None else pd.DataFrame(columns=["ticker", "name", "drawdown"])
    new = hits[~hits["ticker"].isin(prev["ticker"])]
    exited = prev[~prev["ticker"].isin(hits["ticker"])]

    with _conn() as c:
        cur = c.execute("INSERT INTO runs(run_at, market, data_date, params) VALUES(?,?,?,?)",
                        (pd.Timestamp.now().isoformat(timespec="seconds"), market, data_date, params))
        run_id = cur.lastrowid
        c.executemany("INSERT INTO hits VALUES(?,?,?,?,?)",
                      [(run_id, r.ticker, r.name, float(r.drawdown), int(r.streak)) for r in hits.itertuples()])
        ev = [(run_id, data_date, market, r.ticker, r.name, "진입", float(r.drawdown), params) for r in new.itertuples()]
        ev += [(run_id, data_date, market, r.ticker, r.name, "이탈", float(r.drawdown), params) for r in exited.itertuples()]
        c.executemany("INSERT INTO events(run_id, event_date, market, ticker, name, event, drawdown, params) "
                      "VALUES(?,?,?,?,?,?,?,?)", ev)
    return {"saved": True, "first": first, "new": new, "exited": exited}


def list_events(market: Optional[str] = None, limit: int = 500) -> pd.DataFrame:
    q = "SELECT event_date, market, ticker, name, event, drawdown, params FROM events"
    args: tuple = ()
    if market:
        q += " WHERE market=?"
        args = (market,)
    q += " ORDER BY id DESC LIMIT ?"
    with _conn() as c:
        return pd.read_sql(q, c, params=args + (limit,))


def list_runs(market: Optional[str] = None, limit: int = 100) -> pd.DataFrame:
    q = ("SELECT r.id, r.run_at, r.market, r.data_date, r.params, COUNT(h.ticker) AS hits "
         "FROM runs r LEFT JOIN hits h ON h.run_id=r.id")
    args: tuple = ()
    if market:
        q += " WHERE r.market=?"
        args = (market,)
    q += " GROUP BY r.id ORDER BY r.id DESC LIMIT ?"
    with _conn() as c:
        return pd.read_sql(q, c, params=args + (limit,))
