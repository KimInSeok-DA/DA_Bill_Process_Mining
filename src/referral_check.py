"""제안 → 소관위 회부가 30일 넘게 걸린 원안을 분류한다. 정의: docs/의사결정/1017_13_회부_지연_확인.md

- 대상: 이벤트 로그 v2 원안 중 "제안 → 소관위 회부"(재회부 제외)가 30일 초과
- 분류(순서대로 첫 번째): 1 후반기 원구성(그 위원회의 2026-05-30 이후 첫 회부일에 회부, 제안은 그 전),
  2 특별위원회가 생기기 전에 제안(그 특별위원회의 첫 회부일보다 제안이 앞섬), 3 같은 날 한 위원회로 5건 이상 일괄 회부, 4 그 밖
- 출력: outputs/tables/referral_delay.csv(분류별 집계), outputs/referral_results.json

실행: .venv/Scripts/python src/referral_check.py
"""

import json
from pathlib import Path

import pandas as pd

from analysis import load

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
LIMIT = 30
HALF = pd.Timestamp("2026-05-30")  # 22대 전반기(2년)가 끝난 다음 날
BATCH = 5
CATS = ["1 후반기 원구성", "2 특별위원회 생기기 전 제안", "3 같은 날 한 위원회로 일괄 회부", "4 그 밖"]


def classify(log: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """30일 초과 원안(행: case_id, 열: bill_no·prop·ref·days·cmt·cat), 전체 날수, 위원회별 후반기 첫 회부일"""
    log = log[log["case_group"] == "원안"]
    prop = log[log["activity"] == "제안"].groupby("case_id")["ts"].min()
    ref = log[log["activity"] == "소관위 회부"].groupby("case_id")["ts"].min()
    days = (ref - prop.reindex(ref.index)).dt.days
    allref = log[log["activity"].isin(["소관위 회부", "소관위 재회부"])]
    first_ref = allref.groupby("committee_n")["ts"].min()
    half_first = allref[allref["ts"] >= HALF].groupby("committee_n")["ts"].min()

    x = pd.DataFrame({"prop": prop.reindex(days.index), "ref": ref, "days": days})[days > LIMIT]
    x["cmt"] = log.drop_duplicates("case_id").set_index("case_id")["committee_n"].reindex(x.index)
    a = (x["ref"] == x["cmt"].map(half_first)) & (x["prop"] < x["cmt"].map(half_first))
    b = x["cmt"].fillna("").str.contains("특별위원회") & (x["prop"] < x["cmt"].map(first_ref))
    c = x.groupby(["cmt", "ref"])["days"].transform("size") >= BATCH
    x["cat"] = CATS[3]
    x.loc[c, "cat"] = CATS[2]
    x.loc[b, "cat"] = CATS[1]
    x.loc[a, "cat"] = CATS[0]
    x.insert(0, "bill_no", log.drop_duplicates("case_id").set_index("case_id")["bill_no"].reindex(x.index))
    return x, days, half_first


def main() -> None:
    x, days, half_first = classify(load())
    ref = days
    tab = x.groupby("cat")["days"].agg(["size", "median", "max"]).reindex(CATS)
    tab.columns = ["법률안", "제안 → 회부 중앙값(일)", "최대(일)"]
    tab.to_csv(OUT / "tables" / "referral_delay.csv", encoding="utf-8-sig")
    batches = x[x["cat"] == CATS[2]].groupby(["cmt", "ref"]).size()
    res = {
        "referred": len(ref), "over_30": len(x),
        "categories": {k: int(v) for k, v in tab["법률안"].items()},
        "half_first_referral": {k: str(v.date()) for k, v in half_first.items()},
        "batches": [{"committee": cm, "date": str(d.date()), "bills": int(n)} for (cm, d), n in batches.items()],
        "interval_median_all": float(days.median()), "interval_median_without": float(days[days <= LIMIT].median()),
    }
    (OUT / "referral_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(tab.to_string())
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
