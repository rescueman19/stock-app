"""자동 실행기

  python scheduler.py            → 상시 실행 (국내 평일 16:10, 해외 화~토 07:10 KST)
  python scheduler.py --once KR  → 한 번만 실행 (KR / US / ALL)

조건은 config.py의 default_config() 값을 사용함.
"""
import argparse
import logging

from config import MARKET_LABELS, default_config
from core import storage
from core.cache import load_market_data
from core.engine import run_screen
from core.notifier import format_alert, get_notifiers, notify_all
from core.sources import get_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")


def run_job(market: str) -> None:
    cfg = default_config(market)
    bundle, msg = load_market_data(get_source(market), force=True)
    if msg:
        log.warning(msg)
    res = run_screen(bundle, cfg)
    hits = res[res["hit"]]
    r = storage.save_run(market, cfg.key(), bundle.data_date_str, hits)
    if not r["saved"]:
        log.info("[%s] 기준일 %s 이미 처리됨(휴장일 등) → 건너뜀", market, bundle.data_date_str)
        return
    log.info("[%s] 기준일 %s · 조건 충족 %d · 신규 %d · 이탈 %d", market, bundle.data_date_str,
             len(hits), len(r["new"]), len(r["exited"]))
    for row in hits.itertuples():
        log.info("   %s(%s) %.1f%% · %d일", row.name, row.ticker, row.drawdown, row.streak)

    if (r["first"] or len(r["new"]) or len(r["exited"])) and get_notifiers():
        text = format_alert(MARKET_LABELS[market], bundle.data_date_str, cfg.describe(),
                            r["new"], r["exited"], len(hits))
        if r["first"]:
            text = "(첫 실행: 현재 충족 종목 전체를 신규로 표시)\n" + text
        log.info("알림 전송 결과: %s", notify_all(text))


def safe_job(market: str) -> None:
    try:
        run_job(market)
    except Exception:
        log.exception("[%s] 실행 실패", market)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", choices=["KR", "US", "ALL"])
    a = ap.parse_args()
    if a.once:
        for m in (["KR", "US"] if a.once == "ALL" else [a.once]):
            safe_job(m)
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    sch = BlockingScheduler(timezone="Asia/Seoul")
    sch.add_job(safe_job, CronTrigger(day_of_week="mon-fri", hour=16, minute=10), args=["KR"], id="KR")
    sch.add_job(safe_job, CronTrigger(day_of_week="tue-sat", hour=7, minute=10), args=["US"], id="US")
    log.info("스케줄러 시작 (Ctrl+C 종료) · 국내 평일 16:10 · 해외 화~토 07:10")
    sch.start()


if __name__ == "__main__":
    main()
