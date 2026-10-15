"""수집한 원본(data/raw/)으로 이벤트 로그를 만든다. 정의: docs/의사결정/1015_03(v1), 1015_06(v2 범위 확장)

- 케이스: 22대 법률안 1건(BILL_ID). 의원안(기존 API), 정부안·위원장 제안(대안·위원회안, 의안검색 API).
  case_group: 원안(의원·정부) / 위원회안(위원장 제안). 진행 중 의안도 넣고 case_status로 구분한다
- 활동(원안): 제안 → 소관위 회부·상정 → 소위 회부·상정·처리 → 소관위 처리 → 법사위 회부·상정·처리 → 끝(처리 결과)
- 활동(위원회안): 소관위 처리(대안·위원회안 의결) → 법사위 회부·상정·처리 → 위원회 제출(제안일) → 끝
- 원안 ↔ 대안 연결: 대안을 BILL_ID_REF로 물어 받은 원안 목록 → alt_links.csv, 원안의 alt_bill_no 속성
- 시각: 일 단위 날짜. 같은 날은 step_order(절차 순서)로 정렬해 저장한다
  (pm4py는 같은 시각이면 행 순서를 따르므로 저장 순서가 곧 활동 순서다)
- 규칙에 따라 뺀 이벤트는 excluded_events.csv에 사유와 함께 남긴다

출력: data/processed/event_log.csv, excluded_events.csv, alt_links.csv, build_report.json
실행: .venv/Scripts/python src/build_event_log.py [--date YYYYMMDD]
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BILLS, SUB, ALL = "nzmimeepazxkubdpn", "TVBPMCONFINFO", "TVBPMBILL11"

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
SUBMIT_ORDER = 10.5  # 위원회안의 위원회 제출(법사위 체계·자구 심사 반영 뒤, 본회의 앞)
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


def load_links(folder: str) -> pd.DataFrame:
    """대안 1건당 받은 원안 목록 파일들을 (대안, 원안) 행으로 펼친다."""
    d = ROOT / "data" / "raw" / f"{ALL}_REF" / folder
    rows = []
    for p in sorted(d.glob("PRC_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for r in (data[ALL][1]["row"] if ALL in data else []):
            rows.append({"alt_bill_id": p.stem, "orig_bill_id": r["BILL_ID"], "orig_bill_no": r["BILL_NO"],
                         "orig_result": r["PROC_RESULT_CD"]})
    return pd.DataFrame(rows, columns=["alt_bill_id", "orig_bill_id", "orig_bill_no", "orig_result"])


def link_report(links: pd.DataFrame, bills: pd.DataFrame) -> dict:
    absorbed = bills[bills["PROC_RESULT"].isin(["대안반영폐기", "수정안반영폐기"])]
    pend = bills[bills["PROC_RESULT"].isna() & bills["CMT_PROC_RESULT_CD"].isin(["대안반영폐기", "수정안반영폐기"])]
    alts = bills[bills["PROPOSER_KIND"] == "위원장"]
    per_alt = links.groupby("alt_bill_id").size()
    return {
        "link_rows": len(links), "alts_with_links": int(per_alt.size), "alts_total": len(alts),
        "orig_per_alt_median": float(per_alt.median()) if len(per_alt) else None,
        "orig_not_in_cases": int((~links["orig_bill_id"].isin(bills["BILL_ID"])).sum()),
        "orig_linked_to_multiple_alts": int((links.groupby("orig_bill_id").size() > 1).sum()),
        "absorbed_cases": len(absorbed),
        "absorbed_linked": int(absorbed["BILL_ID"].isin(links["orig_bill_id"]).sum()),
        "pending_cmt_absorbed": len(pend),
        "pending_cmt_absorbed_linked": int(pend["BILL_ID"].isin(links["orig_bill_id"]).sum()),
        "orig_result": {str(k): int(v) for k, v in links["orig_result"].fillna("미처리").value_counts().items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--age", type=int, default=22)
    ap.add_argument("--date", default="20261015", help="수집 조회일 폴더")
    args = ap.parse_args()
    folder = f"AGE{args.age}_{args.date}"

    member, m_bills = load_rows(BILLS, folder)
    allb, m_all = load_rows(ALL, folder)
    sub_raw, m_sub = load_rows(SUB, folder)
    query_date = m_bills["query_date"]

    # 의원안은 화면으로 검증한 기존 API, 정부안·위원장 제안은 의안검색 API(1015_06 결정 1)
    member["PROPOSER_KIND"] = "의원"
    other = allb[allb["PROPOSER_KIND"].isin(["정부", "위원장"])].rename(
        columns={"CURR_COMMITTEE": "COMMITTEE", "PROC_RESULT_CD": "PROC_RESULT"})
    bills = pd.concat([member, other[[c for c in member.columns if c in other.columns]]], ignore_index=True)
    kind = bills.set_index("BILL_ID")["PROPOSER_KIND"]
    n_member_allb = int((allb["PROPOSER_KIND"] == "의원").sum())

    # 소위: 완전 중복 행 제거, 원안(의원·정부)만. 위원장 제안의 소위 기록은 위원회안 경로에 맞지 않아 뺀다
    sub = sub_raw.drop_duplicates()
    n_sub_exact_dup = len(sub_raw) - len(sub)
    n_sub_committee_bill = int(sub["BILL_ID"].map(kind).eq("위원장").sum())
    sub = sub[sub["BILL_ID"].map(kind).isin(["의원", "정부"])]

    # 원안 ↔ 대안 연결(1015_06 결정 3)
    links = load_links(folder)
    links["alt_bill_no"] = links["alt_bill_id"].map(bills.set_index("BILL_ID")["BILL_NO"])
    links["orig_kind"] = links["orig_bill_id"].map(kind)
    alt_of = links.groupby("orig_bill_id")["alt_bill_no"].agg(lambda s: ";".join(sorted(set(s.dropna()))))

    # 케이스 속성
    case = bills[["BILL_ID", "BILL_NO", "COMMITTEE", "PROC_RESULT", "PROPOSER_KIND"]].rename(
        columns={"BILL_ID": "case_id", "BILL_NO": "bill_no", "COMMITTEE": "case_committee", "PROC_RESULT": "final_result",
                 "PROPOSER_KIND": "proposer_kind"}
    )
    case["case_status"] = case["final_result"].notna().map({True: "완료", False: "진행중"})
    case["case_group"] = case["proposer_kind"].map({"의원": "원안", "정부": "원안", "위원장": "위원회안"})
    case["alt_bill_no"] = case["case_id"].map(alt_of)

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
        src["activity"], src["step_order"] = act, float(order)
        if col == "PROPOSE_DT":  # 위원회안의 제안일은 위원회 제출일이다(1015_06 결정 4)
            com = src["case_id"].map(kind).eq("위원장")
            src.loc[com, "activity"], src.loc[com, "step_order"] = "위원회 제출", SUBMIT_ORDER
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
    is_com = ev["case_group"] == "위원회안"
    rules = [
        ("위원회안의 소관위 상정(의결 회의와 같은 날, 889/891)", is_com & (ev["activity"] == "소관위 상정")),
        ("철회일과 같은 소관위 처리일(철회일이 복사된 칸)", is_cmt & (ev["final_result"] == "철회") & (ev["date"] == fin)),
        ("결과 코드가 없는 원안의 소관위 처리일", is_cmt & ~is_com & ev["stage_result"].isna()),
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
            "stage_result", "case_status", "final_result", "case_committee", "proposer_kind", "case_group",
            "alt_bill_no"]
    log = log[cols]

    # 5) 대조
    excl = excluded.groupby("activity").size()
    final = log.groupby("activity").size()
    order = {a: o for a, _, o in STEPS} | {"소관위 재회부": 2, "위원회 제출": SUBMIT_ORDER}
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
        "source_rows": {"의원(발의법률안 API)": len(member), "의원(의안검색 API, 대조용)": n_member_allb,
                        "정부": int((bills["PROPOSER_KIND"] == "정부").sum()),
                        "위원장": int((bills["PROPOSER_KIND"] == "위원장").sum()), "의안검색 API 전체": len(allb)},
        "cases_by_kind": {"/".join(k): int(v) for k, v in log.drop_duplicates("case_id")
                          .groupby(["case_group", "proposer_kind", "case_status"]).size().items()},
        "sub_committee_bill_rows_excluded": n_sub_committee_bill,
        "links": link_report(links, bills),
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
    links.to_csv(out / "alt_links.csv", index=False, encoding="utf-8-sig")
    excluded[cols + ["exclude_reason"]].to_csv(out / "excluded_events.csv", index=False, encoding="utf-8-sig")
    (out / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(pd.DataFrame(table).to_string(index=False))
    print(f"케이스 {report['cases']:,} (원본 {len(bills):,}) / 이벤트 {len(log):,} / 상태 {report['case_status']}")
    print(f"규칙 제외 {report['excluded_by_reason']}")
    print(f"직접 회부 의안 {n_direct:,} / 소위 뒤 소관위 상정 {n_after_sub:,} / 소관위 재회부 {n_rere:,}")
    print(f"종류별 케이스 {report['cases_by_kind']}")
    print(f"연결 {report['links']}")
    print(f"절차 역행 의안 {inv_cases:,}")
    print(inv.head(12).to_string())
    ok = report["cases"] == len(bills) == len(allb) and all(r["일치"] for r in table)
    sys.exit(0 if ok else "대조 불일치")


if __name__ == "__main__":
    main()
