"""고점 대비 급락주 스크리너 (실행: streamlit run app.py)"""
from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config import DEMO_MODE, MARKET_LABELS, PEAK_MODES, ScreenConfig, default_config
from core import storage
from core.backtest import HORIZONS, run_backtest
from core.cache import load_market_data
from core.engine import apply_filters, run_screen
from core.fundamentals import fetch_fundamentals
from core.indicators import rolling_peak, rsi
from core.sources import get_source

st.set_page_config(page_title="급락주 스크리너", page_icon="📉", layout="wide")
ss = st.session_state
for k in ("bundles", "screens", "caps", "funds", "backtests"):
    ss.setdefault(k, {})


# ───────────────────────── 사이드바: 조건 설정 ─────────────────────────
with st.sidebar:
    st.header("📉 급락주 스크리너")
    if DEMO_MODE:
        st.warning("데모 모드: 가상 데이터로 표시 중입니다.")
    market = st.radio("시장", list(MARKET_LABELS), format_func=MARKET_LABELS.get)

    st.subheader("기본 조건")
    peak_mode = st.selectbox("최고점 기준", list(PEAK_MODES), index=2, format_func=PEAK_MODES.get)
    peak_years = st.slider("N년", 1, 10, 3, disabled=peak_mode != "nyears")
    dd_pct = st.slider("최고점 대비 하락률 (% 이상)", 10, 70, 30, step=5)
    streak = st.number_input("조건 연속 유지 거래일 (0 = 미적용)", 0, 60,
                             value=default_config(market).min_streak, key=f"streak_{market}")
    cfg = ScreenConfig(peak_mode, peak_years, float(dd_pct), int(streak))

    opts: dict = {}
    with st.expander("지수 대비 상대 하락률"):
        opts["rel_on"] = st.checkbox("지수보다 더 많이 빠진 종목만")
        opts["rel_min"] = st.slider("지수 대비 추가 하락 (%p 이상)", 0, 50, 10, disabled=not opts["rel_on"])
    with st.expander("바닥 확인 신호"):
        opts["ma_cross"] = st.checkbox("최근 3거래일 내 20일선 상향 돌파")
        opts["rsi_rebound"] = st.checkbox("RSI 30 이하 → 30 회복 (최근 10거래일)")
        opts["exclude_new_low"] = st.checkbox("최근 10거래일 내 52주 신저가 종목 제외")
        opts["vol_min"] = st.number_input("거래량 배율 (20일 평균 대비, 0 = 미적용)", 0.0, 10.0, 0.0, 0.5)
    with st.expander("재무 필터 (가치 함정 제거)"):
        fund_on = st.checkbox("재무 지표 조회·필터 사용")
        f = {
            "profit_only": st.checkbox("영업이익 흑자만", disabled=not fund_on),
            "per_max": st.number_input("PER 상한 (0 = 미적용)", 0.0, 200.0, 0.0, 5.0, disabled=not fund_on),
            "pbr_max": st.number_input("PBR 상한 (0 = 미적용)", 0.0, 50.0, 0.0, 0.5, disabled=not fund_on),
            "roe_min": None, "growth_min": None,
            "debt_max": st.number_input("부채비율 상한 % (0 = 미적용)", 0.0, 2000.0, 0.0, 50.0, disabled=not fund_on),
            "keep_missing": st.checkbox("데이터 없는 종목도 포함", value=True, disabled=not fund_on),
        }
        if st.checkbox("ROE 하한 사용", disabled=not fund_on):
            f["roe_min"] = st.number_input("ROE 하한 (%)", -50.0, 50.0, 5.0, 1.0)
        if st.checkbox("영업이익 증감률 하한 사용", disabled=not fund_on):
            f["growth_min"] = st.number_input("영업이익 증감률 하한 (%)", -100.0, 200.0, 0.0, 5.0)
        if market == "KR":
            st.caption("국내 영업이익·부채비율은 .env의 DART_API_KEY가 있어야 조회됩니다(최근 사업연도 기준).")
        else:
            st.caption("해외 부채비율은 총차입금/자기자본 기준으로 국내와 정의가 다릅니다.")
        opts["fund"] = f if fund_on else None

    refresh = st.button("데이터 새로 받기", width="stretch")


# ───────────────────────── 데이터 로딩 ─────────────────────────
def get_bundle(mkt: str, force: bool = False):
    if force or mkt not in ss.bundles:
        bar = st.progress(0.0, text="데이터 준비 중")
        try:
            ss.bundles[mkt] = load_market_data(get_source(mkt), force=force,
                                               progress=lambda p, m: bar.progress(p, text=m))
        finally:
            bar.empty()
    return ss.bundles[mkt]


try:
    bundle, warn = get_bundle(market, refresh)
except Exception as e:
    st.error(f"데이터를 받지 못했습니다: {e}\n\n인터넷 연결을 확인하거나 잠시 후 '데이터 새로 받기'를 누르세요.")
    st.stop()


def screen(c: ScreenConfig) -> pd.DataFrame:
    key = (market, str(bundle.fetched_at), c.key())
    if key not in ss.screens:
        ss.screens[key] = run_screen(bundle, c)
    return ss.screens[key]


res = screen(cfg)
hits = res[res["hit"]].copy()
tickers = hits["ticker"].tolist()

# 시가총액 (조건 충족 종목만 조회)
ck = (market, str(bundle.fetched_at), tuple(tickers))
if ck not in ss.caps:
    with st.spinner("시가총액 조회 중"):
        ss.caps[ck] = get_source(market).get_market_caps(tickers, bundle.universe)
hits["market_cap"] = hits["ticker"].map(ss.caps[ck])

# 재무 지표 (사용 시에만)
if opts["fund"] is not None and tickers:
    fk = (market, str(bundle.fetched_at.date()), tuple(sorted(tickers)))
    if fk not in ss.funds:
        bar = st.progress(0.0, text="재무 지표 조회 중")
        ss.funds[fk] = fetch_fundamentals(market, tickers, lambda p, m: bar.progress(p, text=m))
        bar.empty()
    hits = hits.join(ss.funds[fk], on="ticker")

filtered = apply_filters(hits, opts)

# 이전 저장 이력 대비 신규 진입 표시
prev = storage.last_hits(market, cfg.key())
filtered["is_new"] = ~filtered["ticker"].isin(prev["ticker"]) if prev is not None else False


# ───────────────────────── 표시용 표 구성 ─────────────────────────
cap_div, cap_label = (1e8, "시가총액(억원)") if market == "KR" else (1e9, "시가총액($B)")
price_fmt = "%,.0f" if market == "KR" else "$%.2f"


def display_table(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "NEW": np.where(df["is_new"], "🆕", ""),
        "종목명": df["name"], "티커": df["ticker"],
        "현재가": df["price"], "최고점": df["peak"],
        "최고점일자": pd.to_datetime(df["peak_date"]).dt.strftime("%Y-%m-%d"),
        "하락률(%)": df["drawdown"], "지속거래일": df["streak"],
        cap_label: df["market_cap"] / cap_div,
        "최고점경과일": df["days_since_peak"],
        "52주저가대비(%)": df["low52_pos"],
        "지수대비(%p)": df["rel_drawdown"],
        "RSI": df["rsi"],
        "20일선돌파": df["ma_cross"], "RSI반등": df["rsi_rebound"],
        "10일내신저가": df["new_low10"], "거래량배율": df["vol_ratio"],
    })
    if market == "US" and bundle.fx:
        out.insert(4, "현재가(원)", df["price"] * bundle.fx)
    if "per" in df.columns:
        out["PER"], out["PBR"], out["ROE(%)"] = df["per"], df["pbr"], df["roe"]
        out["부채비율(%)"], out["영업이익증감(%)"] = df["debt_ratio"], df["op_growth"]
        out["영업이익흑자"] = df["op_positive"].map({1.0: "흑자", 0.0: "적자"}).fillna("-")
    return out.reset_index(drop=True)


COL_CFG = {
    "현재가": st.column_config.NumberColumn(format=price_fmt),
    "최고점": st.column_config.NumberColumn(format=price_fmt),
    "현재가(원)": st.column_config.NumberColumn(format="%,.0f"),
    "하락률(%)": st.column_config.NumberColumn(format="%.1f"),
    cap_label: st.column_config.NumberColumn(format="%,.1f"),
    "52주저가대비(%)": st.column_config.NumberColumn(format="%.1f"),
    "지수대비(%p)": st.column_config.NumberColumn(format="%.1f"),
    "RSI": st.column_config.NumberColumn(format="%.0f"),
    "거래량배율": st.column_config.NumberColumn(format="%.2f"),
    "PER": st.column_config.NumberColumn(format="%.1f"),
    "PBR": st.column_config.NumberColumn(format="%.2f"),
    "ROE(%)": st.column_config.NumberColumn(format="%.1f"),
    "부채비율(%)": st.column_config.NumberColumn(format="%.0f"),
    "영업이익증감(%)": st.column_config.NumberColumn(format="%.1f"),
}


def downloads(df: pd.DataFrame, stem: str, key: str) -> None:
    c1, c2 = st.columns(2)
    c1.download_button("CSV 내려받기", df.to_csv(index=False).encode("utf-8-sig"),
                       f"{stem}.csv", "text/csv", key=f"csv_{key}", width="stretch")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="결과")
    c2.download_button("Excel 내려받기", buf.getvalue(), f"{stem}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key=f"xlsx_{key}", width="stretch")


# ───────────────────────── 본문 ─────────────────────────
st.subheader(MARKET_LABELS[market])
st.caption(f"데이터 기준일 {bundle.data_date_str} · 수집 {bundle.fetched_at:%Y-%m-%d %H:%M} · "
           f"조건: {cfg.describe()}")
if warn:
    st.warning(warn)

tab_list, tab_chart, tab_cmp, tab_bt, tab_hist = st.tabs(
    ["스크리닝 결과", "종목 차트", "최고점 기준 비교", "백테스트", "진입·이탈 이력"])

# ── 스크리닝 결과 ──
with tab_list:
    m = st.columns(4)
    m[0].metric("대상 종목", f"{len(res)}개")
    m[1].metric("기본 조건 충족", f"{len(hits)}개")
    m[2].metric("추가 필터 통과", f"{len(filtered)}개")
    m[3].metric("신규 진입", f"{int(filtered['is_new'].sum())}개" if prev is not None else "이력 없음")

    if filtered.empty:
        st.info("조건에 맞는 종목이 없습니다. 하락률 기준을 낮추거나 추가 필터를 해제해 보세요.")
    else:
        table = display_table(filtered)
        st.dataframe(table, column_config=COL_CFG, hide_index=True, width="stretch",
                     height=min(38 * (len(table) + 1), 640))
        downloads(table, f"screener_{market}_{bundle.data_date_str}", "list")

    st.divider()
    if "flash" in ss:
        st.success(ss.pop("flash"))
    c1, c2 = st.columns([1, 2])
    if c1.button("이번 결과를 이력에 저장", width="stretch"):
        r = storage.save_run(market, cfg.key(), bundle.data_date_str, hits)
        if r["saved"]:
            ss["flash"] = f"저장했습니다. 신규 진입 {len(r['new'])} · 이탈 {len(r['exited'])}"
            st.rerun()
        else:
            st.info("같은 기준일·조건의 결과가 이미 저장되어 있습니다.")
    c2.caption("저장한 결과는 다음 실행 때 '신규 진입' 판정 기준이 됩니다. "
               "이력 저장은 추가 필터와 무관하게 기본 조건 충족 종목 전체를 기록합니다.")

# ── 종목 차트 ──
with tab_chart:
    names = bundle.universe.set_index("ticker")["name"]
    all_t = st.toggle("전체 종목에서 선택", value=filtered.empty)
    pool = sorted(bundle.prices) if all_t else filtered["ticker"].tolist()
    if not pool:
        st.info("표시할 종목이 없습니다.")
    else:
        c1, c2 = st.columns([3, 1])
        t = c1.selectbox("종목", pool, format_func=lambda x: f"{names.get(x, x)} ({x})")
        period = c2.selectbox("기간", ["1년", "3년", "5년", "전체"], index=1)
        df = bundle.prices[t]
        close = df["Close"]
        peak_s = rolling_peak(close, cfg.peak_mode, cfg.peak_years)
        ma20, r = close.rolling(20).mean(), rsi(close)
        if period != "전체":
            since = close.index[-1] - pd.DateOffset(years=int(period[0]))
            sel = close.index >= since
            close, peak_s, ma20, r = close[sel], peak_s[sel], ma20[sel], r[sel]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
        fig.add_trace(go.Scatter(x=close.index, y=close, name="종가", line=dict(color="#1F4E79", width=1.6)), 1, 1)
        fig.add_trace(go.Scatter(x=ma20.index, y=ma20, name="20일선", line=dict(color="#8A9BB0", width=1)), 1, 1)
        fig.add_trace(go.Scatter(x=peak_s.index, y=peak_s, name="기준 최고점",
                                 line=dict(color="#2E7D32", width=1, dash="dash")), 1, 1)
        fig.add_trace(go.Scatter(x=peak_s.index, y=peak_s * (1 - cfg.drawdown_pct / 100),
                                 name=f"-{cfg.drawdown_pct:.0f}% 기준선",
                                 line=dict(color="#C62828", width=1, dash="dot")), 1, 1)
        fig.add_trace(go.Scatter(x=[close.index[-1]], y=[close.iloc[-1]], mode="markers+text",
                                 name="현재가", marker=dict(color="#C62828", size=9),
                                 text=[f"{close.iloc[-1]:,.2f}"], textposition="middle left"), 1, 1)
        fig.add_trace(go.Scatter(x=r.index, y=r, name="RSI(14)", line=dict(color="#6A4C93", width=1)), 2, 1)
        fig.add_hline(y=30, line=dict(color="#C62828", width=0.8, dash="dot"), row=2, col=1)
        fig.add_hline(y=70, line=dict(color="#2E7D32", width=0.8, dash="dot"), row=2, col=1)
        fig.update_layout(height=620, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                          legend=dict(orientation="h", y=1.04, x=0))
        fig.update_yaxes(range=[0, 100], row=2, col=1)
        st.plotly_chart(fig, width="stretch")

        row = res[res["ticker"] == t]
        if not row.empty:
            x = row.iloc[0]
            k = st.columns(4)
            k[0].metric("최고점 대비", f"{x['drawdown']:.1f}%")
            k[1].metric("최고점일", pd.Timestamp(x["peak_date"]).strftime("%Y-%m-%d"),
                        f"{x['days_since_peak']}일 경과", delta_color="off")
            k[2].metric("조건 지속", f"{x['streak']}거래일")
            k[3].metric("지수 대비", "-" if pd.isna(x["rel_drawdown"]) else f"{x['rel_drawdown']:+.1f}%p")

# ── 최고점 기준 비교 ──
with tab_cmp:
    st.caption("하락률·지속일 조건은 그대로 두고 최고점 기준만 바꿔 결과를 나란히 비교합니다.")
    modes = [("ath", "사상 최고가"), ("52w", "52주 최고가"), ("nyears", f"{peak_years}년 최고가")]
    merged, counts = None, []
    for mode, label in modes:
        c = ScreenConfig(mode, peak_years, float(dd_pct), int(streak))
        h = screen(c)
        h = h[h["hit"]][["ticker", "name", "drawdown"]].rename(columns={"drawdown": f"{label} 하락률(%)"})
        counts.append((label, len(h)))
        merged = h if merged is None else merged.merge(h, on=["ticker", "name"], how="outer")
    cols = st.columns(3)
    for col, (label, n) in zip(cols, counts):
        col.metric(label, f"{n}개")
    if merged is None or merged.empty:
        st.info("어느 기준에서도 조건을 충족한 종목이 없습니다.")
    else:
        dd_cols = [c for c in merged.columns if c.endswith("하락률(%)")]
        merged["해당 기준 수"] = merged[dd_cols].notna().sum(axis=1)
        merged = merged.sort_values(["해당 기준 수", dd_cols[0]], ascending=[False, True])
        merged = merged.rename(columns={"ticker": "티커", "name": "종목명"})
        st.dataframe(merged, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.1f") for c in dd_cols})
        st.caption("사상 최고가 기준에만 걸리는 종목은 오래전 고점에서 회복하지 못한 경우, "
                   "52주 기준에만 걸리는 종목은 최근 조정을 받은 경우가 많습니다.")
        downloads(merged, f"compare_{market}_{bundle.data_date_str}", "cmp")

# ── 백테스트 ──
with tab_bt:
    st.warning("생존편향 주의: 과거 시점의 지수 구성 종목 이력을 무료로 구할 수 없어 '현재' 구성 종목으로 "
               "검증합니다. 그 사이 지수에서 탈락하거나 상장폐지된 종목이 빠져 있어 결과가 실제보다 좋게 "
               "나올 수 있습니다. 또한 급락장에는 여러 종목이 동시에 신호를 내므로 표본이 특정 시기에 몰릴 수 있습니다.")
    c1, c2 = st.columns([2, 1])
    years = c1.slider("검증 기간 (최근 N년)", 3, 12, 10)
    c1.caption(f"현재 조건: {cfg.describe()} · 조건을 처음 충족한 날 종가로 진입한 것으로 가정")
    bk = (market, str(bundle.fetched_at), cfg.key(), years)
    if c2.button("백테스트 실행", type="primary", width="stretch"):
        bar = st.progress(0.0, text="백테스트 준비 중")
        ss.backtests[bk] = run_backtest(bundle, cfg, years, lambda p, m: bar.progress(p, text=m))
        bar.empty()
    if bk in ss.backtests:
        sig, summ = ss.backtests[bk]
        if sig.empty:
            st.info("검증 기간 동안 조건에 진입한 사례가 없습니다.")
        else:
            st.dataframe(summ, hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.1f")
                                        for c in summ.columns if c not in ("보유기간", "표본수")})
            h_label = st.radio("분포 보기", list(HORIZONS.values()), index=1, horizontal=True)
            h = [k for k, v in HORIZONS.items() if v == h_label][0]
            ex = sig[f"excess_{h}"].dropna() if f"excess_{h}" in sig else pd.Series(dtype=float)
            if len(ex):
                fig = go.Figure(go.Histogram(x=ex, nbinsx=40, marker_color="#1F4E79"))
                fig.add_vline(x=0, line=dict(color="#C62828", dash="dot"))
                fig.add_vline(x=ex.mean(), line=dict(color="#2E7D32"),
                              annotation_text=f"평균 {ex.mean():+.1f}%p")
                fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                                  xaxis_title=f"{h_label} 지수 대비 초과수익(%p)", yaxis_title="건수")
                st.plotly_chart(fig, width="stretch")
            with st.expander(f"진입 사례 {len(sig)}건 보기"):
                view = sig.copy()
                view["entry_date"] = pd.to_datetime(view["entry_date"]).dt.strftime("%Y-%m-%d")
                st.dataframe(view, hide_index=True, width="stretch")
                downloads(view, f"backtest_{market}", "bt")

# ── 이력 ──
with tab_hist:
    ev = storage.list_events(market)
    if ev.empty:
        st.info("저장된 이력이 없습니다. '스크리닝 결과' 탭에서 저장하거나 scheduler.py를 실행하면 쌓입니다.")
    else:
        ev["조건"] = ev["params"].map(lambda p: ScreenConfig(**json.loads(p)).describe())
        st.dataframe(ev.drop(columns=["params"]).rename(columns={
            "event_date": "기준일", "market": "시장", "ticker": "티커", "name": "종목명",
            "event": "구분", "drawdown": "하락률(%)"}), hide_index=True, width="stretch",
            column_config={"하락률(%)": st.column_config.NumberColumn(format="%.1f")})
    with st.expander("실행 기록"):
        st.dataframe(storage.list_runs(market), hide_index=True, width="stretch")

st.caption("본 도구는 매수 후보를 추리는 참고용이며 투자 판단과 책임은 사용자에게 있습니다.")
