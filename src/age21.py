"""21대를 22대 조회일과 같은 경과일(개원 뒤 868일)에서 자른 것처럼 만들어 대기일 추정값을 정답과 비교한다. 정의: docs/의사결정/1016_09

- 입력: data/processed/age21/event_log.csv(build_event_log.py --age 21), 22대 비교용 data/processed/event_log.csv·outputs/tables/h1_waits.csv
- 구간별로 T(2022-10-15) 전에 구간 시작이 있는 원안에 대해
  ① T 시점 끝난 의안 기준 중앙값, ② T 시점 KM 중앙값, ③ 정답(같은 의안을 임기 끝까지 본 KM 중앙값)을 계산한다
- 계산 함수(구간 대기·KM)는 22대 분석(analysis.py)과 같은 것을 쓴다. SQL 재계산은 cross_check.py ⑫
- 출력: outputs/age21/age21_results.json, outputs/age21/tables/*.csv, outputs/age21/fig12_truncation.png

실행: .venv/Scripts/python src/age21.py
"""

import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analysis import PAIRS, QUERY_DATE, first_and_censor, interval, km_median, load, waiting_state

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "age21"
OPEN = {21: pd.Timestamp("2020-05-30"), 22: pd.Timestamp("2024-05-30")}
TERM_END_21 = pd.Timestamp("2024-05-29")
DAYS = (QUERY_DATE - OPEN[22]).days  # 868: 22대 조회일까지 경과일
T = OPEN[21] + pd.Timedelta(days=DAYS)
STD_PAIRS = [p for p in PAIRS if p[1] != "대안 본회의 통과"]  # 21대는 대안 연결이 없음(1016_09 4절)
KEY = ("소위 회부", "소위 상정")
CURVE_DAYS = list(range(180, 1441, 60))
SOURCE = ("출처: 열린국회정보, 국회의원 발의법률안·법률안 심사 및 처리(의안검색)·소위 심사정보(법률안), "
          f"{QUERY_DATE.date()} 조회")

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False


def inf(x) -> float:
    """KM 중앙값이 없으면(끝까지 절반 넘게 도달 못 함) 관찰 기간보다 길다고 보고 ∞로 둔다."""
    return math.inf if x is None or pd.isna(x) else float(x)


def closer(est: float, other: float, truth: float) -> bool:
    """est가 other보다 truth에 가까운가(1016_09 5절). ∞가 섞이면 큰 쪽이 가깝다."""
    if math.isinf(truth):
        return (math.isinf(est) and not math.isinf(other)) or (not math.isinf(other) and est > other)
    if math.isinf(est):
        return False
    if math.isinf(other):
        return True
    return abs(est - truth) < abs(other - truth)


def cut_estimates(orig: pd.DataFrame, cut: pd.Timestamp, pairs) -> pd.DataFrame:
    """절단 시점 cut에서의 ①·②와, 같은 의안을 끝까지 본 ③."""
    first_f, censor_f = first_and_censor(orig, query_date=TERM_END_21)
    lt = orig[orig["ts"] < cut]
    first_c, censor_c = first_and_censor(lt, query_date=cut)
    rows = []
    for a, b in pairs:
        dur_c, ev_c = interval(first_c, censor_c, a, b)
        dur_f, ev_f = interval(first_f, censor_f, a, b)
        assert set(dur_c.index) <= set(dur_f.index)  # T 전에 시작한 의안은 끝까지 본 표에도 같은 조건으로 있다
        dur_f, ev_f = dur_f[dur_c.index], ev_f[dur_c.index]
        rows.append({"구간": f"{a} → {b}", "A": a, "B": b, "대상 의안": len(dur_c),
                     "T 전 도달": int(ev_c.sum()),
                     "① 끝난 의안 기준": dur_c[ev_c].median() if ev_c.any() else None,
                     "② T 시점 KM": km_median(dur_c, ev_c),
                     "③ 정답(끝까지 KM)": km_median(dur_f, ev_f),
                     "임기 끝까지 도달 비율": round(float(ev_f.mean()), 3) if len(ev_f) else None,
                     "끝까지 본 끝난 의안 기준": dur_f[ev_f].median() if ev_f.any() else None})
    t = pd.DataFrame(rows)
    t["②가 더 가까움"] = [closer(inf(k), inf(o), inf(g)) for k, o, g in
                       zip(t["② T 시점 KM"], t["① 끝난 의안 기준"], t["③ 정답(끝까지 KM)"])]
    return t


def state_shares(log: pd.DataFrame, cut: pd.Timestamp) -> dict:
    """cut 시점의 처리 비율과 진행 중 의안의 대기 단계 비율."""
    lt = log[log["ts"] < cut]
    done = set(lt.loc[lt["step_order"] == 11, "case_id"])
    cases = lt["case_id"].unique()
    last = lt[~lt["case_id"].isin(done)].groupby("case_id").tail(1)
    states = last.apply(waiting_state, axis=1).value_counts()
    return {"cases": len(cases), "completed": len(done), "completed_share": round(len(done) / len(cases), 3),
            "in_progress": len(last), "sub_present_waiting": int(states.get("소위 상정 대기", 0)),
            "sub_present_waiting_share": round(float(states.get("소위 상정 대기", 0) / len(last)), 3)}


def draw(main: pd.DataFrame, curve: pd.DataFrame, path: Path) -> None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 6.2), gridspec_kw={"width_ratios": [1, 1.15]})
    k = curve[curve["구간"] == " → ".join(KEY)]
    a1.plot(k["경과일"], k["③ 정답(끝까지 KM)"], color="black", lw=2.2, label="③ 정답: 임기 끝까지 본 값")
    a1.plot(k["경과일"], k["② T 시점 KM"], color="#3b6ea5", marker="o", ms=4, label="② 그 시점의 KM(기다리는 법률안 포함)")
    a1.plot(k["경과일"], k["① 끝난 의안 기준"], color="#e08e45", marker="s", ms=4, label="① 그 시점에 끝난 법률안 기준")
    m868 = main[main["구간"] == " → ".join(KEY)].iloc[0]  # 868일은 60일 간격 시점에 없어 따로 찍는다
    for col, c, mk in (("③ 정답(끝까지 KM)", "black", "_"), ("② T 시점 KM", "#3b6ea5", "o"), ("① 끝난 의안 기준", "#e08e45", "s")):
        a1.scatter(DAYS, m868[col], color=c, marker=mk, s=110 if mk == "_" else 70, lw=2.5, edgecolor="#c0392b", zorder=5)
        a1.annotate(f"{m868[col]:.0f}일", (DAYS, m868[col]), xytext=(-10, 14 if mk == "s" else 0), textcoords="offset points",
                    ha="right", va="center", fontsize=8.5, color=c, fontweight="bold",
                    bbox=dict(fc="white", ec="none", alpha=0.85, pad=0.5))
    a1.axvline(DAYS, color="#c0392b", ls="--", lw=1)
    a1.text(DAYS + 12, 0.22, f"{DAYS}일\n(22대 조회일과 같은 경과일)", color="#c0392b", fontsize=8.5, va="center",
            transform=a1.get_xaxis_transform())
    a1.set_xlabel("21대 개원 뒤 날수(이 시점까지의 기록만 있다고 가정)")
    a1.set_ylabel("소위 회부 → 소위 상정 대기(일, 중앙값)")
    a1.set_title("자른 시점별 추정값과 정답(소위 회부 → 소위 상정)")
    a1.legend(fontsize=8.5, loc="upper left", bbox_to_anchor=(0, 0.88))
    a1.set_ylim(bottom=0)

    m = main.iloc[::-1].reset_index(drop=True)
    xmax = max(v for v in pd.concat([m["① 끝난 의안 기준"], m["② T 시점 KM"], m["③ 정답(끝까지 KM)"]]).dropna()) * 1.25
    for i, r in m.iterrows():
        g = inf(r["③ 정답(끝까지 KM)"])
        vals = [x for x in (r["① 끝난 의안 기준"], r["② T 시점 KM"], None if math.isinf(g) else g) if pd.notna(x)]
        a2.plot([min(vals), max(vals)], [i, i], color="#ccc", lw=2, zorder=1)
        a2.scatter(r["① 끝난 의안 기준"], i, color="#e08e45", marker="s", s=40, zorder=3)
        if pd.notna(r["② T 시점 KM"]):
            a2.scatter(r["② T 시점 KM"], i, color="#3b6ea5", marker="o", s=40, zorder=3)
        else:
            a2.text(xmax * 0.97, i + 0.22, "② 없음(절반 넘게 기다림)", ha="right", fontsize=7.5, color="#3b6ea5")
        if math.isinf(g):
            a2.text(xmax * 0.97, i - 0.3, "③ 없음(끝까지 절반 넘게 못 감)", ha="right", fontsize=7.5)
        else:
            a2.scatter(g, i, color="black", marker="|", s=260, lw=2.5, zorder=4)
    a2.set_yticks(range(len(m)))
    a2.set_yticklabels(m["구간"], fontsize=9)
    a2.set_xlim(0, xmax)
    a2.set_xlabel("대기(일, 중앙값)")
    a2.set_title(f"개원 뒤 {DAYS}일 시점: 구간별 ①·②와 정답 ③(검은 세로선)")
    fig.suptitle("21대로 확인: 끝난 법률안만 보면 대기를 짧게 보고, 기다리는 법률안까지 넣으면 정답에 가까워지나", fontsize=13)
    fig.text(0.01, 0.005, SOURCE + " · 21대 원안(의원·정부), 지금의 최종 기록을 날짜로 자른 것", fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    (OUT / "tables").mkdir(parents=True, exist_ok=True)
    log21 = load(ROOT / "data" / "processed" / "age21" / "event_log.csv")
    orig21 = log21[log21["case_group"] == "원안"].copy()
    assert (orig21["case_status"] == "완료").all() and orig21["ts"].max() <= TERM_END_21

    main_t = cut_estimates(orig21, T, STD_PAIRS)
    main_t.to_csv(OUT / "tables" / "truncation_868.csv", index=False, encoding="utf-8-sig")

    curve = []
    for d in CURVE_DAYS:
        c = cut_estimates(orig21, OPEN[21] + pd.Timedelta(days=d), STD_PAIRS)
        c.insert(0, "경과일", d)
        curve.append(c)
    curve = pd.concat(curve, ignore_index=True)
    curve.to_csv(OUT / "tables" / "truncation_curve.csv", index=False, encoding="utf-8-sig")

    key = main_t.set_index("구간").loc[" → ".join(KEY)]
    o, k, g = inf(key["① 끝난 의안 기준"]), inf(key["② T 시점 KM"]), inf(key["③ 정답(끝까지 KM)"])
    n_closer = int(main_t["②가 더 가까움"].sum())
    res = {
        "days": DAYS, "cut_date": str(T.date()), "term_end": str(TERM_END_21.date()),
        "cases_21": int(orig21["case_id"].nunique()),
        "final_result_21": orig21.drop_duplicates("case_id")["final_result"].value_counts().to_dict(),
        "T1": {"interval": " → ".join(KEY), "observed_at_T": o, "truth": g, "support": o < g / 2},
        "T2": {"km_at_T": k, "observed_at_T": o, "truth": g, "support": closer(k, o, g)},
        "T3": {"intervals": len(main_t), "km_closer": n_closer, "support": n_closer >= 6},
        "table": main_t.drop(columns=["A", "B"]).to_dict("records"),
    }

    # 같은 경과일의 21대와 22대(서술만, 1016_09 6절)
    log22 = load()
    orig22 = log22[log22["case_group"] == "원안"]
    h1_22 = pd.read_csv(ROOT / "outputs" / "tables" / "h1_waits.csv").set_index("구간")
    cmp = main_t[["구간", "대상 의안", "① 끝난 의안 기준", "② T 시점 KM", "③ 정답(끝까지 KM)"]].rename(
        columns={"대상 의안": "21대 대상 의안", "① 끝난 의안 기준": "21대 ①", "② T 시점 KM": "21대 ②",
                 "③ 정답(끝까지 KM)": "21대 ③"}).set_index("구간")
    cmp["22대 대상 의안"] = h1_22["A 있는 의안"]
    cmp["22대 ①"] = h1_22["중앙값(일)"]
    cmp["22대 ②"] = h1_22["KM 중앙값(일)"]
    cmp.reset_index().to_csv(OUT / "tables" / "compare_22_same_day.csv", index=False, encoding="utf-8-sig")
    res["same_day"] = {"21": state_shares(orig21, T), "22": state_shares(orig22, QUERY_DATE + pd.Timedelta(days=1))}

    draw(main_t, curve, OUT / "fig12_truncation.png")
    (OUT / "age21_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(main_t.drop(columns=["A", "B"]).to_string(index=False))
    print(json.dumps({k: v for k, v in res.items() if k != "table"}, ensure_ascii=False, indent=1, default=float))
    print(cmp.to_string())


if __name__ == "__main__":
    main()
