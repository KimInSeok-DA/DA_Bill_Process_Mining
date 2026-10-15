"""열린국회정보 의안 API 전체 페이지를 받아 data/raw/에 저장한다.

- bills: 「국회의원 발의법률안」(nzmimeepazxkubdpn) — 의안 1건 1행, 단계별 날짜
- subcommittee: 「소위 심사정보(법률안)」(TVBPMCONFINFO) — 소위 회부·상정·처리일
- allbills: 「법률안 심사 및 처리(의안검색)」(TVBPMBILL11) — 의원·정부·위원장 제안 법률안 전체, 단계별 날짜
- altref: 같은 서비스에 BILL_ID_REF=대안 ID로 물어 대안이 흡수한 원안 목록을 받는다(대안 1건당 1번 호출)

- 인증키는 .env의 ASSEMBLY_API_KEY로만 읽고, 출력에는 남기지 않는다.
- 이미 저장된 페이지는 다시 부르지 않는다(같은 조회일 폴더 기준).
- 끝나면 저장된 행 수를 list_total_count와 대조한다(bills는 고유 BILL_ID 수도).

실행: .venv/Scripts/python src/collect.py [--service bills|subcommittee|allbills|altref] [--age 22] [--date YYYYMMDD]
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

# 이름: (요청 코드, 서비스 메뉴명, BILL_ID가 행마다 고유한지)
SERVICES = {
    "bills": ("nzmimeepazxkubdpn", "국회의원 발의법률안", True),
    "subcommittee": ("TVBPMCONFINFO", "소위 심사정보(법률안)", False),
    "allbills": ("TVBPMBILL11", "법률안 심사 및 처리(의안검색)", True),
}
PAGE_SIZE = 1000  # API 최대(1,001건 요청 시 ERROR-336)
SLEEP_SEC = 1.0
MAX_RETRY = 5

ROOT = Path(__file__).resolve().parents[1]


def fetch_page(service: str, key: str, age: int, page: int, extra: dict | None = None) -> dict:
    """한 페이지를 받는다. 실패하면 간격을 늘려 다시 시도한다. INFO-200(데이터 없음)은 빈 결과로 돌려준다."""
    url = f"https://open.assembly.go.kr/portal/openapi/{service}"
    params = {"KEY": key, "Type": "json", "pIndex": page, "pSize": PAGE_SIZE, "AGE": age, **(extra or {})}
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            if service in data:
                code = data[service][0]["head"][1]["RESULT"]["CODE"]
                if code == "INFO-000":
                    return data
            else:
                code = data.get("RESULT", {}).get("CODE")
                if code == "INFO-200":
                    return {"RESULT": {"CODE": code}}
            print(f"  page {page}: 응답 코드 {code} (시도 {attempt})")
        except (requests.RequestException, ValueError) as e:
            # 예외 메시지에 요청 URL(키 포함)이 들어갈 수 있어 종류만 출력한다
            print(f"  page {page}: {type(e).__name__} (시도 {attempt})")
        time.sleep(SLEEP_SEC * 2**attempt)
    sys.exit(f"page {page}: {MAX_RETRY}회 실패로 중단")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", choices=[*SERVICES, "altref"], default="bills")
    ap.add_argument("--age", type=int, default=22)
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="조회일 폴더(이어받기용)")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ASSEMBLY_API_KEY")
    if not key:
        sys.exit(".env에 ASSEMBLY_API_KEY가 없습니다")

    if args.service == "altref":
        collect_altref(key, args.age, args.date)
        return
    SERVICE, service_name, unique_bill = SERVICES[args.service]
    out_dir = ROOT / "data" / "raw" / SERVICE / f"AGE{args.age}_{args.date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1페이지로 전체 건수를 확인한다
    first = out_dir / "p001.json"
    if first.exists():
        data = json.loads(first.read_text(encoding="utf-8"))
    else:
        data = fetch_page(SERVICE, key, args.age, 1)
        first.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        time.sleep(SLEEP_SEC)
    total = data[SERVICE][0]["head"][0]["list_total_count"]
    n_pages = -(-total // PAGE_SIZE)
    print(f"AGE={args.age} list_total_count={total:,} → {n_pages}페이지 ({out_dir.relative_to(ROOT)})")

    for page in range(2, n_pages + 1):
        path = out_dir / f"p{page:03d}.json"
        if path.exists():
            continue
        data = fetch_page(SERVICE, key, args.age, page)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"  page {page}/{n_pages} 저장 ({len(data[SERVICE][1]['row'])}건)")
        time.sleep(SLEEP_SEC)

    # 대조: 저장 행 수, 고유 BILL_ID 수, 페이지별 list_total_count 변화
    rows, totals = [], set()
    for path in sorted(out_dir.glob("p*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        totals.add(d[SERVICE][0]["head"][0]["list_total_count"])
        rows.extend(d[SERVICE][1]["row"])
    unique_ids = {r["BILL_ID"] for r in rows}
    print(f"저장 행 {len(rows):,} / 고유 BILL_ID {len(unique_ids):,} / list_total_count {sorted(totals)}")

    manifest = {
        "service": SERVICE,
        "service_name": service_name,
        "age": args.age,
        "query_date": f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}",
        "list_total_count": sorted(totals),
        "pages": n_pages,
        "rows": len(rows),
        "unique_bill_id": len(unique_ids),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = len(totals) == 1 and len(rows) == total and (not unique_bill or len(unique_ids) == total)
    if ok:
        print("대조 통과: 행 수 = list_total_count" + (" = 고유 BILL_ID 수" if unique_bill else ""))
    else:
        sys.exit("대조 불일치: 수집 중 데이터가 바뀌었을 수 있음. 새 --date 폴더로 다시 받으세요")


def collect_altref(key: str, age: int, date_: str) -> None:
    """allbills에서 위원장 제안 의안(대안·위원회안)을 골라, 각각이 참조하는 원안 목록을 받는다."""
    service = "TVBPMBILL11"
    src = ROOT / "data" / "raw" / service / f"AGE{age}_{date_}"
    rows = []
    for path in sorted(src.glob("p*.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8"))[service][1]["row"])
    alts = [r["BILL_ID"] for r in rows if r["PROPOSER_KIND"] == "위원장"]
    out_dir = ROOT / "data" / "raw" / f"{service}_REF" / f"AGE{age}_{date_}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"위원장 제안 의안 {len(alts):,}건의 참조 원안 조회 ({out_dir.relative_to(ROOT)})")
    for i, bill_id in enumerate(alts, 1):
        path = out_dir / f"{bill_id}.json"
        if path.exists():
            continue
        data = fetch_page(service, key, age, 1, {"BILL_ID_REF": bill_id})
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        if i % 100 == 0:
            print(f"  {i}/{len(alts)}")
        time.sleep(SLEEP_SEC)
    # 대조: 파일 수 = 대안 수, 한 페이지(1,000건)를 넘는 응답이 없는지
    links, full = 0, 0
    for bill_id in alts:
        d = json.loads((out_dir / f"{bill_id}.json").read_text(encoding="utf-8"))
        if service in d:
            n = d[service][0]["head"][0]["list_total_count"]
            links += len(d[service][1]["row"])
            full += n > PAGE_SIZE
    files = len([f for f in out_dir.glob("*.json") if f.name != "manifest.json"])
    print(f"저장 파일 {files:,} / 위원장 제안 의안 {len(alts):,} / 연결 행 {links:,} / 1,000건 초과 응답 {full}")
    (out_dir / "manifest.json").write_text(json.dumps(
        {"service": service, "service_name": "법률안 심사 및 처리(의안검색) BILL_ID_REF", "age": age,
         "query_date": f"{date_[:4]}-{date_[4:6]}-{date_[6:]}", "alts": len(alts), "links": links},
        ensure_ascii=False, indent=1), encoding="utf-8")
    if files != len(alts) or full:
        sys.exit("대조 불일치")


if __name__ == "__main__":
    main()
