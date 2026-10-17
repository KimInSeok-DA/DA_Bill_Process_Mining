"""제안 → 소관위 회부 30일 초과 164건의 의안정보시스템 화면(심사정보)을 받아 처음 회부된 위원회를 확인한다.
정의: docs/의사결정/1017_13_회부_지연_확인.md 6절

- API(TVBPMBILL11·nzmimeepazxkubdpn)는 마지막 소관위 회부만 준다. 화면의 "소관위 심사정보" 표에는 회송 이력이 모두 나온다
- 받는 방법: 의안 페이지(billDetailPage.do)를 받은 뒤, 페이지의 숨은 입력값을 그대로 보내 심사정보 조각(billInfo.do)을 받는다
  (브라우저가 하는 것과 같다. billId만 보내면 심사정보가 빠진 응답이 온다). 의안당 2번, 요청 간격 2초
- 받은 조각은 data/raw/likms_billInfo/<날짜>/<의안번호>.html에 저장하고, 있으면 다시 받지 않는다
- 출력: outputs/tables/referral_screen.csv(분류 × 화면 결과), outputs/referral_screen.json

실행: .venv/Scripts/python src/referral_screen.py [--date 20261017]
"""

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from analysis import load
from referral_check import CATS, LIMIT, classify

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
BASE = "https://likms.assembly.go.kr/bill"
HEAD = {"User-Agent": "Mozilla/5.0"}
WAIT = 2


def get(url: str, data: dict | None = None) -> str:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    head = HEAD | ({"X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"} if data else {})
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=head), timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch(bill_id: str) -> str:
    page = get(f"{BASE}/bi/billDetailPage.do?billId={bill_id}&currMenuNo=2600044")
    params = {}
    for tag in re.findall(r'<input[^>]*type="hidden"[^>]*>', page):
        n, v = re.search(r'name="([^"]+)"', tag), re.search(r'value="([^"]*)"', tag)
        if n:
            params[n.group(1)] = html.unescape(v.group(1)) if v else ""
    if params.get("billId") != bill_id:
        raise RuntimeError(f"{bill_id}: 의안 페이지에 billId가 없음")
    time.sleep(WAIT)
    return get(f"{BASE}/bi/bill/detail/billInfo.do", params)


def text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse(frag: str) -> tuple[list[dict], str, str]:
    """소관위 심사정보 행(회부일 순), 표 아래 안내문, 비고"""
    m = re.search(r"<h4>소관위 심사정보</h4>(.*?)</table>(.*?)(?=<h4>|<h3>|$)", frag, re.S)
    if not m:
        return [], "", ""
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
        cell = {c: text(v) for c, v in re.findall(r'<td class="(\w+)[^"]*">\s*<i>[^<]*</i>(.*?)</td>', tr, re.S)}
        if "committeeName" in cell:
            rows.append({"cmt": cell["committeeName"], "ref": cell.get("submitDt", ""),
                         "proc": cell.get("procDt", ""), "result": cell.get("inscResultCd", "")})
    rows.sort(key=lambda r: r["ref"])
    b = re.search(r"<h4>비고</h4>(.*?)</div>\s*</div>", frag, re.S)
    return rows, text(m.group(2)), text(b.group(1)) if b else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261017")
    args = ap.parse_args()
    raw = ROOT / "data" / "raw" / "likms_billInfo" / args.date
    raw.mkdir(parents=True, exist_ok=True)

    x, _, _ = classify(load())
    recs = []
    for i, (bill_id, r) in enumerate(x.sort_values("bill_no").iterrows(), 1):
        f = raw / f"{r['bill_no']}.html"
        if not f.exists():
            f.write_text(fetch(bill_id), encoding="utf-8")
            print(f"{i}/{len(x)} {r['bill_no']} 받음", flush=True)
            time.sleep(WAIT)
        rows, note, memo = parse(f.read_text(encoding="utf-8"))
        first = rows[0] if rows else {}
        last = rows[-1] if rows else {}
        recs.append({
            "bill_no": r["bill_no"], "cat": r["cat"], "prop": r["prop"].date(), "api_ref": r["ref"].date(),
            "api_cmt": r["cmt"], "api_days": r["days"], "screen_rows": len(rows),
            "first_cmt": first.get("cmt"), "first_ref": first.get("ref"),
            "first_days": (pd.Timestamp(first["ref"]) - r["prop"]).days if first.get("ref") else None,
            "last_ref_match": last.get("ref") == str(r["ref"].date()),
            "returned": sum(x_["result"] == "회송" for x_ in rows),
            "history": " → ".join(f"{x_['cmt']}({x_['ref']}{', ' + x_['result'] + ' ' + x_['proc'] if x_['result'] else ''})"
                                  for x_ in rows),
            "note": note, "memo": memo,
        })
    (raw / "manifest.json").write_text(json.dumps(
        {"source": f"{BASE}/bi/bill/detail/billInfo.do", "files": len(recs), "fetched": args.date},
        ensure_ascii=False, indent=1), encoding="utf-8")

    d = pd.DataFrame(recs)
    d["screen"] = "화면에도 1줄"
    d.loc[d["screen_rows"] > 1, "screen"] = "앞선 회부 있음"
    d.loc[d["screen_rows"] == 0, "screen"] = "표 없음"
    tab = pd.crosstab(d["cat"], d["screen"], margins=True, margins_name="합계").reindex(CATS + ["합계"])
    tab.to_csv(OUT / "tables" / "referral_screen.csv", encoding="utf-8-sig")
    multi = d[d["screen_rows"] > 1]
    res = {
        "bills": len(d), "limit_days": LIMIT,
        "screen": d["screen"].value_counts().to_dict(),
        "last_ref_match_api": int(d["last_ref_match"].sum()),
        "first_days_median": float(multi["first_days"].median()) if len(multi) else None,
        "first_days_over_30": int((multi["first_days"] > LIMIT).sum()),
        "returned_any": int((d["returned"] > 0).sum()),
        "first_cmt": multi["first_cmt"].value_counts().to_dict(),
        "notes": d.loc[d["note"] != "", "note"].value_counts().to_dict(),
        "single_row": d.loc[d["screen_rows"] == 1, ["bill_no", "cat", "api_cmt", "prop", "api_ref", "api_days"]]
                        .astype(str).to_dict("records"),
    }
    (OUT / "referral_screen.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    d.to_csv(ROOT / "data" / "processed" / "referral_screen_bills.csv", index=False, encoding="utf-8-sig")
    print(tab.to_string())
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
