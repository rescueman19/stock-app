"""앱 전역 설정값"""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "screener.db"

HISTORY_YEARS = 15      # 시세 수집 기간(년). '사상 최고가'는 이 기간 안의 최고가를 뜻함
KR_TOP_N = 100          # 국내 유니버스: KOSPI 시가총액 상위 N개
TD_PER_YEAR = 252       # 연간 거래일 수
DEMO_MODE = os.getenv("STOCK_DEMO", "0") == "1"

PEAK_MODES = {
    "ath": "사상 최고가 (수집기간 내)",
    "52w": "52주 최고가",
    "nyears": "최근 N년 최고가",
}

MARKET_LABELS = {"KR": "국내 · KOSPI 시총 상위 100", "US": "해외 · NASDAQ-100"}


@dataclass
class ScreenConfig:
    """스크리닝 기본 조건"""
    peak_mode: str = "nyears"   # ath | 52w | nyears
    peak_years: int = 3         # peak_mode가 nyears일 때 기간
    drawdown_pct: float = 30.0  # 최고점 대비 하락률 기준(%)
    min_streak: int = 0         # 조건 연속 유지 거래일 (0 = 미적용)

    def key(self) -> str:
        """이력 비교용 조건 식별 문자열"""
        return json.dumps(asdict(self), sort_keys=True)

    def describe(self) -> str:
        peak = PEAK_MODES[self.peak_mode].replace("N년", f"{self.peak_years}년")
        s = f"{peak} 대비 -{self.drawdown_pct:.0f}% 이하"
        if self.min_streak > 0:
            s += f", {self.min_streak}거래일 연속 유지"
        return s


def default_config(market: str) -> ScreenConfig:
    """시장별 기본 조건: 국내는 지속일 미적용, 해외는 5거래일 연속"""
    return ScreenConfig(min_streak=0 if market == "KR" else 5)
