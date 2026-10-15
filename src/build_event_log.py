"""수집한 원본(data/raw/)으로 이벤트 로그를 만든다. 정의: docs/의사결정/1015_03_이벤트로그_정의.md

- 케이스: 의원발의 법률안 1건(BILL_ID). 진행 중 의안도 넣고 case_status로 구분한다
- 활동: 제안 → 소관위 회부·상정 → 소위 회부·상정·처리 → 소관위 처리 → 법사위 회부·상정·처리 → 끝(처리 결과)
- 시각: 일 단위 날짜. 같은 날은 step_order(절차 순서)로 정렬해 저장한다
  (pm4py는 같은 시각이면 행 순서를 따르므로 저장 순서가 곧 활동 순서다)
- 규칙에 따라 뺀 이벤트는 excluded_events.csv에 사유와 함께 남긴다

출력: data/processed/event_log.csv, excluded_events.csv, build_report.json
실행: .venv/Scripts/python src/build_event_log.py [--date YYYYMMDD]
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BILLS, SUB = "nzmimeepazxkubdpn", "TVBPMCONFINFO"

# (활동, 원본 필드, 절차 순서). 소위 3개는 소위 심사정보의 필드
STEPS = [
    ("제안", "PROPOSE_DT", 1),
    ("소관위 회부", "COMMITTEE_DT", 2),
    ("소관위 상정", "CMT_PRESENT_DT", 3),
    ("소위 회부", "SUBMIT_DT", 4),
    ("소위 상정", "PRESENT_DT", 5),
    ("소위 처리", "PROC_DT", 6),
    ("소관위 처리", "CMT_PROC_DT", 7),
    ("법사위 회부", "LAW_SUBMIT_DT", 8),
    ("법사위 상정", "LAW_PRESENT_DT", 9),
    ("법사위 처리", "LAW_PROC_DT", 10),
]
END_ORDER = 11
DIRECT_PRESENT_ORDER = 6.5  # 소위가 먼저 심사한 경로의 소관위 상정(소위 처리 뒤)
# 끝 활동 이름: 원안·수정가결은 같은 절차라 "가결"로 합치고 원래 결과는 final_result에 둔다
END_NAME = {"원안가결": "가결", "수정가결": "가결"}

# 화면 대조로 확인한 원 시스템 입력 오류: (의안번호, 활동, 날짜) → 사유. 고치지 않고 뺀다
KNOWN_ERRORS = {
    ("2207462", "소위 처리", "2026-03-06"): "원 시스템 연도 입력 오류(회의정보상 소위 의결 2025-03-06), 2026-10-15 화면 대조",
}


def load_rows(service: str, folder: str) -> tuple[pd.DataFrame, dict]:
    d = ROOT / "data" / "raw" / service / folder
    rows = []
    for p in sorted(d.glob("p*.json")):
        rows += json.loads(p.read_text(encoding="utf-8"))[service][1]["row"]
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    return pd.DataFrame(rows), manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--age", type=int, default=22)
    ap.add_argument("--date", default="20261015", help="수집 조회일 폴더")
    args = ap.parse_args()
    folder = f"AGE{args.age}_{args.date}"

    bills, m_bills = load_rows(BILLS, folder)
    sub_raw, m_sub = load_rows(SUB, folder)
    query_date = m_bills["query_date"]

    # 소위: 완전 중복 행 제거, 의원발의 의안만
    sub = sub_raw.drop_duplicates()
    n_sub_exact_dup = len(sub_raw) - len(sub)
    sub = sub[sub["BILL_ID"].isin(bills["BILL_ID"])]

    # 케이스 속성
    case = bills[["BILL_ID", "BILL_NO", "COMMITTEE", "PROC_RESULT"]].rename(
        columns={"BILL_ID": "case_id", "BILL_NO": "bill_no", "COMMITTEE": "case_committee", "PROC_RESULT": "final_result"}
    )
    case["case_status"] = case["final_result"].notna().map({True: "완료", False: "진행중"})

    # 1) 원본 날짜를 모두 이벤트 후보로 펼친다
    ev = []
    for act, col, order in STEPS:
        if order in (4, 5, 6):
            src = sub[["BILL_ID", col, "COMMITTEE_NAME", "SUB_COMMITTEE_NAME", "PROC_RESULT_CD"]].copy()
            src.columns = ["case_id", "date", "committee", "sub_committee", "stage_result"]
            if order != 6:
                src["stage_result"] = None
        else:
            res = {"CMT_PROC_DT": "CMT_PROC_RESULT_CD", "LAW_PROC_DT": "LAW_PROC_RESULT_CD"}.get(col)
            src = bills[["BILL_ID", col, "COMMITTEE"]].copy()
            src.columns = ["case_id", "date", "committee"]
            src["sub_committee"] = None
            src["stage_result"] = bills[res] if res else None
        src["activity"], src["step_order"] = act, order
        ev.append(src[src["date"].notna()])
    end = bills.loc[bills["PROC_DT"].notna(), ["BILL_ID", "PROC_DT", "PROC_RESULT", "COMMITTEE"]].copy()
    end.columns = ["case_id", "date", "stage_result", "committee"]
    end["activity"] = end["stage_result"].map(lambda r: END_NAME.get(r, r))
    end["sub_committee"], end["step_order"] = None, END_ORDER
    ev.append(end)
    ev = pd.concat(ev, ignore_index=True)
    ev["date"] = pd.to_datetime(ev["date"]).dt.strftime("%Y-%m-%d")
    ev = ev.merge(case, on="case_id", how="left")

    # 경로에 따른 보정(1015_03 4절)
    # 소위가 먼저 심사한 경로(소위 직접 회부, 국회법 제58조제4항 등): 소관위 상정은 소위 처리 뒤 전체회의 상정이다.
    # 소관위 상정일이 첫 소위 처리일과 같거나 늦으면 같은 날에도 소위 처리 뒤에 둔다(날짜가 다르면 날짜가 순서를 정한다)
    direct = set(sub.loc[sub["ENROLL_TYPE"] == "Y", "BILL_ID"])
    first_sub_proc = ev[ev["activity"] == "소위 처리"].groupby("case_id")["date"].min()
    after_sub = ev["date"] >= ev["case_id"].map(first_sub_proc)
    ev["step_order"] = ev["step_order"].astype(float)
    ev.loc[(ev["activity"] == "소관위 상정") & after_sub, "step_order"] = DIRECT_PRESENT_ORDER
    n_after_sub = int(((ev["activity"] == "소관위 상정") & after_sub).sum())
    # 위원회가 바뀐 의안: COMMITTEE_DT는 새 위원회 회부일이다(첫 소위 회부보다 늦음). 첫 회부일은 데이터에 없다
    first_sub = ev[ev["activity"] == "소위 회부"].groupby("case_id")["date"].min()
    rere = (ev["activity"] == "소관위 회부") & (ev["date"] > ev["case_id"].map(first_sub))
    ev.loc[rere, "activity"] = "소관위 재회부"
    n_direct, n_rere = int(ev["case_id"].isin(direct).groupby(ev["case_id"]).first().sum()), int(rere.sum())
    candidates = ev.groupby("activity").size()  # 소관위 회부 + 소관위 재회부 = 원본 COMMITTEE_DT 건수

    # 2) 규칙에 맞는 이벤트를 뺀다(사유 기록)
    fin = ev["case_id"].map(bills.set_index("BILL_ID")["PROC_DT"])
    is_cmt = ev["activity"] == "소관위 처리"
    rules = [
        ("철회일과 같은 소관위 처리일(철회일이 복사된 칸)", is_cmt & (ev["final_result"] == "철회") & (ev["date"] == fin)),
        ("결과 코드가 없는 소관위 처리일", is_cmt & ev["stage_result"].isna()),
        ("신속처리 심사미료(법사위 회부 간주 뒤 기록된 날짜)", is_cmt & (ev["stage_result"] == "심사미료")),
        ("화면 대조로 확인한 원 시스템 입력 오류", pd.Series(
            [(b, a, d) in KNOWN_ERRORS for b, a, d in zip(ev["bill_no"], ev["activity"], ev["date"])], index=ev.index)),
    ]
    ev["exclude_reason"] = None
    for reason, mask in rules:
        ev.loc[mask & ev["exclude_reason"].isna(), "exclude_reason"] = reason
    excluded = ev[ev["exclude_reason"].notna()].copy()
    log = ev[ev["exclude_reason"].isna()].drop(columns="exclude_reason")

    # 3) 같은 의안·활동·날짜는 하나로 합친다(소위 여러 줄)
    log = log.sort_values(["case_id", "date", "step_order", "committee"], kind="stable")
    dup = log.duplicated(["case_id", "activity", "date"])
    dedup = log[dup].groupby("activity").size()
    log = log[~dup]

    # 4) 정렬: 의안 → 날짜 → 절차 순서. 이 순서가 활동 순서다
    log = log.sort_values(["case_id", "date", "step_order"], kind="stable").reset_index(drop=True)
    cols = ["case_id", "bill_no", "activity", "date", "step_order", "committee", "sub_committee",
            "stage_result", "case_status", "final_result", "case_committee"]
    log = log[cols]

    # 5) 대조
    excl = excluded.groupby("activity").size()
    final = log.groupby("activity").size()
    order = {a: o for a, _, o in STEPS} | {"소관위 재회부": 2}
    acts = sorted(candidates.index, key=lambda a: (order.get(a, END_ORDER), a))
    table = []
    for a in acts:
        c, x, d, f = (int(s.get(a, 0)) for s in (candidates, excl, dedup, final))
        table.append({"activity": a, "원본": c, "규칙 제외": x, "중복 합침": d, "로그": f, "일치": c - x - d == f})

    # 절차 역행: 같은 의안에서 바로 다음 이벤트의 절차 순서가 더 작은 경우(소위 재회부 등). 쌍별로 센다
    nxt = log.groupby("case_id").shift(-1)
    back = log[(nxt["step_order"] < log["step_order"])]
    inv = (back["activity"] + " → " + nxt.loc[back.index, "activity"]).value_counts()
    inv_cases = int(back["case_id"].nunique())

    report = {
        "query_date": query_date,
        "source": {"bills": m_bills["service_name"], "subcommittee": m_sub["service_name"]},
        "cases": int(log["case_id"].nunique()),
        "bills_rows": len(bills),
        "case_status": log.drop_duplicates("case_id")["case_status"].value_counts().to_dict(),
        "events": len(log),
        "sub_rows": len(sub_raw),
        "sub_exact_duplicates": n_sub_exact_dup,
        "excluded_by_reason": excluded["exclude_reason"].value_counts().to_dict(),
        "activities": table,
        "direct_referral_cases": n_direct,
        "present_after_sub_events": n_after_sub,
        "re_referral_events": n_rere,
        "backward_steps": {"cases": inv_cases, "pairs": inv.to_dict()},
    }

    out = ROOT / "data" / "processed"
    out.mkdir(parents=True, exist_ok=True)
    log.to_csv(out / "event_log.csv", index=False, encoding="utf-8-sig")
    excluded[cols + ["exclude_reason"]].to_csv(out / "excluded_events.csv", index=False, encoding="utf-8-sig")
    (out / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(pd.DataFrame(table).to_string(index=False))
    print(f"케이스 {report['cases']:,} (원본 {len(bills):,}) / 이벤트 {len(log):,} / 상태 {report['case_status']}")
    print(f"규칙 제외 {report['excluded_by_reason']}")
    print(f"직접 회부 의안 {n_direct:,} / 소위 뒤 소관위 상정 {n_after_sub:,} / 소관위 재회부 {n_rere:,}")
    print(f"절차 역행 의안 {inv_cases:,}")
    print(inv.head(12).to_string())
    ok = report["cases"] == len(bills) and all(r["일치"] for r in table)
    sys.exit(0 if ok else "대조 불일치")


if __name__ == "__main__":
    main()
