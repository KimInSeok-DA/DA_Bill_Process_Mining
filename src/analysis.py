"""이벤트 로그로 가설 H1～H6을 확인한다. 가설·판정 기준: docs/의사결정/1015_04_분석_가설.md

- pm4py: 프로세스 지도(직접 이어짐 빈도·대기일 중앙값), 변형 상위
- pandas: 구간 대기(관찰값), 계류(진행 중 의안의 현재 대기 단계), 묶음 처리, 위원회·월별 집계
- 출력: outputs/figures/*.png, outputs/tables/*.csv, outputs/analysis_results.json

실행: .venv/Scripts/python src/analysis.py
"""

import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

warnings.filterwarnings("ignore")
import pm4py  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
QUERY_DATE = pd.Timestamp("2026-10-15")
SOURCE = "출처: 열린국회정보, 국회의원 발의법률안·소위 심사정보(법률안), 2026-10-15 조회"

# H5: 위원회 개편 전후 이름 묶기(1015_04 4절)
RENAME = {
    "기획재정위원회": "재정경제기획위원회",
    "환경노동위원회": "기후에너지환경노동위원회",
    "여성가족위원회": "성평등가족위원회",
}
ALT = {"대안반영폐기", "수정안반영폐기"}

# H1: 표준 구간(A → B). 끝 활동은 묶어서 본다
PAIRS = [
    ("제안", "소관위 회부"),
    ("소관위 회부", "소관위 상정"),
    ("소관위 상정", "소위 회부"),
    ("소위 회부", "소위 상정"),
    ("소위 상정", "소위 처리"),
    ("소위 처리", "소관위 처리"),
    ("소관위 처리", "법사위 회부"),
    ("법사위 회부", "법사위 상정"),
    ("법사위 상정", "법사위 처리"),
    ("법사위 처리", "본회의 처리"),
    ("소관위 처리", "대안 본회의 통과"),
]

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False


def load() -> pd.DataFrame:
    log = pd.read_csv(ROOT / "data" / "processed" / "event_log.csv", dtype={"case_id": str, "bill_no": str})
    log["ts"] = pd.to_datetime(log["date"])
    log["committee_n"] = log["committee"].replace(RENAME)
    # 끝 활동 묶음 이름(구간 계산용)
    log["act2"] = log["activity"]
    log.loc[log["activity"].isin(["가결", "부결", "폐기"]), "act2"] = "본회의 처리"
    log.loc[log["activity"].isin(ALT), "act2"] = "대안 본회의 통과"
    log.loc[log["activity"] == "소관위 재회부", "act2"] = "소관위 회부"
    # 소위가 먼저 심사한 경로의 소관위 상정(step_order 6.5)은 "상정 대기"가 아니라 소위 뒤 전체회의 상정이라 구간 계산에서 뺀다
    log.loc[(log["activity"] == "소관위 상정") & (log["step_order"] > 6), "act2"] = "소관위 상정(소위 뒤)"
    return log


def waiting_state(last: pd.Series) -> str:
    """진행 중 의안의 마지막 이벤트로 지금 기다리는 단계를 정한다."""
    a, o, r = last["activity"], last["step_order"], last["stage_result"]
    if a == "제안":
        return "소관위 회부 대기"
    if a in ("소관위 회부", "소관위 재회부"):
        return "소관위 상정 대기"
    if a == "소관위 상정":
        return "소관위 처리 대기" if o > 6 else "소위 회부 대기"
    if a == "소위 회부":
        return "소위 상정 대기"
    if a == "소위 상정":
        return "소위 처리 대기"
    if a == "소위 처리":
        return "소관위 처리 대기"
    if a == "소관위 처리":
        return "대안 본회의 대기" if r in ALT else ("법사위 회부 대기" if r in ("원안가결", "수정가결") else "기타")
    return {"법사위 회부": "법사위 상정 대기", "법사위 상정": "법사위 처리 대기", "법사위 처리": "본회의 대기"}.get(a, "기타")


STATE_PAIR = {
    "소관위 회부 대기": ("제안", "소관위 회부"),
    "소관위 상정 대기": ("소관위 회부", "소관위 상정"),
    "소위 회부 대기": ("소관위 상정", "소위 회부"),
    "소위 상정 대기": ("소위 회부", "소위 상정"),
    "소위 처리 대기": ("소위 상정", "소위 처리"),
    "소관위 처리 대기": ("소위 처리", "소관위 처리"),
    "법사위 회부 대기": ("소관위 처리", "법사위 회부"),
    "법사위 상정 대기": ("법사위 회부", "법사위 상정"),
    "법사위 처리 대기": ("법사위 상정", "법사위 처리"),
    "본회의 대기": ("법사위 처리", "본회의 처리"),
    "대안 본회의 대기": ("소관위 처리", "대안 본회의 통과"),
}


def km_curve(dur: pd.Series, event: pd.Series) -> pd.Series:
    """Kaplan-Meier 생존곡선: 날짜별로 '아직 다음 단계에 가지 못한 비율'."""
    d = pd.DataFrame({"t": dur, "e": event.astype(int)})
    g = d.groupby("t")["e"].agg(["sum", "size"]).sort_index()
    at_risk = g["size"][::-1].cumsum()[::-1]
    return (1 - g["sum"] / at_risk).cumprod()


def km_median(dur: pd.Series, event: pd.Series) -> float | None:
    """생존곡선이 0.5 아래로 처음 내려가는 날. 끝까지 0.5 위면 None(절반 넘게 아직 기다림)."""
    surv = km_curve(dur, event)
    below = surv[surv <= 0.5]
    return float(below.index[0]) if len(below) else None


def first_and_censor(log: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """의안별 활동 첫 날짜 표와 중도절단 시점(진행 중이면 조회일, 끝났으면 끝 활동 날짜)."""
    first = log.pivot_table(index="case_id", columns="act2", values="ts", aggfunc="min")
    end = log[log["step_order"] == 11].groupby("case_id")["ts"].min()
    censor = pd.Series(QUERY_DATE, index=first.index)
    censor.loc[end.index] = end
    return first, censor


def interval(first: pd.DataFrame, censor: pd.Series, a: str, b: str) -> tuple[pd.Series, pd.Series]:
    """A가 있는 의안의 A → B 대기일과 B 도달 여부(KM 입력). B가 A보다 이른 의안은 뺀다."""
    has_a = first[a].notna() & ~((first[b] - first[a]).dt.days < 0)
    ev = first.loc[has_a, b].notna()
    dur = ((first.loc[has_a, b].fillna(censor[has_a]) - first.loc[has_a, a]).dt.days).clip(lower=0)
    return dur, ev


def observed_waits(log: pd.DataFrame) -> pd.DataFrame:
    first, censor = first_and_censor(log)
    rows = []
    for a, b in PAIRS:
        d = (first[b] - first[a]).dt.days.dropna()
        neg = int((d < 0).sum())
        d = d[d >= 0]
        dur, ev = interval(first, censor, a, b)
        rows.append({"구간": f"{a} → {b}", "A": a, "B": b, "A 있는 의안": len(dur), "관찰 건수": len(d),
                     "중앙값(일)": d.median(), "90분위(일)": d.quantile(0.9), "같은 날 비율": round((d == 0).mean(), 3),
                     "KM 중앙값(일)": km_median(dur, ev), "역순 제외": neg})
    return pd.DataFrame(rows)


def backlog(log: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    prog = log[log["case_status"] == "진행중"]
    last = prog.groupby("case_id").tail(1).copy()
    last["state"] = last.apply(waiting_state, axis=1)
    last["elapsed"] = (QUERY_DATE - last["ts"]).dt.days
    tab = last.groupby("state").agg(건수=("case_id", "size"), 경과중앙값=("elapsed", "median"),
                                    경과90분위=("elapsed", lambda s: s.quantile(0.9))).reset_index()
    tab["비율"] = (tab["건수"] / len(last)).round(3)
    return tab.sort_values("건수", ascending=False), last


def draw_map(log, freq, perf, path, min_freq=600):
    pos = {
        "제안": (0, 11), "소관위 회부": (0, 10), "소관위 상정": (0, 9), "소위 회부": (0, 8), "소위 상정": (0, 7),
        "소위 처리": (0, 6), "소관위 처리": (0, 5), "법사위 회부": (2.4, 4), "법사위 상정": (2.4, 3),
        "법사위 처리": (2.4, 2), "가결": (2.4, 1), "대안반영폐기": (-2.4, 3),
        "수정안반영폐기": (-2.4, 1.9), "철회": (-2.4, 0.9), "폐기": (-0.6, 0.9), "부결": (0.9, 0.9),
    }
    # 같은 세로줄을 건너뛰는 연결은 휘게 그린다(양수: 왼쪽, 음수: 오른쪽으로 휨)
    rad = {("소관위 회부", "소위 회부"): 0.35, ("소관위 상정", "소관위 처리"): 0.3, ("소위 처리", "소관위 상정"): 0.6}
    n_act = log.groupby("activity")["case_id"].nunique()
    edges = {k: v for k, v in freq.items() if v >= min_freq and k[0] in pos and k[1] in pos}
    vmax = max(edges.values())
    fig, ax = plt.subplots(figsize=(11, 13))
    for (a, b), v in sorted(edges.items(), key=lambda x: x[1]):
        (x1, y1), (x2, y2) = pos[a], pos[b]
        r = rad.get((a, b), 0)
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", lw=0.8 + 7 * v / vmax, color="#3b6ea5", alpha=0.75,
                                    shrinkA=18, shrinkB=18, connectionstyle=f"arc3,rad={r}"))
        # 2차 베지어 중간점: 직선 중점 + 0.5·rad·(dy, -dx)
        mx, my = (x1 + x2) / 2 + 0.5 * r * (y2 - y1), (y1 + y2) / 2 - 0.5 * r * (x2 - x1)
        if (a, b) == ("소위 처리", "소관위 상정"):  # 소위 회부 → 소위 상정 글자와 겹치지 않게 아래로
            my -= 0.75
        med = perf[(a, b)]["median"] / 86400
        ha = "right" if mx < -0.05 else "left"
        ax.text(mx + (0.1 if ha == "left" else -0.1), my, f"{v:,}건 · 중앙 {med:.0f}일", fontsize=8.5,
                color="#1f3b5c", ha=ha, va="center", bbox=dict(fc="white", ec="none", alpha=0.8, pad=0.6))
    for a, (x, y) in pos.items():
        end = a in ("가결", "대안반영폐기", "수정안반영폐기", "철회", "폐기", "부결")
        ax.text(x, y, f"{a}\n{int(n_act.get(a, 0)):,}", ha="center", va="center", fontsize=10,
                bbox=dict(boxstyle="round,pad=0.4", fc="#fde9c9" if end else "#e8eef6", ec="#555"))
    ax.set_xlim(-3.8, 4.4)
    ax.set_ylim(0.3, 11.7)
    ax.axis("off")
    ax.set_title(f"의원발의 법률안 처리 흐름(22대, 직접 이어진 횟수 {min_freq}건 이상)\n"
                 "상자: 활동이 있는 의안 수(주황은 끝 활동) · 화살표: 이어진 횟수와 대기일 중앙값", fontsize=13)
    fig.text(0.01, 0.01, SOURCE + f" · 진행 중 의안 포함 · 재회부·철회 등 {min_freq}건 미만 흐름은 생략",
             fontsize=8, color="#666")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def draw_waits(waits, bl, path):
    w = waits.iloc[::-1]
    bl_map = {STATE_PAIR[s]: (n, e) for s, n, e in zip(bl["state"], bl["건수"], bl["경과중앙값"]) if s in STATE_PAIR}
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 6.5), sharey=True)
    y = range(len(w))
    a1.barh(y, w["중앙값(일)"], color="#3b6ea5", label="중앙값")
    a1.scatter(w["90분위(일)"], y, color="#c0392b", marker="|", s=200, label="90분위")
    km = w["KM 중앙값(일)"].where(w["구간"] != "소관위 처리 → 대안 본회의 통과")
    a1.scatter(km, y, color="black", marker="D", s=30, label="KM 중앙값(기다리는 의안 포함)")
    for i, (m, n, k, q) in enumerate(zip(w["중앙값(일)"], w["관찰 건수"], km, w["90분위(일)"])):
        txt = f"{m:.0f}일 (n={n:,})" + (f" · KM {k:.0f}일" if pd.notna(k) and k > m else "")
        a1.text(max(m, q, k if pd.notna(k) else 0) + 10, i, txt, va="center", fontsize=8)
    a1.set_yticks(list(y))
    a1.set_yticklabels(w["구간"])
    a1.set_xlabel("일")
    a1.set_title("구간 대기: 관찰값(두 활동이 모두 있는 의안)과 KM 중앙값")
    a1.set_xlim(0, 820)
    a1.legend(loc="lower right", fontsize=8)
    cnt = [bl_map.get((a, b), (0, 0))[0] for a, b in zip(w["A"], w["B"])]
    ela = [bl_map.get((a, b), (0, 0))[1] for a, b in zip(w["A"], w["B"])]
    a2.barh(y, cnt, color="#e08e45")
    for i, (c, e) in enumerate(zip(cnt, ela)):
        if c:
            a2.text(c + 80, i, f"{c:,}건 · 경과 중앙 {e:.0f}일", va="center", fontsize=8)
    a2.set_xlabel("진행 중 의안 수")
    a2.set_title("지금 이 구간에서 기다리는 의안(조회일 기준)")
    a2.set_xlim(0, max(cnt) * 1.45)
    fig.suptitle("구간별 대기: 끝난 의안의 대기와 지금 기다리는 의안을 함께 본다", fontsize=13)
    fig.text(0.01, 0.005, SOURCE, fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def draw_batch(batch_tab, path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    cols = [c for c in ["대안반영폐기", "가결", "기타"] if c in batch_tab.columns]
    bottom = None
    colors = {"대안반영폐기": "#e08e45", "가결": "#3b6ea5", "기타": "#999"}
    for c in cols:
        ax.bar(batch_tab.index, batch_tab[c], bottom=bottom, label=c, color=colors[c])
        bottom = batch_tab[c] if bottom is None else bottom + batch_tab[c]
    for i, t in enumerate(batch_tab[cols].sum(axis=1)):
        ax.text(i, t, f"{t:,}", ha="center", va="bottom", fontsize=9)
    ax.set_xlabel("같은 날 같은 위원회에서 함께 처리된 의안 수(묶음 크기)")
    ax.set_ylabel("소관위 처리 의안 수")
    ax.set_title("소관위 처리는 대부분 여러 의안을 한꺼번에 처리한다")
    ax.legend()
    fig.text(0.01, 0.005, SOURCE, fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    (OUT / "figures").mkdir(parents=True, exist_ok=True)
    (OUT / "tables").mkdir(parents=True, exist_ok=True)
    log = load()
    res = {"query_date": str(QUERY_DATE.date()), "cases": int(log["case_id"].nunique()), "events": len(log)}

    # pm4py: 직접 이어짐 빈도·대기, 변형
    f = pm4py.format_dataframe(log[["case_id", "activity", "ts"]].copy(), case_id="case_id",
                               activity_key="activity", timestamp_key="ts")
    freq, _, _ = pm4py.discover_dfg(f)
    perf, _, _ = pm4py.discover_performance_dfg(f)
    dfg = pd.DataFrame([{"A": a, "B": b, "빈도": v, "대기 중앙값(일)": perf[(a, b)]["median"] / 86400,
                         "대기 평균(일)": perf[(a, b)]["mean"] / 86400} for (a, b), v in freq.items()])
    dfg.sort_values("빈도", ascending=False).to_csv(OUT / "tables" / "dfg_pm4py.csv", index=False, encoding="utf-8-sig")
    done = f[f["case:concept:name"].isin(log.loc[log["case_status"] == "완료", "case_id"])]
    var = pm4py.get_variants(done)
    vt = pd.DataFrame([{"경로": " > ".join(k), "의안 수": len(v) if not isinstance(v, int) else v} for k, v in var.items()])
    vt = vt.sort_values("의안 수", ascending=False).reset_index(drop=True)
    vt["비율"] = (vt["의안 수"] / vt["의안 수"].sum()).round(3)
    vt.head(10).to_csv(OUT / "tables" / "variants_top10_completed.csv", index=False, encoding="utf-8-sig")
    res["variants_completed"] = {"count": len(vt), "top10_share": round(float(vt["비율"].head(10).sum()), 3)}
    draw_map(log, freq, perf, OUT / "figures" / "fig1_process_map.png")

    # H1, H4: 구간 대기와 계류
    waits = observed_waits(log)
    bl, last = backlog(log)
    waits.to_csv(OUT / "tables" / "h1_waits.csv", index=False, encoding="utf-8-sig")
    bl.to_csv(OUT / "tables" / "h3_backlog_state.csv", index=False, encoding="utf-8-sig")
    draw_waits(waits, bl, OUT / "figures" / "fig2_waits_backlog.png")
    target = "소위 회부 → 소위 상정"
    rank_med = int(waits["중앙값(일)"].rank(ascending=False, method="min")[waits["구간"] == target].iloc[0])
    blp = bl[bl["state"].isin(STATE_PAIR)].copy()
    blp["구간"] = blp["state"].map(lambda s: " → ".join(STATE_PAIR[s]))
    rank_bl = int(blp["건수"].rank(ascending=False, method="min")[blp["구간"] == target].iloc[0])
    km = waits.set_index("구간")["KM 중앙값(일)"]
    res["H1"] = {"target": target, "median_rank": rank_med, "backlog_rank": rank_bl,
                 "support": rank_med == 1 and rank_bl == 1,
                 "km_median": {k: (None if pd.isna(v) else float(v)) for k, v in km.items()}}

    n_prog = int((log.drop_duplicates("case_id")["case_status"] == "진행중").sum())
    sub_wait = bl.loc[bl["state"] == "소위 상정 대기", "건수"].sum()
    res["H3"] = {"in_progress": n_prog, "sub_present_waiting": int(sub_wait),
                 "share": round(sub_wait / n_prog, 3), "support": bool(sub_wait / n_prog > 0.5)}

    law = waits.set_index("구간")
    law_med = float((log.pivot_table(index="case_id", columns="act2", values="ts", aggfunc="min")
                     .pipe(lambda p: (p["법사위 처리"] - p["법사위 회부"]).dt.days.dropna()).median()))
    law_bl = int(bl.loc[bl["state"].isin(["법사위 상정 대기", "법사위 처리 대기"]), "건수"].sum())
    res["H4"] = {"law_submit_to_proc_median": law_med, "law_backlog": law_bl,
                 "law_backlog_share": round(law_bl / n_prog, 4),
                 "cmt_to_law_submit_median": float(law.loc["소관위 처리 → 법사위 회부", "중앙값(일)"]),
                 "law_proc_to_plenary_median": float(law.loc["법사위 처리 → 본회의 처리", "중앙값(일)"]),
                 "support": law_med <= 30 and law_bl / n_prog < 0.05}

    # H2: 묶음 처리
    cmt = log[log["activity"] == "소관위 처리"].copy()
    cmt["batch"] = cmt.groupby(["committee_n", "date"], dropna=False)["case_id"].transform("size")
    cmt["결과"] = cmt["stage_result"].map(lambda r: "대안반영폐기" if r in ALT else ("가결" if r in ("원안가결", "수정가결") else "기타"))
    bins = [0, 1, 4, 9, 19, 49, 10**6]
    labels = ["1", "2～4", "5～9", "10～19", "20～49", "50+"]
    cmt["크기"] = pd.cut(cmt["batch"], bins=bins, labels=labels)
    bt = cmt.pivot_table(index="크기", columns="결과", values="case_id", aggfunc="size", observed=False).fillna(0).astype(int)
    bt.to_csv(OUT / "tables" / "h2_batch.csv", encoding="utf-8-sig")
    draw_batch(bt, OUT / "figures" / "fig3_batch.png")
    share_batch = float((cmt["batch"] >= 2).mean())
    res["H2"] = {"cmt_proc_events": len(cmt), "share_in_batch_ge2": round(share_batch, 3),
                 "median_batch_size_per_event": float(cmt["batch"].median()),
                 "share_ge2_by_result": cmt.groupby("결과")["batch"].apply(lambda s: round(float((s >= 2).mean()), 3)).to_dict(),
                 "meeting_days": int(cmt.groupby(["committee_n", "date"], dropna=False).ngroups),
                 "support": share_batch > 0.5}

    # H5: 상임위원회별 소위 회부 → 소위 상정
    special = log["committee_n"].fillna("").str.contains("특별위원회")
    sub = log[log["activity"].isin(["소위 회부", "소위 상정"]) & ~special & log["committee_n"].notna()]
    p = sub.pivot_table(index=["committee_n", "case_id"], columns="activity", values="ts", aggfunc="min")
    p["wait"] = (p["소위 상정"] - p["소위 회부"]).dt.days
    cm = p.groupby("committee_n").agg(소위회부=("소위 회부", "count"), 소위상정=("소위 상정", "count"),
                                      관찰중앙값=("wait", lambda s: s[s >= 0].median()))
    cm["상정 도달 비율"] = (cm["소위상정"] / cm["소위회부"]).round(3)
    # 보조: 위원회별 KM 중앙값(아직 상정 전인 의안은 조회일 또는 끝 활동 날짜에서 중도절단)
    end = log[log["step_order"] == 11].groupby("case_id")["ts"].min()
    pp = p[p["소위 회부"].notna()].reset_index()
    cens = pp["case_id"].map(end).fillna(QUERY_DATE)
    pp = pp[pp["소위 상정"].isna() | (pp["소위 상정"] >= pp["소위 회부"])]
    cens = cens[pp.index]
    pp["dur"] = (pp["소위 상정"].fillna(cens) - pp["소위 회부"]).dt.days.clip(lower=0)
    cm["KM중앙값"] = pp.groupby("committee_n").apply(lambda g: km_median(g["dur"], g["소위 상정"].notna()))
    wl = last[last["state"] == "소위 상정 대기"].copy()
    wl["committee_n"] = wl["committee_n"]
    w2 = wl.groupby("committee_n").agg(상정대기=("case_id", "size"), 대기경과중앙값=("elapsed", "median"))
    cm = cm.join(w2).fillna({"상정대기": 0}).sort_values("관찰중앙값")
    cm.to_csv(OUT / "tables" / "h5_committee.csv", encoding="utf-8-sig")
    ok = cm[cm["소위상정"] >= 30]
    res["H5"] = {"committees": len(cm), "with_n_ge30": len(ok), "median_min": float(ok["관찰중앙값"].min()),
                 "median_max": float(ok["관찰중앙값"].max()),
                 "ratio": round(float(ok["관찰중앙값"].max() / max(ok["관찰중앙값"].min(), 1)), 2),
                 "reach_min": float(ok["상정 도달 비율"].min()), "reach_max": float(ok["상정 도달 비율"].max()),
                 "km_min": float(ok["KM중앙값"].min()), "km_max": float(ok["KM중앙값"].max()),
                 "km_undefined": int(ok["KM중앙값"].isna().sum()),
                 "support": bool(ok["관찰중앙값"].max() >= 2 * max(ok["관찰중앙값"].min(), 1))}

    # H6: 월별 처리
    ends = log[log["step_order"] == 11].copy()
    ends["월"] = ends["ts"].dt.to_period("M").astype(str)
    cmt["월"] = cmt["ts"].dt.to_period("M").astype(str)
    months = pd.period_range(log["ts"].min(), QUERY_DATE, freq="M").astype(str)
    mt = pd.DataFrame({"끝 활동": ends.groupby("월").size(), "끝 활동(철회 제외)": ends[ends["activity"] != "철회"].groupby("월").size(),
                       "소관위 처리": cmt.groupby("월").size()}).reindex(months).fillna(0).astype(int)
    mt.to_csv(OUT / "tables" / "h6_monthly.csv", encoding="utf-8-sig")
    top3 = mt["끝 활동"].sort_values(ascending=False).head(3)
    res["H6"] = {"months": len(mt), "top3_months": top3.to_dict(), "top3_share": round(float(top3.sum() / mt["끝 활동"].sum()), 3),
                 "uniform_share": round(3 / len(mt), 3),
                 "plenary_days": int(ends[ends["activity"] != "철회"]["date"].nunique()),
                 "support": float(top3.sum() / mt["끝 활동"].sum()) >= 0.30}

    (OUT / "analysis_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(waits[["구간", "A 있는 의안", "관찰 건수", "중앙값(일)", "90분위(일)", "같은 날 비율", "KM 중앙값(일)", "역순 제외"]].to_string(index=False))
    print(bl.to_string(index=False))


if __name__ == "__main__":
    main()
