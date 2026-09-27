"""시세 데이터 캐시

같은 날 재실행 시 다시 받지 않고, 수집에 실패하면 마지막 캐시로 대체함.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from config import CACHE_DIR, DEMO_MODE, HISTORY_YEARS
from core.sources import DataSource, ProgressFn

log = logging.getLogger(__name__)


@dataclass
class MarketBundle:
    market: str
    currency: str
    benchmark_name: str
    universe: pd.DataFrame
    prices: dict
    benchmark: pd.Series
    fx: Optional[float]
    fetched_at: pd.Timestamp = field(default_factory=pd.Timestamp.now)

    @property
    def data_date(self) -> Optional[pd.Timestamp]:
        """데이터 기준일(가장 최근 거래일)"""
        if not self.prices:
            return None
        return max(df.index[-1] for df in self.prices.values())

    @property
    def data_date_str(self) -> str:
        d = self.data_date
        return d.strftime("%Y-%m-%d") if d is not None else "-"


def _path(market: str):
    return CACHE_DIR / f"{market}{'_demo' if DEMO_MODE else ''}_bundle.pkl"


def _read(market: str) -> Optional[MarketBundle]:
    p = _path(market)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        log.warning("캐시 읽기 실패: %s", e)
        return None


def _write(bundle: MarketBundle) -> None:
    p = _path(bundle.market)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(bundle, f)
    tmp.replace(p)


def load_market_data(source: DataSource, force: bool = False,
                     progress: ProgressFn = None) -> tuple[MarketBundle, Optional[str]]:
    """시장 데이터 반환. (bundle, 경고 메시지 또는 None)"""
    cached = _read(source.market)
    today = pd.Timestamp.today().date()
    if cached is not None and not force and cached.fetched_at.date() == today:
        return cached, None

    try:
        start = pd.Timestamp.today().normalize() - pd.DateOffset(years=HISTORY_YEARS)
        universe = source.get_universe()
        tickers = universe["ticker"].tolist()
        prices = source.get_prices(tickers, start, progress)
        benchmark = source.get_benchmark(start)
        bundle = MarketBundle(source.market, source.currency, source.benchmark_name,
                              universe, prices, benchmark, source.get_fx())
    except Exception as e:
        if cached is not None:
            return cached, f"데이터 수집 실패({e}). {cached.fetched_at:%Y-%m-%d %H:%M}에 받은 캐시를 표시합니다."
        raise

    coverage = len(bundle.prices) / max(len(tickers), 1)
    if coverage < 0.5 and cached is not None:
        return cached, (f"시세 수집이 {coverage:.0%}만 성공해 "
                        f"{cached.fetched_at:%Y-%m-%d %H:%M}에 받은 캐시를 표시합니다.")
    _write(bundle)
    msg = None
    if coverage < 0.95:
        missing = sorted(set(tickers) - set(bundle.prices))
        msg = f"{len(missing)}개 종목 시세를 받지 못했습니다: {', '.join(missing[:10])}{' 외' if len(missing) > 10 else ''}"
    return bundle, msg
