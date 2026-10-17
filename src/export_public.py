"""가공한 이벤트 로그를 공개용 압축본(data/public/)으로 내보낸다. 결정: docs/의사결정/1017_14_데이터_공개.md

- 22대: 이벤트 로그, 규칙으로 뺀 기록, 원안-대안 연결 / 21대: 이벤트 로그, 규칙으로 뺀 기록
- gzip 시각을 0으로 고정해 같은 입력이면 같은 파일이 나온다. 압축 전후 줄 수를 대조한다
- 원본(data/raw/)은 내보내지 않는다

실행: .venv/Scripts/python src/export_public.py
"""

import gzip
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "processed"
OUT = ROOT / "data" / "public"
FILES = {
    "age22": ["event_log.csv", "excluded_events.csv", "alt_links.csv", "build_report.json"],
    "age21": ["event_log.csv", "excluded_events.csv", "build_report.json"],
}


def main() -> None:
    for age, names in FILES.items():
        src = SRC if age == "age22" else SRC / age
        (OUT / age).mkdir(parents=True, exist_ok=True)
        for name in names:
            if name.endswith(".json"):
                shutil.copyfile(src / name, OUT / age / name)
                continue
            dst = OUT / age / f"{name}.gz"
            with open(src / name, "rb") as f, open(dst, "wb") as raw, \
                    gzip.GzipFile(filename=name, mode="wb", fileobj=raw, mtime=0, compresslevel=9) as g:
                shutil.copyfileobj(f, g)
            n_src = len(pd.read_csv(src / name, dtype=str))
            n_dst = len(pd.read_csv(dst, dtype=str))
            assert n_src == n_dst, (dst, n_src, n_dst)
            print(f"{age}/{name}.gz {n_dst:,}줄 {dst.stat().st_size / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
