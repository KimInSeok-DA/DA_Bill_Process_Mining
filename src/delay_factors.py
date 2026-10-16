"""어떤 법률안이 소위 상정까지 오래 기다리나(소위 회부 → 소위 상정). 정의: docs/의사결정/1016_11

- 대상: 22대 원안 중 소위 회부 기록이 있는 법률안. 대기·중도절단·KM은 analysis.py와 같은 함수
- 특징: F1 같은 법을 고치는 법률안 수, F2 법률안 종류, F3 제안자. 전체 비교 + 위원회 안 비교(두 집단 각 30건 이상)
- 출력: outputs/tables/delay_*.csv, outputs/figures/fig13_delay_factors.png, outputs/delay_results.json

실행: .venv/Scripts/python src/delay_factors.py
"""

import json
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analysis import QUERY_DATE, SOURCE, first_and_censor, interval, km_curve, km_median, load

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
A, B = "소위 회부", "소위 상정"
MIN_N = 30  # 위원회 안 비교에서 집단별 최소 건수
MIN_COMMITTEES = 5
F1_BINS = [0, 1, 4, 9, 49, 10**6]
F1_LABELS = ["1건(혼자)", "2～4", "5～9", "10～49", "50건 이상"]
ONE_YEAR = 365  # 보조 지표(결과를 본 뒤 추가, 1016_11 7절): 1년 안에 상정된 비율

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False


def law_name(bill_name: str) -> str:
    """법률안 이름 → 법 이름(1016_11 3절)."""
    s = re.sub(r"\s*(일부개정|전부개정|폐지)법률안$", "", bill_name.strip())
    return s[:-1] if s == bill_name.strip() and s.endswith("안") else s


def bill_kind(bill_name: str) -> str:
    for k in ("일부개정", "전부개정", "폐지"):
        if k in bill_name:
            return "전부개정·폐지" if k != "일부개정" else k
    return "제정"


def reached_by(dur: pd.Series, ev: pd.Series, t: int = ONE_YEAR) -> float:
    """KM으로 계산한 t일 안에 다음 단계에 간 비율(1 - 생존율). 중앙값과 달리 늘 계산된다."""
    surv = km_curve(dur, ev)
    s = surv[surv.index <= t]
    return round(float(1 - (s.iloc[-1] if len(s) else 1.0)), 4)


def inf(x) -> float:
    return math.inf if x is None or pd.isna(x) else float(x)


def bill_names() -> pd.Series:
    d = ROOT / "data" / "raw" / "TVBPMBILL11" / f"AGE22_{QUERY_DATE:%Y%m%d}"
    rows = []
    for p in sorted(d.glob("p*.json")):
        rows += json.loads(p.read_text(encoding="utf-8"))["TVBPMBILL11"][1]["row"]
    return pd.DataFrame(rows).set_index("BILL_ID")["BILL_NAME"]


def group_table(df: pd.DataFrame, col: str, order: list[str]) -> pd.DataFrame:
    rows = []
    for g in order:
        s = df[df[col] == g]
        rows.append({"특징": col, "집단": g, "법률안": len(s), "상정 도달": int(s["ev"].sum()),
                     "도달 비율": round(float(s["ev"].mean()), 3) if len(s) else None,
                     "끝난 법률안 기준 중앙값": s.loc[s["ev"], "dur"].median() if s["ev"].any() else None,
                     "KM 중앙값": km_median(s["dur"], s["ev"]) if len(s) else None,
                     "1년 안 상정 비율(보조)": reached_by(s["dur"], s["ev"]) if len(s) else None})
    return pd.DataFrame(rows)


def within(df: pd.DataFrame, col: str, slow: list[str], fast: list[str]) -> tuple[pd.DataFrame, dict]:
    """위원회마다 slow 집단과 fast 집단의 KM 중앙값을 비교한다(각 MIN_N건 이상인 위원회만)."""
    d = df[~df["special"]]
    rows = []
    for c, g in d.groupby("committee_n"):
        s, f = g[g[col].isin(slow)], g[g[col].isin(fast)]
        if len(s) < MIN_N or len(f) < MIN_N:
            continue
        ms, mf = inf(km_median(s["dur"], s["ev"])), inf(km_median(f["dur"], f["ev"]))
        rows.append({"특징": col, "위원회": c, "느린 쪽 가설 집단": "·".join(slow), "건수 1": len(s),
                     "KM 중앙값 1": None if math.isinf(ms) else ms, "빠른 쪽 가설 집단": "·".join(fast), "건수 2": len(f),
                     "KM 중앙값 2": None if math.isinf(mf) else mf, "같은 방향": ms > mf,
                     "둘 다 중앙값 없음": math.isinf(ms) and math.isinf(mf),
                     "1년 안 상정 비율 1(보조)": reached_by(s["dur"], s["ev"]),
                     "1년 안 상정 비율 2(보조)": reached_by(f["dur"], f["ev"])})
    t = pd.DataFrame(rows)
    t["같은 방향(보조)"] = t["1년 안 상정 비율 1(보조)"] < t["1년 안 상정 비율 2(보조)"]
    n, same = len(t), int(t["같은 방향"].sum()) if len(t) else 0
    return t, {"committees": n, "same_direction": same,
               "majority": None if n < MIN_COMMITTEES else same > n / 2,
               "both_undefined": int(t["둘 다 중앙값 없음"].sum()),
               "same_direction_1y_post_hoc": int(t["같은 방향(보조)"].sum())}


def main() -> None:
    log = load()
    log = log[log["case_group"] == "원안"]
    first, censor = first_and_censor(log)
    dur, ev = interval(first, censor, A, B)
    df = pd.DataFrame({"dur": dur, "ev": ev})

    names = bill_names()
    df["bill_name"] = names.reindex(df.index).values
    assert df["bill_name"].notna().all()
    orig_ids = log["case_id"].unique()
    laws = names.reindex(orig_ids).map(law_name)
    df["law"] = df["bill_name"].map(law_name)
    df["F1 같은 법 법률안 수"] = pd.cut(df["law"].map(laws.value_counts()), bins=F1_BINS, labels=F1_LABELS).astype(str)
    df["F2 법률안 종류"] = df["bill_name"].map(bill_kind)
    kind = log.drop_duplicates("case_id").set_index("case_id")["proposer_kind"]
    df["F3 제안자"] = kind.reindex(df.index).values
    sref = log[log["activity"] == A].sort_values("ts").drop_duplicates("case_id").set_index("case_id")["committee_n"]
    df["committee_n"] = sref.reindex(df.index).values
    df["special"] = df["committee_n"].fillna("").str.contains("특별위원회")

    groups = pd.concat([
        group_table(df, "F1 같은 법 법률안 수", F1_LABELS),
        group_table(df, "F2 법률안 종류", ["일부개정", "제정", "전부개정·폐지"]),
        group_table(df, "F3 제안자", ["의원", "정부"]),
    ], ignore_index=True)
    groups.to_csv(OUT / "tables" / "delay_groups.csv", index=False, encoding="utf-8-sig")

    big = ["10～49", "50건 이상"]
    w1, s1 = within(df, "F1 같은 법 법률안 수", ["1건(혼자)"], big)
    w2, s2 = within(df, "F2 법률안 종류", ["제정"], ["일부개정"])
    w3, s3 = within(df, "F3 제안자", ["의원"], ["정부"])
    pd.concat([w1, w2, w3], ignore_index=True).to_csv(OUT / "tables" / "delay_within_committee.csv", index=False,
                                                     encoding="utf-8-sig")

    alone = df[df["F1 같은 법 법률안 수"] == "1건(혼자)"]
    many = df[df["F1 같은 법 법률안 수"].isin(big)]
    m_alone, m_many = inf(km_median(alone["dur"], alone["ev"])), inf(km_median(many["dur"], many["ev"]))
    enact, part = df[df["F2 법률안 종류"] == "제정"], df[df["F2 법률안 종류"] == "일부개정"]
    m_enact, m_part = inf(km_median(enact["dur"], enact["ev"])), inf(km_median(part["dur"], part["ev"]))

    def ok(a, b, ratio):
        return math.isinf(a) and not math.isinf(b) or (not math.isinf(a) and a >= ratio * b)

    def keep(x):
        return None if math.isinf(x) else x

    res = {
        "query_date": str(QUERY_DATE.date()), "cases": len(df), "reached": int(df["ev"].sum()),
        "laws": int(laws.nunique()),
        "D1": {"alone_km": keep(m_alone), "ten_plus_km": keep(m_many), "n_alone": len(alone), "n_ten_plus": len(many),
               "overall": ok(m_alone, m_many, 2), "within": s1},
        "D2": {"enact_km": keep(m_enact), "partial_km": keep(m_part), "n_enact": len(enact), "n_partial": len(part),
               "overall": ok(m_enact, m_part, 1.5), "within": s2},
        "D3": {"within": s3},
    }
    res["D1"]["support"] = bool(res["D1"]["overall"] and s1["majority"] is not False)
    res["D2"]["support"] = bool(res["D2"]["overall"] and s2["majority"] is not False)
    res["D3"]["support"] = bool(s3["majority"])

    # 보조: 같은 날 같은 위원회에서 같은 법의 다른 법률안과 함께 소위 상정된 비율
    pres = log[log["activity"] == B][["case_id", "date", "committee_n"]].copy()
    pres["law"] = names.reindex(pres["case_id"]).map(law_name).values
    pres["n"] = pres.groupby(["committee_n", "date", "law"], dropna=False)["case_id"].transform("size")
    res["same_law_same_day_share"] = round(float((pres["n"] >= 2).mean()), 3)
    res["sub_present_events"] = len(pres)

    draw(df, groups, OUT / "figures" / "fig13_delay_factors.png")
    (OUT / "delay_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(groups.to_string(index=False))
    print(pd.concat([w1, w2, w3]).to_string(index=False))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))


def draw(df: pd.DataFrame, groups: pd.DataFrame, path: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6), sharey=True)
    colors = ["#c0392b", "#e08e45", "#9b9b9b", "#5b8fc7", "#1f4e89"]
    for ax, col in zip(axes, ["F1 같은 법 법률안 수", "F2 법률안 종류", "F3 제안자"]):
        g = groups[groups["특징"] == col]
        for i, (name, n, km) in enumerate(zip(g["집단"], g["법률안"], g["KM 중앙값"])):
            s = df[df[col] == name]
            if len(s) < MIN_N:
                continue
            curve = km_curve(s["dur"], s["ev"])
            c = colors[i] if col.startswith("F1") else ["#c0392b", "#1f4e89", "#9b9b9b"][i]
            lab = f"{name} (n={n:,}) · " + (f"중앙값 {km:.0f}일" if pd.notna(km) else "중앙값 없음(절반 넘게 기다림)")
            ax.step([0, *curve.index], [1, *curve.values], where="post", color=c, lw=1.8, label=lab)
        ax.axhline(0.5, color="#999", ls=":", lw=1)
        ax.set_title(col[3:])
        ax.set_xlabel("소위 회부 뒤 날수")
        ax.set_xlim(0, 860)
        ax.legend(fontsize=8, loc="upper right")
    axes[0].set_ylabel("아직 소위에 상정되지 않은 비율")
    fig.suptitle("어떤 법률안이 소위 상정까지 오래 기다리나 (22대 원안, 기다리는 법률안 포함 생존곡선)", fontsize=13)
    fig.text(0.01, 0.005, SOURCE + " · 관련이 있다는 뜻이지 원인이라는 뜻은 아님", fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
