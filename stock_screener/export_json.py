"""휴대폰 앱이 읽을 결과 파일(data/results.json) 생성

GitHub Actions에서 매일 자동 실행됨. 신규 진입 종목은 텔레그램·카카오톡으로 알림.
  python export_json.py
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from config import MARKET_LABELS, default_config
from core.cache import load_market_data
from core.engine import run_screen
from core.notifier import format_alert, get_notifiers, notify_all
from core.sources import get_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("export")
OUT = Path(__file__).resolve().parent.parent / "data" / "results.json"
HISTORY_DAYS = 260   # 앱 차트에 보낼 최근 거래일 수(약 1년)


def _num(v, nd=2):
    """NaN·None을 JSON에서 null로"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _cap(market: str, v) -> str:
    v = _num(v, 0)
    if v is None:
        return "-"
    if market == "KR":
        return f"{v / 1e12:.1f}조원" if v >= 1e12 else f"{v / 1e8:,.0f}억원"
    return f"${v / 1e9:,.1f}B"


def build_market(market: str, prev: dict | None) -> tuple[dict, list, list]:
    cfg = default_config(market)
    src = get_source(market)
    bundle, msg = load_market_data(src, force=True)
    if msg:
        log.warning("[%s] %s", market, msg)
    res = run_screen(bundle, cfg)
    hits = res[res["hit"]]
    caps = src.get_market_caps(hits["ticker"].tolist(), bundle.universe)

    # 신규 판정 기준: 기준일이 바뀌었으면 직전 결과, 같은 날 재실행이면 직전 기준 유지
    same_day = prev is not None and prev.get("data_date") == bundle.data_date_str
    if prev is None:
        base = None
    elif same_day:
        base = prev.get("base")
    else:
        base = [h["t"] for h in prev.get("hits", [])]

    items = []
    for r in hits.itertuples():
        close = bundle.prices[r.ticker]["Close"].iloc[-HISTORY_DAYS:]
        items.append({
            "t": r.ticker, "n": r.name, "p": _num(r.price), "pk": _num(r.peak),
            "pd": pd.Timestamp(r.peak_date).strftime("%Y-%m-%d"),
            "dd": _num(r.drawdown, 1), "st": int(r.streak), "rel": _num(r.rel_drawdown, 1),
            "rsi": _num(r.rsi, 0), "cap": _cap(market, caps.get(r.ticker)),
            "new": base is not None and r.ticker not in base,
            "h": [_num(v) for v in close.values],
            "hs": close.index[0].strftime("%Y-%m-%d"), "he": close.index[-1].strftime("%Y-%m-%d"),
        })

    now_t = {i["t"] for i in items}
    new = [i for i in items if i["new"]] if not same_day else []
    exited = [h for h in (prev or {}).get("hits", []) if h["t"] not in now_t] if (prev and not same_day) else []
    data = {
        "label": MARKET_LABELS[market], "condition": cfg.describe(),
        "drawdown_pct": cfg.drawdown_pct, "data_date": bundle.data_date_str,
        "benchmark": bundle.benchmark_name, "fx": _num(bundle.fx), "total": len(res),
        "count": len(items), "new_count": sum(i["new"] for i in items),
        "base": base, "hits": items,
    }
    return data, new, exited


def main() -> None:
    prev_all = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    prev_markets = prev_all.get("markets", {}) or {}
    out = {"updated_at": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M"), "markets": {}}

    for m in ("KR", "US"):
        prev = prev_markets.get(m)
        try:
            data, new, exited = build_market(m, prev)
            out["markets"][m] = data
            log.info("[%s] 기준일 %s · 충족 %d · 신규 %d · 이탈 %d",
                     m, data["data_date"], data["count"], len(new), len(exited))
            if (new or exited) and get_notifiers():
                to_df = lambda rows: pd.DataFrame(
                    [{"ticker": x["t"], "name": x["n"], "drawdown": x["dd"] or 0} for x in rows],
                    columns=["ticker", "name", "drawdown"])
                text = format_alert(data["label"], data["data_date"], data["condition"],
                                    to_df(new), to_df(exited), data["count"])
                log.info("알림 결과: %s", notify_all(text))
        except Exception as e:
            log.exception("[%s] 실패", m)
            if prev:   # 실패 시 직전 결과 유지
                prev["error"] = f"최근 갱신 실패로 이전 결과를 표시합니다 ({type(e).__name__})"
                out["markets"][m] = prev
            else:
                out["markets"][m] = None

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("저장 완료: %s", OUT)


if __name__ == "__main__":
    main()
