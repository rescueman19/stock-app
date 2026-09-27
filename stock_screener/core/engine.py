"""스크리닝 엔진: 종목별 지표 계산 및 조건 판정"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from config import TD_PER_YEAR, ScreenConfig
from core.indicators import consecutive_true, peak_window, rolling_peak, rsi

RESULT_COLUMNS = ["ticker", "name", "price", "peak", "peak_date", "drawdown", "streak",
                  "days_since_peak", "low52_pos", "rel_drawdown", "bench_ret", "rsi",
                  "ma_cross", "rsi_rebound", "new_low10", "vol_ratio", "last_date", "hit"]


def compute_metrics(df: pd.DataFrame, bench: Optional[pd.Series], cfg: ScreenConfig) -> Optional[dict]:
    """한 종목의 최신 시점 지표 계산 (데이터 60거래일 미만이면 제외)"""
    close = df["Close"].dropna()
    if len(close) < 60:
        return None
    th = cfg.drawdown_pct / 100

    # 최고점 대비 하락률과 조건 연속 유지일
    peak_s = rolling_peak(close, cfg.peak_mode, cfg.peak_years)
    dd_s = close / peak_s - 1
    streak_s = consecutive_true(dd_s <= -th)

    w = peak_window(cfg.peak_mode, cfg.peak_years)
    win = close if w is None else close.iloc[-w:]
    peak, peak_date = float(win.max()), win.idxmax()
    price, last_date = float(close.iloc[-1]), close.index[-1]

    # 52주 최저가 대비 위치 및 최근 10거래일 신저가 여부
    low = df["Low"].reindex(close.index).fillna(close) if "Low" in df else close
    low52 = low.iloc[-TD_PER_YEAR:]
    low52_min, low52_date = float(low52.min()), low52.idxmin()

    # 바닥 확인 보조 지표
    ma20 = close.rolling(20).mean()
    above = close > ma20
    ma_cross = bool(above.iloc[-1] and (~above.iloc[-4:-1]).any())   # 최근 3거래일 내 20일선 상향 돌파
    r = rsi(close)
    rsi_now = float(r.iloc[-1])
    rsi_rebound = bool(r.iloc[-10:].min() <= 30 and rsi_now > 30)     # 10거래일 내 30 이하 → 30 회복

    vol_ratio = np.nan
    if "Volume" in df:
        vol = df["Volume"].reindex(close.index).astype(float)
        base = vol.iloc[-21:-1].mean()
        if base and base > 0:
            vol_ratio = float(vol.iloc[-1] / base)

    # 지수 대비 상대 하락률(%p) = 종목 하락률 - 같은 기간 지수 등락률
    drawdown = float(dd_s.iloc[-1] * 100)
    bench_ret = rel = np.nan
    if bench is not None and len(bench):
        b0, b1 = bench.asof(peak_date), bench.asof(last_date)
        if pd.notna(b0) and pd.notna(b1) and b0 > 0:
            bench_ret = (b1 / b0 - 1) * 100
            rel = drawdown - bench_ret

    return {
        "price": price, "peak": peak, "peak_date": peak_date, "drawdown": drawdown,
        "streak": int(streak_s.iloc[-1]), "days_since_peak": int((last_date - peak_date).days),
        "low52_pos": (price / low52_min - 1) * 100 if low52_min > 0 else np.nan,
        "rel_drawdown": rel, "bench_ret": bench_ret, "rsi": rsi_now,
        "ma_cross": ma_cross, "rsi_rebound": rsi_rebound,
        "new_low10": bool(low52_date >= close.index[-10]), "vol_ratio": vol_ratio,
        "last_date": last_date,
    }


def run_screen(bundle, cfg: ScreenConfig) -> pd.DataFrame:
    """전 종목 지표 계산 후 조건 충족 여부(hit) 표시"""
    names = bundle.universe.set_index("ticker")["name"]
    rows = []
    for t, df in bundle.prices.items():
        m = compute_metrics(df, bundle.benchmark, cfg)
        if m is None:
            continue
        m["ticker"], m["name"] = t, names.get(t, t)
        rows.append(m)
    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    res = pd.DataFrame(rows)
    need = max(cfg.min_streak, 1)
    res["hit"] = (res["drawdown"] <= -cfg.drawdown_pct) & (res["streak"] >= need)
    return res[RESULT_COLUMNS].sort_values("drawdown").reset_index(drop=True)


def _mask(s: pd.Series, ok: pd.Series, keep_missing: bool) -> pd.Series:
    ok = ok.fillna(False).astype(bool)
    return ok | s.isna() if keep_missing else ok


def apply_filters(df: pd.DataFrame, o: dict) -> pd.DataFrame:
    """2단계 선택 필터(상대 하락률, 바닥 신호, 재무)"""
    out = df.copy()
    if o.get("rel_on"):
        out = out[out["rel_drawdown"] <= -o["rel_min"]]
    if o.get("ma_cross"):
        out = out[out["ma_cross"]]
    if o.get("rsi_rebound"):
        out = out[out["rsi_rebound"]]
    if o.get("exclude_new_low"):
        out = out[~out["new_low10"]]
    if o.get("vol_min", 0) > 0:
        out = out[out["vol_ratio"] >= o["vol_min"]]

    f = o.get("fund")
    if f and "per" in out.columns:
        k = f["keep_missing"]
        if f["profit_only"]:
            out = out[_mask(out["op_positive"], out["op_positive"] == 1, k)]
        if f["per_max"] > 0:
            out = out[_mask(out["per"], (out["per"] > 0) & (out["per"] <= f["per_max"]), k)]
        if f["pbr_max"] > 0:
            out = out[_mask(out["pbr"], out["pbr"] <= f["pbr_max"], k)]
        if f["roe_min"] is not None:
            out = out[_mask(out["roe"], out["roe"] >= f["roe_min"], k)]
        if f["debt_max"] > 0:
            out = out[_mask(out["debt_ratio"], out["debt_ratio"] <= f["debt_max"], k)]
        if f["growth_min"] is not None:
            out = out[_mask(out["op_growth"], out["op_growth"] >= f["growth_min"], k)]
    return out
