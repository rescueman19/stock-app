"""알림 모듈 (텔레그램 / 카카오톡 '나에게 보내기')

새 채널을 추가하려면 Notifier를 상속해 send()만 구현하면 됨.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

import pandas as pd
import requests

log = logging.getLogger(__name__)


class Notifier(ABC):
    name = ""

    @abstractmethod
    def send(self, text: str) -> bool: ...


class TelegramNotifier(Notifier):
    name = "텔레그램"

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> bool:
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              data={"chat_id": self.chat_id, "text": text[:4000]}, timeout=15)
            return r.ok
        except Exception as e:
            log.warning("텔레그램 전송 실패: %s", e)
            return False


class KakaoNotifier(Notifier):
    """카카오톡 나에게 보내기. 액세스 토큰(약 6시간 유효) 만료 시 리프레시 토큰으로 갱신"""
    name = "카카오톡"
    SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

    def __init__(self, access_token: str, refresh_token: str = "", rest_key: str = ""):
        self.access, self.refresh, self.rest_key = access_token, refresh_token, rest_key

    def _refresh(self) -> bool:
        if not (self.refresh and self.rest_key):
            return False
        r = requests.post("https://kauth.kakao.com/oauth/token",
                          data={"grant_type": "refresh_token", "client_id": self.rest_key,
                                "refresh_token": self.refresh}, timeout=15)
        if r.ok and "access_token" in r.json():
            self.access = r.json()["access_token"]
            return True
        return False

    def _post(self, text: str):
        tpl = {"object_type": "text", "text": text[:200],
               "link": {"web_url": "https://finance.naver.com", "mobile_web_url": "https://m.stock.naver.com"}}
        return requests.post(self.SEND_URL, headers={"Authorization": f"Bearer {self.access}"},
                             data={"template_object": json.dumps(tpl, ensure_ascii=False)}, timeout=15)

    def send(self, text: str) -> bool:
        try:
            r = self._post(text)
            if r.status_code == 401 and self._refresh():
                r = self._post(text)
            return r.ok
        except Exception as e:
            log.warning("카카오톡 전송 실패: %s", e)
            return False


def get_notifiers() -> list[Notifier]:
    """.env에 설정된 채널만 활성화"""
    out: list[Notifier] = []
    tg_token, tg_chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        out.append(TelegramNotifier(tg_token, tg_chat))
    kk = os.getenv("KAKAO_ACCESS_TOKEN", "")
    if kk:
        out.append(KakaoNotifier(kk, os.getenv("KAKAO_REFRESH_TOKEN", ""), os.getenv("KAKAO_REST_API_KEY", "")))
    return out


def format_alert(market_label: str, data_date: str, cond: str,
                 new: pd.DataFrame, exited: pd.DataFrame, total: int) -> str:
    lines = [f"[급락주 스크리너] {market_label}", f"기준일 {data_date} · {cond}",
             f"조건 충족 {total}종목"]
    if len(new):
        lines.append(f"\n🆕 신규 진입 {len(new)}")
        lines += [f"· {r.name}({r.ticker}) {r.drawdown:.1f}%" for r in new.head(20).itertuples()]
    if len(exited):
        lines.append(f"\n↩ 조건 이탈 {len(exited)}")
        lines += [f"· {r.name}({r.ticker})" for r in exited.head(20).itertuples()]
    return "\n".join(lines)


def notify_all(text: str) -> dict:
    return {n.name: n.send(text) for n in get_notifiers()}
