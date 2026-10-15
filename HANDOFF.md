# HANDOFF

## 현재 상태 (2026-10-15)

- 설정: `AGENTS.md`·`CLAUDE.md`·`.gitignore`·`.gitattributes`·`.env.example`·`.claude/settings.json`(attribution 끔)·`.githooks/`(`core.hooksPath` 설정됨)·`.python-version`(3.14), `git init`
- GitHub(10-15): `main`에 README, 설정 파일(PR #1 병합). 현재 브랜치 `field-check`(커밋 전). 계속 바뀌는 문서·`requirements.txt`는 단계가 끝날 때 그 브랜치로 올림. 커밋·푸시는 사용자 요청 시에만, PR 제목·본문은 알려 주면 사용자가 붙여 넣음
- `.env`에 `ASSEMBLY_API_KEY` 있음. 10-15 동작 확인: 「국회의원 발의법률안」(`nzmimeepazxkubdpn`) AGE=22 19,917건, 단계 날짜 필드 있음(`_private/02_로드맵.md` 1절)
- 환경(10-15): `.venv` Python 3.14.5, `requirements.txt`는 `pip freeze` 고정(pm4py 2.7.23.10·pandas 3.0.6·duckdb 1.5.6). 3.12 대신 3.14: PC에 3.14만 설치돼 있고 pm4py·의존 패키지가 3.14 Windows 휠을 제공(설치·import 확인)
- 수집 완료(10-15): `src/collect.py` → `data/raw/nzmimeepazxkubdpn/AGE22_20261015/`(20페이지 + `manifest.json`). 행 19,917 = 고유 `BILL_ID` 19,917 = `list_total_count` 19,917. 제안일 2024-05-30～2026-10-14
  - 날짜 채움: 제안 19,917 / 회부 19,871 / 소관위 상정 16,535 / 소관위 처리 5,292 / 법사위 회부 683·상정 672·처리 666 / 본회의(`PROC_DT`) 5,249
  - `PROC_RESULT`: 미처리(null) 14,668, 대안반영폐기 4,308, 수정가결 533, 원안가결 193, 철회 171, 수정안반영폐기 39, 폐기 4, 부결 1
  - 한 번에 최대 1,000건(1,001건 요청 시 ERROR-336). pm4py 라이선스 AGPL v3(공개 개인 프로젝트라 문제없음)
- 작업 기간 10-22～10-28(그 전에는 다른 토이프로젝트 진행)

## 다음 작업

1. **`field-check` 커밋 시점**(사용자 요청 대기): `src/collect.py`, `docs/의사결정/1015_01_날짜필드_정의.md`, 작업 문서(`HANDOFF.md`·`docs/작업일지/`·`docs/AI_활용_기록.md`)
2. 이벤트 로그 v1(`event-log-v1` 브랜치, 고난도) — 날짜 뜻은 1015_01에서 확정(7건 화면 일치). 정할 것은 1015_01 4절: 철회의 `CMT_PROC_DT`(철회일), 대안반영폐기의 끝(위원회 의결 vs 대안 본회의 통과), 신속처리 `심사미료` 날짜, 같은 날 순서

## 열린 결정

- `ALLBILL`(의안정보 통합) 필수 인자, 정부이송·공포일 포함 여부
- 의원발의만 볼지, 정부제출안·위원회안까지 넣을지
- 소위원회 회부·상정·처리일 추가 수집 여부(화면 대조 결과 소관위 상정→처리 대기의 대부분이 소위 단계, 1015_01 1절)
