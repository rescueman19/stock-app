"""재무 지표 수집 (가치 함정 제거용)

- 국내: pykrx(PER·PBR·EPS·BPS) + OpenDART(영업이익·부채비율, API 키 있을 때)
  ※ OpenDART는 최근 사업연도 '연간' 기준 → 영업이익 흑자 여부도 연간 기준
- 해외: yfinance(PER·PBR·ROE·부채비율, 최근 4분기 영업이익)
  ※ yfinance 부채비율은 '총차입금/자기자본'으로 국내 '총부채/자기자본'과 정의가 다름
조건 충족 종목에만 조회하여 호출 수를 줄임.
"""
from __future__ import annotations

import io
import logging
import os
import pickle
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

from config import CACHE_DIR, DEMO_MODE
from core.sources import ProgressFn, _progress

log = logging.getLogger(__name__)
FUND_COLS = ["per", "pbr", "roe", "debt_ratio", "op_positive", "op_growth"]


def _num(v):
    try:
        x = float(str(v).replace(",", ""))
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


# ─────────────── 국내 ───────────────
def _dart_corp_map(key: str) -> dict:
    """종목코드 → DART 고유번호 매핑 (한 달간 캐시)"""
    p = CACHE_DIR / "dart_corpcode.pkl"
    if p.exists() and (pd.Timestamp.now() - pd.Timestamp(p.stat().st_mtime, unit="s")).days < 30:
        return pickle.loads(p.read_bytes())
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml",
                     params={"crtfc_key": key}, timeout=60)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    m = {}
    for el in root.iter("list"):
        code = (el.findtext("stock_code") or "").strip()
        if code:
            m[code] = el.findtext("corp_code")
    p.write_bytes(pickle.dumps(m))
    return m


def _dart_accounts(key: str, corp: str) -> dict:
    """최근 사업보고서의 영업이익(당기·전기), 부채총계, 자본총계"""
    today = pd.Timestamp.today()
    first_year = today.year - 1 if today.month >= 4 else today.year - 2
    for year in (first_year, first_year - 1):
        r = requests.get("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                         params={"crtfc_key": key, "corp_code": corp,
                                 "bsns_year": str(year), "reprt_code": "11011"}, timeout=20).json()
        if r.get("status") != "000":
            continue
        items = r.get("list", [])
        div = "CFS" if any(i.get("fs_div") == "CFS" for i in items) else "OFS"
        acc = {}
        for i in items:
            if i.get("fs_div") != div:
                continue
            nm = i.get("account_nm", "")
            if nm.startswith("영업이익") and "op" not in acc:
                acc["op"], acc["op_prev"] = _num(i.get("thstrm_amount")), _num(i.get("frmtrm_amount"))
            elif nm == "부채총계":
                acc["debt"] = _num(i.get("thstrm_amount"))
            elif nm == "자본총계":
                acc["equity"] = _num(i.get("thstrm_amount"))
        return acc
    return {}


def _fund_kr(tickers, progress) -> pd.DataFrame:
    out = pd.DataFrame(index=tickers, columns=FUND_COLS, dtype=float)
    try:
        from pykrx import stock
        f = stock.get_market_fundamental(stock.get_nearest_business_day_in_a_week(), market="KOSPI")
        f = f.reindex(tickers)
        out["per"] = f["PER"].replace(0, np.nan)
        out["pbr"] = f["PBR"].replace(0, np.nan)
        out["roe"] = np.where(f["BPS"] > 0, f["EPS"] / f["BPS"] * 100, np.nan)
    except Exception as e:
        log.warning("pykrx 재무 조회 실패: %s", e)

    key = os.getenv("DART_API_KEY", "").strip()
    if not key:
        return out
    try:
        cmap = _dart_corp_map(key)
    except Exception as e:
        log.warning("DART 고유번호 조회 실패: %s", e)
        return out
    for i, t in enumerate(tickers):
        _progress(progress, i / max(len(tickers), 1), f"OpenDART 재무 조회 {i + 1}/{len(tickers)}")
        corp = cmap.get(t)
        if not corp:
            continue
        try:
            a = _dart_accounts(key, corp)
        except Exception as e:
            log.debug("DART 실패 %s: %s", t, e)
            continue
        op, prev = a.get("op", np.nan), a.get("op_prev", np.nan)
        if pd.notna(op):
            out.loc[t, "op_positive"] = 1.0 if op > 0 else 0.0
        if pd.notna(op) and pd.notna(prev) and prev != 0:
            out.loc[t, "op_growth"] = (op - prev) / abs(prev) * 100
        d, e = a.get("debt", np.nan), a.get("equity", np.nan)
        if pd.notna(d) and pd.notna(e) and e > 0:
            out.loc[t, "debt_ratio"] = d / e * 100
    return out


# ─────────────── 해외 ───────────────
def _fund_us(tickers, progress) -> pd.DataFrame:
    import yfinance as yf
    out = pd.DataFrame(index=tickers, columns=FUND_COLS, dtype=float)
    for i, t in enumerate(tickers):
        _progress(progress, i / max(len(tickers), 1), f"재무 조회 {i + 1}/{len(tickers)} · {t}")
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}
            out.loc[t, "per"] = _num(info.get("trailingPE"))
            out.loc[t, "pbr"] = _num(info.get("priceToBook"))
            roe = _num(info.get("returnOnEquity"))
            out.loc[t, "roe"] = roe * 100 if pd.notna(roe) else np.nan
            out.loc[t, "debt_ratio"] = _num(info.get("debtToEquity"))
            q = tk.quarterly_income_stmt
            if q is not None and "Operating Income" in q.index:
                op = q.loc["Operating Income"].dropna()
                if len(op) >= 4:
                    out.loc[t, "op_positive"] = 1.0 if (op.iloc[:4] > 0).all() else 0.0
                if len(op) >= 5 and op.iloc[4] != 0:
                    out.loc[t, "op_growth"] = (op.iloc[0] - op.iloc[4]) / abs(op.iloc[4]) * 100
        except Exception as e:
            log.debug("yfinance 재무 실패 %s: %s", t, e)
    return out


def _fund_demo(tickers) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = len(tickers)
    return pd.DataFrame({"per": rng.uniform(-5, 40, n), "pbr": rng.uniform(0.3, 6, n),
                         "roe": rng.uniform(-10, 30, n), "debt_ratio": rng.uniform(20, 300, n),
                         "op_positive": rng.integers(0, 2, n).astype(float),
                         "op_growth": rng.uniform(-60, 80, n)}, index=tickers)


def fetch_fundamentals(market: str, tickers: list[str], progress: ProgressFn = None) -> pd.DataFrame:
    """종목별 재무 지표 (index: ticker, 컬럼: FUND_COLS)"""
    if not tickers:
        return pd.DataFrame(columns=FUND_COLS)
    if DEMO_MODE:
        return _fund_demo(tickers)
    return _fund_kr(tickers, progress) if market == "KR" else _fund_us(tickers, progress)
