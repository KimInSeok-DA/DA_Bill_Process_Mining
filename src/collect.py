"""열린국회정보 「국회의원 발의법률안」(nzmimeepazxkubdpn) 전체 페이지를 받아 data/raw/에 저장한다.

- 인증키는 .env의 ASSEMBLY_API_KEY로만 읽고, 출력에는 남기지 않는다.
- 이미 저장된 페이지는 다시 부르지 않는다(같은 조회일 폴더 기준).
- 끝나면 저장된 행 수·고유 BILL_ID 수를 list_total_count와 대조한다.

실행: .venv/Scripts/python src/collect.py [--age 22] [--date YYYYMMDD]
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

SERVICE = "nzmimeepazxkubdpn"
URL = f"https://open.assembly.go.kr/portal/openapi/{SERVICE}"
PAGE_SIZE = 1000  # API 최대(1,001건 요청 시 ERROR-336)
SLEEP_SEC = 1.0
MAX_RETRY = 5

ROOT = Path(__file__).resolve().parents[1]


def fetch_page(key: str, age: int, page: int) -> dict:
    """한 페이지를 받는다. 실패하면 간격을 늘려 다시 시도한다."""
    params = {"KEY": key, "Type": "json", "pIndex": page, "pSize": PAGE_SIZE, "AGE": age}
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.get(URL, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            if SERVICE in data:
                code = data[SERVICE][0]["head"][1]["RESULT"]["CODE"]
                if code == "INFO-000":
                    return data
            else:
                code = data.get("RESULT", {}).get("CODE")
            print(f"  page {page}: 응답 코드 {code} (시도 {attempt})")
        except (requests.RequestException, ValueError) as e:
            # 예외 메시지에 요청 URL(키 포함)이 들어갈 수 있어 종류만 출력한다
            print(f"  page {page}: {type(e).__name__} (시도 {attempt})")
        time.sleep(SLEEP_SEC * 2**attempt)
    sys.exit(f"page {page}: {MAX_RETRY}회 실패로 중단")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--age", type=int, default=22)
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="조회일 폴더(이어받기용)")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ASSEMBLY_API_KEY")
    if not key:
        sys.exit(".env에 ASSEMBLY_API_KEY가 없습니다")

    out_dir = ROOT / "data" / "raw" / SERVICE / f"AGE{args.age}_{args.date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1페이지로 전체 건수를 확인한다
    first = out_dir / "p001.json"
    if first.exists():
        data = json.loads(first.read_text(encoding="utf-8"))
    else:
        data = fetch_page(key, args.age, 1)
        first.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        time.sleep(SLEEP_SEC)
    total = data[SERVICE][0]["head"][0]["list_total_count"]
    n_pages = -(-total // PAGE_SIZE)
    print(f"AGE={args.age} list_total_count={total:,} → {n_pages}페이지 ({out_dir.relative_to(ROOT)})")

    for page in range(2, n_pages + 1):
        path = out_dir / f"p{page:03d}.json"
        if path.exists():
            continue
        data = fetch_page(key, args.age, page)
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
        "service_name": "국회의원 발의법률안",
        "age": args.age,
        "query_date": f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}",
        "list_total_count": sorted(totals),
        "pages": n_pages,
        "rows": len(rows),
        "unique_bill_id": len(unique_ids),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    if len(totals) == 1 and len(rows) == len(unique_ids) == total:
        print("대조 통과: 행 수 = 고유 BILL_ID 수 = list_total_count")
    else:
        sys.exit("대조 불일치: 수집 중 데이터가 바뀌었을 수 있음. 새 --date 폴더로 다시 받으세요")


if __name__ == "__main__":
    main()
