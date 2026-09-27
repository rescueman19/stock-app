"""백테스트: 과거에 조건에 진입한 종목이 이후 지수보다 나았는지 검증

주의(생존편향): 무료로 구할 수 있는 과거 지수 구성 종목 이력이 없어 '현재' 구성 종목으로
과거를 검증함. 그 사이 탈락·상장폐지된 종목이 빠져 결과가 실제보다 좋게 나올 수 있음.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import TD_PER_YEAR, ScreenConfig
from core.indicators import consecutive_true, rolling_peak
from core.sources import ProgressFn, _progress

HORIZONS = {63: "3개월", 126: "6개월", 252: "12개월"}


def run_backtest(bundle, cfg: ScreenConfig, years: int = 10,
                 progress: ProgressFn = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(진입 신호 목록, 기간별 요약) 반환"""
    bench = bundle.benchmark.dropna()
    names = bundle.universe.set_index("ticker")["name"]
    th, need = cfg.drawdown_pct / 100, max(cfg.min_streak, 1)
    start = bundle.data_date - pd.DateOffset(years=years)
    rows = []
    items = list(bundle.prices.items())

    for k, (t, df) in enumerate(items):
        _progress(progress, k / max(len(items), 1), f"백테스트 {k + 1}/{len(items)} · {t}")
        close = df["Close"].dropna()
        if len(close) < TD_PER_YEAR + 60:
            continue
        dd = close / rolling_peak(close, cfg.peak_mode, cfg.peak_years) - 1
        streak = consecutive_true(dd <= -th)
        entry = (streak == need) & (close.index >= start)   # 조건을 처음 충족한 날 = 진입일
        entry.iloc[:TD_PER_YEAR] = False                     # 최고점 산출용 준비기간 제외
        vals, idx = close.values, close.index
        for i in np.flatnonzero(entry.values):
            d, p0 = idx[i], vals[i]
            b0 = bench.asof(d)
            row = {"ticker": t, "name": names.get(t, t), "entry_date": d,
                   "entry_price": p0, "drawdown": dd.iloc[i] * 100}
            for h in HORIZONS:
                if i + h >= len(vals):
                    continue
                ret = vals[i + h] / p0 - 1
                b1 = bench.asof(idx[i + h])
                bret = b1 / b0 - 1 if pd.notna(b0) and b0 > 0 and pd.notna(b1) else np.nan
                row[f"ret_{h}"] = ret * 100
                row[f"bench_{h}"] = bret * 100
                row[f"excess_{h}"] = (ret - bret) * 100
                row[f"mdd_{h}"] = (vals[i:i + h + 1].min() / p0 - 1) * 100
            rows.append(row)
    _progress(progress, 1.0, "백테스트 완료")

    sig = pd.DataFrame(rows)
    summary = []
    for h, label in HORIZONS.items():
        col = f"ret_{h}"
        if sig.empty or col not in sig:
            summary.append({"보유기간": label, "표본수": 0})
            continue
        s = sig.dropna(subset=[col])
        summary.append({
            "보유기간": label,
            "표본수": len(s),
            "평균수익률(%)": s[col].mean(),
            "중앙값(%)": s[col].median(),
            "승률(%)": (s[col] > 0).mean() * 100,
            "같은기간 지수(%)": s[f"bench_{h}"].mean(),
            "평균 초과수익(%p)": s[f"excess_{h}"].mean(),
            "지수대비 승률(%)": (s[f"excess_{h}"] > 0).mean() * 100,
            "평균 MDD(%)": s[f"mdd_{h}"].mean(),
            "최악 MDD(%)": s[f"mdd_{h}"].min(),
        })
    if not sig.empty:
        sig = sig.sort_values("entry_date", ascending=False).reset_index(drop=True)
    return sig, pd.DataFrame(summary)
