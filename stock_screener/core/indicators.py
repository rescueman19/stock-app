"""기술적 지표 계산 함수"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from config import TD_PER_YEAR


def peak_window(mode: str, years: int) -> Optional[int]:
    """최고점 산출 구간(거래일 수). 사상 최고가는 None(전체 구간)"""
    if mode == "ath":
        return None
    if mode == "52w":
        return TD_PER_YEAR
    return int(years * TD_PER_YEAR)


def rolling_peak(close: pd.Series, mode: str, years: int) -> pd.Series:
    """각 거래일 시점의 기준 최고점 시계열"""
    w = peak_window(mode, years)
    return close.cummax() if w is None else close.rolling(w, min_periods=1).max()


def consecutive_true(cond: pd.Series) -> pd.Series:
    """조건이 연속으로 참인 일수 (거짓이 나오면 0으로 초기화)"""
    cond = cond.fillna(False).astype(bool)
    groups = (~cond).cumsum()
    return cond.astype(int).groupby(groups).cumsum()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI (와일더 평활 방식)"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0   # 하락 없이 상승만 한 구간
    return out
