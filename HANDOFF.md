# HANDOFF

## 현재 상태 (2026-10-15)

- 설정: `AGENTS.md`·`CLAUDE.md`·`.gitignore`·`.gitattributes`·`.env.example`·`.claude/settings.json`(attribution 끔)·`.githooks/`(`core.hooksPath` 설정됨)·`.python-version`(3.14), `git init`
- GitHub(10-15): `main`에 README·설정(PR #1), 수집·날짜 필드 정의(PR #2), 소위 수집(PR #3) 병합. 현재 브랜치 `event-log-v1`(커밋 전). 계속 바뀌는 문서·`requirements.txt`는 단계가 끝날 때 그 브랜치로 올림. 커밋·푸시는 사용자 요청 시에만, PR 제목·본문은 알려 주면 사용자가 붙여 넣음
- `.env`에 `ASSEMBLY_API_KEY` 있음. 10-15 동작 확인: 「국회의원 발의법률안」(`nzmimeepazxkubdpn`) AGE=22 19,917건, 단계 날짜 필드 있음(`_private/02_로드맵.md` 1절)
- 환경(10-15): `.venv` Python 3.14.5, `requirements.txt`는 `pip freeze` 고정(pm4py 2.7.23.10·pandas 3.0.6·duckdb 1.5.6). 3.12 대신 3.14: PC에 3.14만 설치돼 있고 pm4py·의존 패키지가 3.14 Windows 휠을 제공(설치·import 확인)
- 수집 완료(10-15): `src/collect.py` → `data/raw/nzmimeepazxkubdpn/AGE22_20261015/`(20페이지 + `manifest.json`). 행 19,917 = 고유 `BILL_ID` 19,917 = `list_total_count` 19,917. 제안일 2024-05-30～2026-10-14
  - 날짜 채움: 제안 19,917 / 회부 19,871 / 소관위 상정 16,535 / 소관위 처리 5,292 / 법사위 회부 683·상정 672·처리 666 / 본회의(`PROC_DT`) 5,249
  - `PROC_RESULT`: 미처리(null) 14,668, 대안반영폐기 4,308, 수정가결 533, 원안가결 193, 철회 171, 수정안반영폐기 39, 폐기 4, 부결 1
- 소위 수집(10-15, [1015_02](docs/의사결정/1015_02_소위원회_단계_보완.md)): `src/collect.py --service subcommittee` → `data/raw/TVBPMCONFINFO/AGE22_20261015/` 18,324행 = `list_total_count`. 의원발의 17,238건 연결(소관위 상정 있는 의안의 97.7%), 여러 줄 의안 471건, 화면 7건 일치
  - 한 번에 최대 1,000건(1,001건 요청 시 ERROR-336). pm4py 라이선스 AGPL v3(공개 개인 프로젝트라 문제없음)
- 작업 기간 10-22～10-28(그 전에는 다른 토이프로젝트 진행)
- 이벤트 로그 v1(10-15, [1015_03](docs/의사결정/1015_03_이벤트로그_정의.md)): `src/build_event_log.py` → `data/processed/event_log.csv`(15MB), `excluded_events.csv`, `build_report.json`
  - 케이스 19,917(완료 5,249 / 진행중 14,668), 이벤트 99,458. 활동 11단계 + 끝 활동 6종(가결·부결·대안반영폐기·수정안반영폐기·철회·폐기), "처리" 용어
  - 규칙 제외 34개(철회일 복사 26, 결과 없는 소관위 처리 4, 심사미료 3, 원 시스템 입력 오류 1), 소위 중복 합침 93개. 활동별 처리 전후 건수 일치(코드가 대조)
  - 같은 날은 `step_order`로 정렬(pm4py는 동점 시 행 순서를 따름). 소위가 먼저 심사한 경로는 소관위 상정을 소위 처리 뒤(6.5), 위원회가 바뀐 의안 301건은 "소관위 재회부"
  - 남은 역행 384건은 모두 재회부·직접 회부 경로. 민감도: 뺀 이벤트를 넣어도 주요 중앙값 동일

## 다음 작업

1. **`event-log-v1` 커밋 시점**(사용자 요청 대기): `src/build_event_log.py`, `docs/의사결정/1015_03_이벤트로그_정의.md`, `data/processed/build_report.json`, `.gitignore`, 작업 문서. CSV(`event_log.csv` 15MB, `excluded_events.csv`)는 올리지 않음(사용자 결정 10-15)
2. analysis 브랜치: 가설을 먼저 세우고 사용자가 고름 → pm4py로 경로 지도·단계별 대기·계류·대안반영폐기 경로. 완료 기준과 조회일 경과 기준을 나눠 보임. 남은 확인: 재회부 분리 뒤 "제안 → 회부 30일 초과" 164건
3. cross-check(pandas·DuckDB로 지표 재계산) → report(보고서·README·출처)

## 열린 결정

- 의원발의만 볼지, 정부제출안·위원회안까지 넣을지
- 이벤트 로그 CSV: 최종본을 올릴지, README에 만드는 방법(수집 → 생성 스크립트)만 안내할지 report 단계에서 결정
