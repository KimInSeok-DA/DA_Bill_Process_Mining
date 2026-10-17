"""소위는 같은 법률안을 몇 번 심사하나(위원회 회의정보). 정의: docs/의사결정/1016_10

- 입력: data/raw/BILLJUDGECONF/AGE22_<조회일>/<BILL_ID>.json(법률안마다 회의별 기록), 이벤트 로그 v2
- 소위 상정 횟수 = "소위회의" + 결과 "상정" 행의 서로 다른 회의 날짜 수. 반복 심사 = 2회 이상
- 정합성 C(첫 소위 상정 날짜가 이벤트 로그와 같은 비율) → 가설 R1～R3
- 출력: outputs/tables/meetings_*.csv, outputs/figures/fig14_meetings.png, outputs/meetings_results.json,
  data/processed/meeting_counts.csv(법률안별, 올리지 않음)

실행: .venv/Scripts/python src/meetings.py
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from analysis import QUERY_DATE, SOURCE, load
from delay_factors import F1_BINS, F1_LABELS, bill_names, law_name

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
CONF_DATE = "20261016"
SERVICE = "BILLJUDGECONF"
MIN_N = 30
SOURCE_CONF = f"출처: 열린국회정보, 의안 위원회심사 회의정보 조회, {CONF_DATE[:4]}-{CONF_DATE[4:6]}-{CONF_DATE[6:]} 조회"

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False


def load_conf() -> tuple[pd.DataFrame, list[str], list[str]]:
    """회의 기록 행 전체와, 자료 없음(INFO-200) 법률안, 받은 법률안 목록."""
    d = ROOT / "data" / "raw" / SERVICE / f"AGE22_{CONF_DATE}"
    rows, empty, ids = [], [], []
    for p in sorted(d.glob("*.json")):
        if p.name == "manifest.json":
            continue
        ids.append(p.stem)
        js = json.loads(p.read_text(encoding="utf-8"))
        if SERVICE not in js:
            empty.append(p.stem)
            continue
        rows += js[SERVICE][1]["row"]
    df = pd.DataFrame(rows)[["BILL_ID", "BILL_NO", "JRCMIT_CONF_NM", "JRCMIT_CONF_DT", "JRCMIT_CONF_RSLT"]]
    df.columns = ["case_id", "bill_no", "conf_nm", "date", "rslt"]
    df["ts"] = pd.to_datetime(df["date"])
    return df, empty, ids


def main() -> None:
    conf, empty, ids = load_conf()
    n_rows = len(conf)
    late = conf["ts"] > QUERY_DATE  # 1016_10 2절: 이벤트 로그 조회일보다 뒤인 회의는 버린다
    conf = conf[~late]
    sub = conf[conf["conf_nm"].str.contains("소위회의")]
    pres = sub[sub["rslt"] == "상정"].drop_duplicates(["case_id", "ts"]).sort_values(["case_id", "ts"])

    have = sorted(set(ids) - set(empty))
    per = pres.groupby("case_id")["ts"].agg(n="size", first="first",
                                             second=lambda s: s.iloc[1] if len(s) > 1 else pd.NaT)
    per = per.reindex(have)
    no_present = per["n"].isna()
    per = per[~no_present].copy()
    per["n"] = per["n"].astype(int)
    per["gap"] = (per["second"] - per["first"]).dt.days
    per["repeat"] = per["n"] >= 2

    # 특징: 제안자, 위원회(첫 소위 상정 이벤트), 같은 법 법률안 수(1016_11 F1)
    log = load()
    log = log[log["case_group"] == "원안"]
    kind = log.drop_duplicates("case_id").set_index("case_id")["proposer_kind"]
    per["proposer"] = kind.reindex(per.index).values
    lp = log[log["activity"] == "소위 상정"].sort_values("ts").drop_duplicates("case_id").set_index("case_id")
    per["committee"] = lp["committee_n"].reindex(per.index).values
    names = bill_names()
    laws = names.reindex(log["case_id"].unique()).map(law_name)
    per["f1"] = pd.cut(names.reindex(per.index).map(law_name).map(laws.value_counts()), bins=F1_BINS,
                       labels=F1_LABELS).astype(str)

    # C 정합성: 첫 소위 상정 날짜 = 이벤트 로그 첫 "소위 상정" 날짜(분모: 자료가 있는 법률안, 소위 상정 없음은 불일치)
    ev_first = lp["ts"].reindex(have)
    conf_first = per["first"].reindex(have)
    c_match = int((conf_first == ev_first).sum())
    c_share = c_match / len(have)

    # R1～R3
    rep = per[per["repeat"]]
    r1 = float(per["repeat"].mean())
    r2 = float(rep["gap"].median()) if len(rep) else None
    alone, big = per[per["f1"] == "1건(혼자)"], per[per["f1"] == "50건 이상"]
    r3_ok = len(alone) >= MIN_N and len(big) >= MIN_N
    r3a, r3b = float(alone["repeat"].mean()), float(big["repeat"].mean())

    # 보조
    dist = per["n"].clip(upper=4).value_counts().sort_index()
    dist.index = ["1회", "2회", "3회", "4회 이상"][:len(dist)]
    by_prop = per.groupby("proposer")["repeat"].agg(["size", "mean"])
    by_cmt = per[~per["committee"].fillna("").str.contains("특별위원회")].groupby("committee")["repeat"].agg(["size", "mean"])
    by_cmt = by_cmt[by_cmt["size"] >= MIN_N].sort_values("mean")
    one = per[per["n"] == 1]
    voted = sub[sub["rslt"] == "의결"][["case_id", "ts"]].drop_duplicates()
    one_voted = one.reset_index().merge(voted, left_on=["case_id", "first"], right_on=["case_id", "ts"])["case_id"].nunique()
    day = sub.groupby(["case_id", "ts"])["rslt"].agg(lambda s: set(s))
    only_present = float(day.map(lambda s: s == {"상정"}).mean())

    tab = pd.concat([
        pd.DataFrame({"구분": "소위 상정 횟수", "집단": dist.index, "법률안": dist.values,
                      "비율": (dist / dist.sum()).round(4).values}),
        pd.DataFrame({"구분": "제안자별 반복 심사", "집단": by_prop.index, "법률안": by_prop["size"].values,
                      "비율": by_prop["mean"].round(4).values}),
        pd.DataFrame({"구분": "같은 법 법률안 수별 반복 심사", "집단": F1_LABELS,
                      "법률안": [int((per["f1"] == g).sum()) for g in F1_LABELS],
                      "비율": [round(float(per.loc[per["f1"] == g, "repeat"].mean()), 4) for g in F1_LABELS]}),
        pd.DataFrame({"구분": "위원회별 반복 심사(30건 이상)", "집단": by_cmt.index, "법률안": by_cmt["size"].values,
                      "비율": by_cmt["mean"].round(4).values}),
    ], ignore_index=True)
    tab.to_csv(OUT / "tables" / "meetings_groups.csv", index=False, encoding="utf-8-sig")
    per.assign(bill_no=conf.drop_duplicates("case_id").set_index("case_id")["bill_no"].reindex(per.index)) \
        .to_csv(ROOT / "data" / "processed" / "meeting_counts.csv", encoding="utf-8-sig")

    res = {
        "conf_date": CONF_DATE, "query_date": str(QUERY_DATE.date()),
        "bills_requested": len(ids), "no_data": len(empty), "bills_with_data": len(have),
        "rows": n_rows, "rows_after_query_date": int(late.sum()),
        "no_sub_present": int(no_present.sum()), "bills_sub_present": len(per),
        "C": {"match": c_match, "share": round(c_share, 4), "pass": c_share >= 0.95},
        "R1": {"repeat": int(per["repeat"].sum()), "share": round(r1, 4), "support": r1 >= 0.20},
        "R2": {"n": len(rep), "median_gap": r2, "support": None if len(rep) < MIN_N else r2 >= 90},
        "R3": {"n_alone": len(alone), "alone_share": round(r3a, 4), "n_50plus": len(big), "50plus_share": round(r3b, 4),
               "support": (r3b > r3a) if r3_ok else None},
        "aux": {"distribution": {k: int(v) for k, v in dist.items()},
                "gap_quartiles": [float(x) for x in rep["gap"].quantile([0.25, 0.75])] if len(rep) else None,
                "one_present_voted_same_day": round(one_voted / len(one), 4) if len(one) else None,
                "sub_meeting_days_only_present": round(only_present, 4),
                "max_presentations": int(per["n"].max())},
    }
    draw(per, dist, r2, OUT / "figures" / "fig14_meetings.png")
    (OUT / "meetings_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.set_option("display.width", 200)
    print(tab.to_string(index=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


def draw(per: pd.DataFrame, dist: pd.Series, med: float | None, path: Path) -> None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.4))
    a1.bar(dist.index, dist.values, color=["#9b9b9b", "#5b8fc7", "#1f4e89", "#0b2a55"][:len(dist)])
    for x, v in zip(dist.index, dist.values):
        a1.text(x, v, f"{v:,}\n({v / dist.sum():.1%})", ha="center", va="bottom", fontsize=9)
    a1.set_title("소위에 상정된 횟수(서로 다른 회의 날짜)")
    a1.set_ylabel("법률안 수")
    a1.set_ylim(0, dist.max() * 1.18)
    gap = per.loc[per["repeat"], "gap"]
    a2.hist(gap, bins=range(0, int(gap.max()) + 15, 15), color="#5b8fc7")
    if med is not None:
        a2.axvline(med, color="#c0392b", ls="--", lw=1.5, label=f"중앙값 {med:.0f}일")
        a2.legend()
    a2.set_title(f"반복 심사 법률안의 첫 상정 → 두 번째 상정 날수 (n={len(gap):,})")
    a2.set_xlabel("날수(15일 간격)")
    a2.set_ylabel("법률안 수")
    fig.suptitle("소위는 같은 법률안을 몇 번 심사하나 (22대 원안, 소위에 상정된 법률안)", fontsize=13)
    fig.text(0.01, 0.005, SOURCE_CONF + " · " + SOURCE, fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
