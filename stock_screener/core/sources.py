"""시장별 데이터 수집 모듈

데이터 소스를 DataSource 인터페이스로 추상화하여, 특정 라이브러리가 막히거나
형식이 바뀌어도 해당 클래스만 교체하면 되도록 구성함.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from io import StringIO
from typing import Callable, Optional

import numpy as np
import pandas as pd
import requests

from config import DEMO_MODE, KR_TOP_N

log = logging.getLogger(__name__)
ProgressFn = Optional[Callable[[float, str], None]]
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _progress(cb: ProgressFn, frac: float, msg: str) -> None:
    if cb:
        cb(min(max(frac, 0.0), 1.0), msg)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """인덱스를 날짜형(시간대 없음)으로 통일하고 OHLCV 컬럼만 남김"""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df = df[[c for c in OHLCV if c in df.columns]]
    df = df[df["Close"].notna() & (df["Close"] > 0)]
    return df[~df.index.duplicated(keep="last")].sort_index()


def _clean_series(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    return s.dropna().sort_index()


class DataSource(ABC):
    """데이터 소스 공통 인터페이스"""
    market = ""
    currency = ""
    benchmark_name = ""

    @abstractmethod
    def get_universe(self) -> pd.DataFrame:
        """대상 종목 목록 (컬럼: ticker, name, market_cap)"""

    @abstractmethod
    def get_prices(self, tickers: list[str], start: pd.Timestamp,
                   progress: ProgressFn = None) -> dict[str, pd.DataFrame]:
        """종목별 수정주가 OHLCV"""

    @abstractmethod
    def get_benchmark(self, start: pd.Timestamp) -> pd.Series:
        """벤치마크 지수 종가"""

    def get_market_caps(self, tickers: list[str], universe: pd.DataFrame) -> pd.Series:
        """시가총액(원 또는 달러). 기본은 유니버스에 담긴 값 사용"""
        if "market_cap" in universe.columns:
            return universe.set_index("ticker")["market_cap"].reindex(tickers)
        return pd.Series(np.nan, index=tickers)

    def get_fx(self) -> Optional[float]:
        """원/달러 환율 (표시용). 국내는 해당 없음"""
        return None


# ─────────────────────────────── 국내 ───────────────────────────────
class KRSource(DataSource):
    market = "KR"
    currency = "KRW"
    benchmark_name = "KOSPI"
    EXCLUDE_WORDS = ("리츠", "스팩", "REIT", "ETN")

    def _valid(self, ticker: str, name: str) -> bool:
        """보통주만 인정(우선주는 코드 끝자리가 0이 아님), 리츠·스팩 제외"""
        return str(ticker).endswith("0") and not any(w in str(name) for w in self.EXCLUDE_WORDS)

    def get_universe(self) -> pd.DataFrame:
        try:
            return self._universe_pykrx()
        except Exception as e:  # pykrx 실패 시 FinanceDataReader로 대체
            log.warning("pykrx 유니버스 실패(%s) → FinanceDataReader 사용", e)
            return self._universe_fdr()

    def _universe_pykrx(self) -> pd.DataFrame:
        from pykrx import stock
        day = stock.get_nearest_business_day_in_a_week()
        cap = stock.get_market_cap(day, market="KOSPI")
        if cap is None or cap.empty:
            raise RuntimeError("시가총액 데이터 없음")
        cap = cap.sort_values("시가총액", ascending=False)
        rows = []
        for t, r in cap.iterrows():
            name = stock.get_market_ticker_name(t)
            if self._valid(t, name):
                rows.append({"ticker": t, "name": name, "market_cap": float(r["시가총액"])})
            if len(rows) >= KR_TOP_N:
                break
        return pd.DataFrame(rows)

    def _universe_fdr(self) -> pd.DataFrame:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KOSPI").sort_values("Marcap", ascending=False)
        df = df[[self._valid(c, n) for c, n in zip(df["Code"], df["Name"])]].head(KR_TOP_N)
        return pd.DataFrame({"ticker": df["Code"].values, "name": df["Name"].values,
                             "market_cap": df["Marcap"].astype(float).values})

    def get_prices(self, tickers, start, progress=None):
        s, e = start.strftime("%Y%m%d"), pd.Timestamp.today().strftime("%Y%m%d")
        out = {}
        for i, t in enumerate(tickers):
            _progress(progress, i / max(len(tickers), 1), f"국내 시세 수집 {i + 1}/{len(tickers)} · {t}")
            df = None
            try:
                from pykrx import stock
                raw = stock.get_market_ohlcv(s, e, t, adjusted=True)
                if raw is not None and not raw.empty:
                    df = raw.rename(columns={"시가": "Open", "고가": "High", "저가": "Low",
                                             "종가": "Close", "거래량": "Volume"})
            except Exception as ex:
                log.debug("pykrx 시세 실패 %s: %s", t, ex)
            if df is None or df.empty:
                try:
                    import FinanceDataReader as fdr
                    df = fdr.DataReader(t, start)
                except Exception as ex:
                    log.warning("시세 수집 실패 %s: %s", t, ex)
                    continue
            if df is not None and not df.empty:
                df = _clean(df)
                if not df.empty:
                    out[t] = df
            time.sleep(0.05)
        _progress(progress, 1.0, "국내 시세 수집 완료")
        return out

    def get_benchmark(self, start):
        try:
            from pykrx import stock
            idx = stock.get_index_ohlcv(start.strftime("%Y%m%d"),
                                        pd.Timestamp.today().strftime("%Y%m%d"), "1001")
            return _clean_series(idx["종가"].astype(float))
        except Exception as e:
            log.warning("pykrx 지수 실패(%s) → FinanceDataReader 사용", e)
            import FinanceDataReader as fdr
            return _clean_series(fdr.DataReader("KS11", start)["Close"].astype(float))


# ─────────────────────────────── 해외 ───────────────────────────────
# 위키백과 조회 실패 시 사용하는 예비 목록(2025년 기준, 구성 변경 시 갱신 필요)
NDX_FALLBACK = """AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO TSLA COST NFLX PLTR ASML AMD CSCO TMUS AZN
LIN INTU ISRG PEP SHOP BKNG TXN QCOM ADBE AMGN ARM PDD AMAT GILD MU LRCX HON CMCSA APP ADP KLAC
ADI PANW INTC MELI CRWD VRTX SBUX CEG MSTR DASH CDNS SNPS MAR ORLY CTAS ABNB FTNT ADSK MRVL MDLZ
PYPL REGN AXON WDAY ROP CSX MNST CHTR AEP NXPI FAST PCAR IDXX ZS TTWO PAYX KDP EA ROST CPRT BKR
VRSK XEL EXC FANG TEAM CCEP DDOG CTSH LULU KHC ODFL GEHC ON TTD MCHP CSGP DXCM CDW BIIB GFS WBD""".split()


class USSource(DataSource):
    market = "US"
    currency = "USD"
    benchmark_name = "NASDAQ-100"
    WIKI = "https://en.wikipedia.org/wiki/Nasdaq-100"

    def get_universe(self) -> pd.DataFrame:
        try:
            html = requests.get(self.WIKI, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
            for tb in pd.read_html(StringIO(html)):
                cols = [str(c) for c in tb.columns]
                if "Ticker" in cols and len(tb) >= 90:
                    name_col = "Company" if "Company" in cols else cols[0]
                    return pd.DataFrame({
                        "ticker": tb["Ticker"].astype(str).str.strip().str.replace(".", "-", regex=False),
                        "name": tb[name_col].astype(str),
                    })
            raise RuntimeError("구성 종목 표를 찾지 못함")
        except Exception as e:
            log.warning("NASDAQ-100 목록 조회 실패(%s) → 예비 목록 사용", e)
            return pd.DataFrame({"ticker": NDX_FALLBACK, "name": NDX_FALLBACK})

    def get_prices(self, tickers, start, progress=None):
        import yfinance as yf
        out, chunk = {}, 25
        for i in range(0, len(tickers), chunk):
            batch = tickers[i:i + chunk]
            _progress(progress, i / max(len(tickers), 1), f"해외 시세 수집 {i + 1}~{i + len(batch)}/{len(tickers)}")
            try:
                data = yf.download(batch, start=start.strftime("%Y-%m-%d"), auto_adjust=True,
                                   progress=False, group_by="ticker", threads=True)
            except Exception as e:
                log.warning("yfinance 수집 실패: %s", e)
                continue
            for t in batch:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        if t not in data.columns.get_level_values(0):
                            continue
                        df = data[t]
                    else:
                        df = data
                    df = df.dropna(how="all")
                    if not df.empty:
                        df = _clean(df)
                        if not df.empty:
                            out[t] = df
                except Exception as e:
                    log.debug("종목 파싱 실패 %s: %s", t, e)
        _progress(progress, 1.0, "해외 시세 수집 완료")
        return out

    def get_benchmark(self, start):
        import yfinance as yf
        h = yf.Ticker("^NDX").history(start=start.strftime("%Y-%m-%d"), auto_adjust=True)
        return _clean_series(h["Close"].astype(float))

    def get_market_caps(self, tickers, universe):
        import yfinance as yf
        caps = {}
        for t in tickers:
            try:
                caps[t] = float(yf.Ticker(t).fast_info["market_cap"])
            except Exception:
                caps[t] = np.nan
        return pd.Series(caps).reindex(tickers)

    def get_fx(self):
        try:
            import yfinance as yf
            return float(yf.Ticker("KRW=X").history(period="5d")["Close"].dropna().iloc[-1])
        except Exception:
            return None


# ─────────────────────────────── 데모 ───────────────────────────────
class DemoSource(DataSource):
    """인터넷 없이 화면을 시험하기 위한 가상 데이터"""

    def __init__(self, market: str):
        self.market = market
        self.currency = "KRW" if market == "KR" else "USD"
        self.benchmark_name = "데모지수"
        self.n = 40

    def _dates(self, start):
        return pd.bdate_range(start, pd.Timestamp.today().normalize())

    def get_universe(self):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "ticker": [f"D{self.market}{i:02d}" for i in range(self.n)],
            "name": [f"데모종목{i:02d}" for i in range(self.n)],
            "market_cap": rng.uniform(1e12, 5e14, self.n) if self.market == "KR" else rng.uniform(5e9, 3e12, self.n),
        })

    def get_prices(self, tickers, start, progress=None):
        dates, out = self._dates(start), {}
        for i, t in enumerate(tickers):
            rng = np.random.default_rng(100 + i)
            r = rng.normal(0.0004, 0.02, len(dates))
            if i % 3 == 0:          # 최근 급락 종목
                r[-160:-15] -= 0.004
            if i % 7 == 0:          # 과거 급락 후 회복 종목
                r[-900:-700] -= 0.004
                r[-700:-500] += 0.004
            close = 100 * np.exp(np.cumsum(r))
            out[t] = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                                   "Close": close, "Volume": rng.integers(1e5, 1e6, len(dates))},
                                  index=dates)
            _progress(progress, (i + 1) / len(tickers), f"가상 데이터 생성 {i + 1}/{len(tickers)}")
        return out

    def get_benchmark(self, start):
        dates = self._dates(start)
        r = np.random.default_rng(7).normal(0.0003, 0.012, len(dates))
        return pd.Series(1000 * np.exp(np.cumsum(r)), index=dates)

    def get_fx(self):
        return 1380.0 if self.market == "US" else None


def get_source(market: str) -> DataSource:
    """시장 코드(KR/US)에 맞는 데이터 소스 반환"""
    if DEMO_MODE:
        return DemoSource(market)
    return KRSource() if market == "KR" else USSource()
