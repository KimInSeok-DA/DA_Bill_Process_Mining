"""핵심 지표를 DuckDB SQL로 처음부터 다시 계산해 pm4py·pandas 결과와 대조한다. 정의: docs/의사결정/1015_05_교차검증.md

- 입력은 이벤트 로그 CSV와 원안-대안 연결 CSV다. analysis.py의 함수는 쓰지 않는다(같은 코드를 다시 돌리는 것은 검증이 아니다)
- ①～⑤는 원안(의원·정부) 기준, ⑥은 의원·정부별 KM, ⑦은 대안반영폐기 원안의 대안 경로(1015_06 Q7·Q9),
  ⑧ 위원회별 KM(H5), ⑨ 월별 건수(H6), ⑩ 대안당 원안 수(Q8), ⑪ 적합성 검사(1016_08), ⑫ 21대 절단 실험(1016_09), ⑬ 지연 법률안 특징(1016_11),
  ⑭ 소위 반복 심사(1016_10, 회의정보 원본 JSON을 SQL로 직접 읽음), ⑮ OCEL(1016_12), ⑯ 회부 지연 확인(1017_13)
- 대조 대상: outputs/tables/*.csv, outputs/analysis_results.json
- 일치 기준: 건수는 완전 일치, 중앙값·분위수·KM 중앙값은 같은 정의에서 완전 일치(소수점 오차 1e-9)
- 출력: outputs/cross_check.csv. 하나라도 어긋나면 실패로 끝난다

실행: .venv/Scripts/python src/cross_check.py
"""

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
LOG = (ROOT / "data" / "processed" / "event_log.csv").as_posix()
LINKS = (ROOT / "data" / "processed" / "alt_links.csv").as_posix()
QUERY_DATE = json.loads((ROOT / "data" / "processed" / "build_report.json").read_text(encoding="utf-8"))["query_date"]

con = duckdb.connect()
con.execute(f"""
CREATE TABLE ev AS
SELECT *, row_number() OVER () AS rn
FROM read_csv('{LOG}', header = true, all_varchar = true, quote = '"', escape = '"')
""")
con.execute("""
CREATE TABLE eall AS
SELECT case_id, activity, CAST(date AS DATE) AS d, CAST(step_order AS DOUBLE) AS o, committee,
       stage_result, case_status, proposer_kind, case_group, final_result, rn,
       -- 구간 계산용 이름(1015_04 2절): 끝 활동 묶음, 재회부는 회부로, 소위 뒤 소관위 상정은 따로
       CASE WHEN activity IN ('가결', '부결', '폐기') THEN '본회의 처리'
            WHEN activity IN ('대안반영폐기', '수정안반영폐기') THEN '대안 본회의 통과'
            WHEN activity = '소관위 재회부' THEN '소관위 회부'
            WHEN activity = '소관위 상정' AND CAST(step_order AS DOUBLE) > 6 THEN '소관위 상정(소위 뒤)'
            ELSE activity END AS act2,
       CASE committee WHEN '기획재정위원회' THEN '재정경제기획위원회'
                      WHEN '환경노동위원회' THEN '기후에너지환경노동위원회'
                      WHEN '여성가족위원회' THEN '성평등가족위원회'
                      ELSE committee END AS cmt
FROM ev
""")
con.execute("CREATE TABLE e AS SELECT * FROM eall WHERE case_group = '원안'")
con.execute(f"CREATE TABLE lk AS SELECT * FROM read_csv('{LINKS}', header = true, all_varchar = true)")

PAIRS = [
    ("제안", "소관위 회부"), ("소관위 회부", "소관위 상정"), ("소관위 상정", "소위 회부"),
    ("소위 회부", "소위 상정"), ("소위 상정", "소위 처리"), ("소위 처리", "소관위 처리"),
    ("소관위 처리", "법사위 회부"), ("법사위 회부", "법사위 상정"), ("법사위 상정", "법사위 처리"),
    ("법사위 처리", "본회의 처리"), ("소관위 처리", "대안 본회의 통과"),
]

rows = []


def check(metric: str, item: str, before, sql, tol: float = 1e-9) -> None:
    both_na = pd.isna(before) and pd.isna(sql)
    ok = both_na or (not pd.isna(before) and not pd.isna(sql) and abs(float(before) - float(sql)) <= tol)
    rows.append({"지표": metric, "항목": item, "기존": before, "SQL": sql, "일치": bool(ok)})


def q(sql: str) -> pd.DataFrame:
    return con.execute(sql).fetchdf()


# ① 직접 이어짐(프로세스 지도): pm4py ↔ SQL LEAD
dfg_sql = q("""
WITH s AS (
  SELECT activity AS a, LEAD(activity) OVER w AS b, LEAD(d) OVER w - d AS gap
  FROM e WINDOW w AS (PARTITION BY case_id ORDER BY rn)
)
SELECT a, b, count(*) AS n, median(gap) AS med FROM s WHERE b IS NOT NULL GROUP BY a, b
""").set_index(["a", "b"])
dfg_pm = pd.read_csv(OUT / "tables" / "dfg_pm4py.csv").set_index(["A", "B"])
check("① 직접 이어짐", "연결 종류 수", len(dfg_pm), len(dfg_sql))
for k in dfg_pm.index.union(dfg_sql.index):
    name = f"{k[0]} → {k[1]}"
    check("① 직접 이어짐 빈도", name, dfg_pm["빈도"].get(k), dfg_sql["n"].get(k))
    check("① 직접 이어짐 대기 중앙값(일)", name, dfg_pm["대기 중앙값(일)"].get(k), dfg_sql["med"].get(k))
# 저장 순서(rn)가 (의안, 날짜, 절차 순서) 정렬과 같은지도 확인
check("① 직접 이어짐", "행 순서 = (의안, 날짜, 절차 순서) 정렬", 0, int(q("""
SELECT count(*) AS n FROM (
  SELECT rn, row_number() OVER (ORDER BY case_id, d, o, rn) AS r2, row_number() OVER (ORDER BY rn) AS r1 FROM e
) WHERE r1 <> r2""")["n"][0]))

# ② 구간 대기(관찰값): pandas ↔ SQL
con.execute("CREATE TABLE f AS SELECT case_id, act2, min(d) AS d FROM e GROUP BY case_id, act2")
con.execute(f"""
CREATE TABLE cens AS
SELECT c.case_id, coalesce(min(CASE WHEN e.o = 11 THEN e.d END), DATE '{QUERY_DATE}') AS cd
FROM (SELECT DISTINCT case_id FROM e) c JOIN e USING (case_id) GROUP BY c.case_id
""")
h1 = pd.read_csv(OUT / "tables" / "h1_waits.csv").set_index("구간")
for a, b in PAIRS:
    name = f"{a} → {b}"
    r = q(f"""
    SELECT count(*) AS n, median(gap) AS med, quantile_cont(gap, 0.9) AS p90
    FROM (SELECT fb.d - fa.d AS gap FROM f fa JOIN f fb USING (case_id)
          WHERE fa.act2 = '{a}' AND fb.act2 = '{b}') WHERE gap >= 0
    """).iloc[0]
    check("② 구간 관찰 건수", name, h1.loc[name, "관찰 건수"], r["n"])
    check("② 구간 관찰 중앙값(일)", name, h1.loc[name, "중앙값(일)"], r["med"])
    check("② 구간 90분위(일)", name, h1.loc[name, "90분위(일)"], r["p90"])

# ⑤ KM 중앙값: 직접 짠 Python 함수 ↔ SQL 누적곱(log 합)
    km = q(f"""
    WITH base AS (
      SELECT a.case_id, a.d AS da, b.d AS db, c.cd
      FROM f a LEFT JOIN f b ON b.case_id = a.case_id AND b.act2 = '{b}'
      JOIN cens c ON c.case_id = a.case_id
      WHERE a.act2 = '{a}' AND (b.d IS NULL OR b.d >= a.d)
    ), s AS (
      SELECT greatest(coalesce(db, cd) - da, 0) AS t, (db IS NOT NULL)::INT AS ev FROM base
    ), g AS (
      SELECT t, sum(ev) AS dth, count(*) AS cnt FROM s GROUP BY t
    ), r AS (
      SELECT t, dth, sum(cnt) OVER (ORDER BY t DESC) AS at_risk FROM g
    ), k AS (
      SELECT t, sum(CASE WHEN dth = at_risk THEN -1e9 ELSE ln(1 - dth / at_risk) END) OVER (ORDER BY t) AS ls FROM r
    )
    SELECT (SELECT count(*) FROM base) AS n_a, min(t) FILTER (WHERE exp(ls) <= 0.5) AS med FROM k
    """).iloc[0]
    check("② 구간 A 있는 의안", name, h1.loc[name, "A 있는 의안"], km["n_a"])
    check("⑤ KM 중앙값(일)", name, h1.loc[name, "KM 중앙값(일)"], km["med"])

# ③ 진행 중 의안의 대기 단계: pandas ↔ SQL
st = q(f"""
WITH last AS (
  SELECT *, row_number() OVER (PARTITION BY case_id ORDER BY rn DESC) AS k FROM e WHERE case_status = '진행중'
)
SELECT CASE
  WHEN activity = '제안' THEN '소관위 회부 대기'
  WHEN activity IN ('소관위 회부', '소관위 재회부') THEN '소관위 상정 대기'
  WHEN activity = '소관위 상정' AND o > 6 THEN '소관위 처리 대기'
  WHEN activity = '소관위 상정' THEN '소위 회부 대기'
  WHEN activity = '소위 회부' THEN '소위 상정 대기'
  WHEN activity = '소위 상정' THEN '소위 처리 대기'
  WHEN activity = '소위 처리' THEN '소관위 처리 대기'
  WHEN activity = '소관위 처리' AND stage_result IN ('대안반영폐기', '수정안반영폐기') THEN '대안 본회의 대기'
  WHEN activity = '소관위 처리' AND stage_result IN ('원안가결', '수정가결') THEN '법사위 회부 대기'
  WHEN activity = '법사위 회부' THEN '법사위 상정 대기'
  WHEN activity = '법사위 상정' THEN '법사위 처리 대기'
  WHEN activity = '법사위 처리' THEN '본회의 대기'
  ELSE '기타' END AS state,
  count(*) AS n, median(DATE '{QUERY_DATE}' - d) AS med, quantile_cont(DATE '{QUERY_DATE}' - d, 0.9) AS p90
FROM last WHERE k = 1 GROUP BY state
""").set_index("state")
h3 = pd.read_csv(OUT / "tables" / "h3_backlog_state.csv").set_index("state")
check("③ 대기 단계", "단계 종류 수", len(h3), len(st))
for s in h3.index.union(st.index):
    check("③ 대기 단계 의안 수", s, h3["건수"].get(s), st["n"].get(s))
    check("③ 대기 단계 경과 중앙값(일)", s, h3["경과중앙값"].get(s), st["med"].get(s))
    check("③ 대기 단계 경과 90분위(일)", s, h3["경과90분위"].get(s), st["p90"].get(s))

# ④ 묶음 처리: pandas ↔ SQL
res = json.loads((OUT / "analysis_results.json").read_text(encoding="utf-8"))
b = q("""
WITH c AS (SELECT case_id, stage_result, count(*) OVER (PARTITION BY cmt, d) AS batch, cmt, d
           FROM e WHERE activity = '소관위 처리')
SELECT count(*) AS n, avg((batch >= 2)::INT) AS share, median(batch) AS med,
       (SELECT count(*) FROM (SELECT DISTINCT cmt, d FROM c)) AS days,
       avg((batch >= 2)::INT) FILTER (WHERE stage_result IN ('대안반영폐기', '수정안반영폐기')) AS share_alt,
       avg((batch >= 2)::INT) FILTER (WHERE stage_result IN ('원안가결', '수정가결')) AS share_pass
FROM c
""").iloc[0]
h2 = res["H2"]
check("④ 묶음 처리", "소관위 처리 의안 수", h2["cmt_proc_events"], b["n"])
check("④ 묶음 처리", "2건 이상 묶음 비율(소수 셋째 자리)", h2["share_in_batch_ge2"], round(b["share"], 3))
check("④ 묶음 처리", "의안 기준 묶음 크기 중앙값", h2["median_batch_size_per_event"], b["med"])
check("④ 묶음 처리", "(위원회, 날짜) 수", h2["meeting_days"], b["days"])
check("④ 묶음 처리", "대안반영폐기 묶음 비율", h2["share_ge2_by_result"]["대안반영폐기"], round(b["share_alt"], 3))
check("④ 묶음 처리", "가결 묶음 비율", h2["share_ge2_by_result"]["가결"], round(b["share_pass"], 3))

# ⑥ 의원·정부별 소위 회부 → 상정 KM 중앙값(Q7)
for kind in ("의원", "정부"):
    km = q(f"""
    WITH ff AS (SELECT f.* FROM f JOIN (SELECT DISTINCT case_id FROM e WHERE proposer_kind = '{kind}') USING (case_id)),
    base AS (
      SELECT a.case_id, a.d AS da, b.d AS db, c.cd
      FROM ff a LEFT JOIN ff b ON b.case_id = a.case_id AND b.act2 = '소위 상정'
      JOIN cens c ON c.case_id = a.case_id
      WHERE a.act2 = '소위 회부' AND (b.d IS NULL OR b.d >= a.d)
    ), s AS (SELECT greatest(coalesce(db, cd) - da, 0) AS t, (db IS NOT NULL)::INT AS ev FROM base),
    g AS (SELECT t, sum(ev) AS dth, count(*) AS cnt FROM s GROUP BY t),
    r AS (SELECT t, dth, sum(cnt) OVER (ORDER BY t DESC) AS at_risk FROM g),
    k AS (SELECT t, sum(CASE WHEN dth = at_risk THEN -1e9 ELSE ln(1 - dth / at_risk) END) OVER (ORDER BY t) AS ls FROM r)
    SELECT (SELECT count(*) FROM base) AS n_a, min(t) FILTER (WHERE exp(ls) <= 0.5) AS med FROM k
    """).iloc[0]
    check("⑥ 의원·정부 KM", f"{kind} 소위 회부 의안", res["Q7"][kind]["소위 회부 의안"], km["n_a"])
    check("⑥ 의원·정부 KM", f"{kind} 소위 회부 → 상정 KM 중앙값", res["Q7"][kind]["소위 회부 → 상정 KM 중앙값"], km["med"])

# ⑦ 대안반영폐기 원안의 대안 경로(Q9): 연결 표 조인
con.execute("""
CREATE TABLE fa AS SELECT case_id, activity, min(d) AS d FROM eall GROUP BY case_id, activity
""")
con.execute("""
CREATE TABLE seg AS
SELECT
  (SELECT d FROM fa WHERE fa.case_id = lk.orig_bill_id AND activity = '소관위 처리') AS o_cmt,
  (SELECT d FROM fa WHERE fa.case_id = lk.alt_bill_id AND activity = '소관위 처리') AS a_cmt,
  (SELECT d FROM fa WHERE fa.case_id = lk.alt_bill_id AND activity = '법사위 회부') AS a_ls,
  (SELECT d FROM fa WHERE fa.case_id = lk.alt_bill_id AND activity = '법사위 처리') AS a_lp,
  (SELECT d FROM fa WHERE fa.case_id = lk.alt_bill_id AND activity = '위원회 제출') AS a_sub,
  (SELECT min(d) FROM eall WHERE eall.case_id = lk.alt_bill_id AND o = 11) AS a_end
FROM lk
WHERE orig_bill_id IN (SELECT DISTINCT case_id FROM e WHERE final_result IN ('대안반영폐기', '수정안반영폐기'))
""")
segs = [("원안 소관위 처리", "대안 소관위 처리", "o_cmt", "a_cmt"), ("대안 소관위 처리", "대안 법사위 회부", "a_cmt", "a_ls"),
        ("대안 법사위 회부", "대안 법사위 처리", "a_ls", "a_lp"), ("대안 법사위 처리", "대안 위원회 제출", "a_lp", "a_sub"),
        ("대안 위원회 제출", "대안 본회의", "a_sub", "a_end"), ("원안 소관위 처리", "대안 본회의(전체)", "o_cmt", "a_end")]
check("⑦ 대안 경로", "연결된 대안반영폐기 원안 쌍", res["Q9"]["linked_absorbed_pairs"], int(q("SELECT count(*) AS n FROM seg")["n"][0]))
for x, y, cx, cy in segs:
    name = f"{x} → {y}"
    r = q(f"SELECT count(*) AS n, median({cy} - {cx}) AS med, quantile_cont({cy} - {cx}, 0.9) AS p90 "
          f"FROM seg WHERE {cx} IS NOT NULL AND {cy} IS NOT NULL").iloc[0]
    check("⑦ 대안 경로", f"{name} 건수", res["Q9"]["segments"][name]["n"], r["n"])
    check("⑦ 대안 경로", f"{name} 중앙값", res["Q9"]["segments"][name]["median"], r["med"])
    check("⑦ 대안 경로", f"{name} 90분위", res["Q9"]["segments"][name]["p90"], r["p90"])

# ⑧ 위원회별 소위 회부 → 상정 KM 중앙값(H5): 위원회는 그 단계를 맡은 위원회(개편 전후 이름 묶음), 특별위원회 제외
h5 = pd.read_csv(OUT / "tables" / "h5_committee.csv", index_col=0)
km5 = q("""
WITH sub AS (
  SELECT cmt, case_id, min(d) FILTER (WHERE activity = '소위 회부') AS da, min(d) FILTER (WHERE activity = '소위 상정') AS db
  FROM e WHERE activity IN ('소위 회부', '소위 상정') AND cmt IS NOT NULL AND cmt NOT LIKE '%특별위원회%'
  GROUP BY cmt, case_id
), base AS (
  SELECT s.cmt, greatest(coalesce(s.db, c.cd) - s.da, 0) AS t, (s.db IS NOT NULL)::INT AS ev
  FROM sub s JOIN cens c USING (case_id) WHERE s.da IS NOT NULL AND (s.db IS NULL OR s.db >= s.da)
), g AS (SELECT cmt, t, sum(ev) AS dth, count(*) AS cnt FROM base GROUP BY cmt, t),
r AS (SELECT cmt, t, dth, sum(cnt) OVER (PARTITION BY cmt ORDER BY t DESC) AS at_risk FROM g),
k AS (SELECT cmt, t, sum(CASE WHEN dth = at_risk THEN -1e9 ELSE ln(1 - dth / at_risk) END)
             OVER (PARTITION BY cmt ORDER BY t) AS ls FROM r)
SELECT cmt, min(t) FILTER (WHERE exp(ls) <= 0.5) AS med FROM k GROUP BY cmt
""").set_index("cmt")["med"]
for c in h5.index.union(km5.index):
    check("⑧ 위원회별 KM 중앙값(H5)", c, h5["KM중앙값"].get(c), km5.get(c))

# ⑨ 월별 끝 활동·소관위 처리 건수(H6)
h6 = pd.read_csv(OUT / "tables" / "h6_monthly.csv", index_col=0)
m6 = q("""
SELECT strftime(d, '%Y-%m') AS m, count(*) FILTER (WHERE o = 11) AS ends,
       count(*) FILTER (WHERE activity = '소관위 처리') AS cmt
FROM e GROUP BY m
""").set_index("m")
for m in h6.index:
    check("⑨ 월별 건수(H6)", f"{m} 끝 활동", h6.loc[m, "끝 활동"], m6["ends"].get(m, 0))
    check("⑨ 월별 건수(H6)", f"{m} 소관위 처리", h6.loc[m, "소관위 처리"], m6["cmt"].get(m, 0))

# ⑩ 대안당 원안 수(Q8)
q8 = q("""
WITH a AS (SELECT DISTINCT case_id FROM eall WHERE case_group = '위원회안'),
n AS (SELECT a.case_id, count(lk.orig_bill_id) AS k FROM a LEFT JOIN lk ON lk.alt_bill_id = a.case_id GROUP BY a.case_id)
SELECT count(*) FILTER (WHERE k > 0) AS alts, median(k) FILTER (WHERE k > 0) AS med,
       quantile_cont(k, 0.9) FILTER (WHERE k > 0) AS p90, max(k) AS mx, sum(k) AS rows_ FROM n
""").iloc[0]
check("⑩ 대안당 원안 수(Q8)", "원안과 연결된 대안", res["Q8"]["alts_with_origins"], q8["alts"])
check("⑩ 대안당 원안 수(Q8)", "연결 행", res["Q8"]["link_rows"], q8["rows_"])
check("⑩ 대안당 원안 수(Q8)", "중앙값", res["Q8"]["origins_per_alt_median"], q8["med"])
check("⑩ 대안당 원안 수(Q8)", "90분위", res["Q8"]["origins_per_alt_p90"], q8["p90"])
check("⑩ 대안당 원안 수(Q8)", "최대", res["Q8"]["origins_per_alt_max"], q8["mx"])

# ⑪ 적합성 검사(1016_08): pm4py 정렬·상태 추적 ↔ 정규식(SQL)과 SQL 규칙
conf_path = OUT / "conformance_results.json"
if conf_path.exists():
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    # 표준 절차를 정규식으로 다시 씀(conformance.py의 상태표와 따로 작성)
    W = "(>철회)"
    law = f"법사위 회부(>법사위 상정(>법사위 처리(>(가결|부결|폐기)|{W})|{W})|{W})"
    after_cmt = f"소관위 처리(>{law}|>(대안반영폐기|수정안반영폐기|폐기)|{W})"
    sub = f"소위 회부(>소위 상정(>소위 상정)*(>소위 처리(>{after_cmt}|{W})|{W})|{W})"
    full = f"^제안(>소관위 회부(>소관위 상정(>{sub}|{W})|{W})|{W})$"
    law_p = "법사위 회부(>법사위 상정(>법사위 처리)?)?"
    prefix = (f"^제안(>소관위 회부(>소관위 상정(>소위 회부(>소위 상정(>소위 상정)*(>소위 처리(>소관위 처리(>{law_p})?)?)?)?)?)?)?$")
    con.execute("""
    CREATE TABLE tr AS
    SELECT case_id, any_value(case_status) AS st, any_value(case_committee) AS cc,
           string_agg(activity, '>' ORDER BY rn) AS path,
           min(rn) FILTER (WHERE activity = '소위 회부') AS r_sref, min(rn) FILTER (WHERE activity = '소관위 상정') AS r_cpre,
           min(rn) FILTER (WHERE activity = '법사위 회부') AS r_lref, min(rn) FILTER (WHERE activity = '소관위 처리') AS r_cproc,
           bool_or(activity = '소관위 재회부') AS rere,
           count(*) FILTER (WHERE activity = '소위 회부') AS n_sref,
           count(DISTINCT committee) FILTER (WHERE activity = '소위 회부') AS n_scmt,
           bool_or(activity = '소관위 상정' AND o > 6) AS pre_after,
           bool_or(activity = '가결') AS passed, bool_or(activity IN ('가결', '부결')) AS voted,
           bool_or(activity IN ('소위 회부', '소위 상정', '소위 처리')) AS has_sub
    FROM (SELECT eall.*, ev.case_committee FROM eall JOIN ev USING (rn)) WHERE case_group = '원안'
    GROUP BY case_id
    """)
    tr = q(f"""
    SELECT *, CASE
      WHEN rere OR (n_sref >= 2 AND n_scmt >= 2) THEN '재회부'
      WHEN (r_sref IS NOT NULL AND (r_cpre IS NULL OR r_sref < r_cpre)) OR pre_after THEN '소위 직접 회부'
      WHEN r_lref IS NOT NULL AND (r_cproc IS NULL OR r_lref < r_cproc) THEN '신속처리'
      WHEN passed AND r_cproc IS NULL THEN '예산부수 자동부의'
      WHEN voted AND r_lref IS NULL AND cc = '법제사법위원회' THEN '법사위 소관'
      WHEN r_cproc IS NOT NULL AND NOT has_sub THEN '소위 생략'
      ELSE '그 밖' END AS typ,
      regexp_full_match(path, '{full}') AS fit, regexp_full_match(path, '{prefix}') AS pfit
    FROM tr
    """)
    done, prog = tr[tr["st"] == "완료"], tr[tr["st"] == "진행중"]
    check("⑪ 적합성 검사", "끝난 원안 수", conf["completed"]["cases"], len(done))
    check("⑪ 적합성 검사", "끝난 원안 완전 적합", conf["completed"]["fully_fit"], int(done["fit"].sum()))
    check("⑪ 적합성 검사", "처리 중 원안 수", conf["in_progress"]["cases"], len(prog))
    check("⑪ 적합성 검사", "처리 중 지금까지 표준 절차", conf["in_progress"]["compliant_so_far"], int(prog["pfit"].sum()))
    for label, part, key, ok_col in (("끝난", done, "completed", "fit"), ("처리 중", prog, "in_progress", "pfit")):
        typ = part.loc[~part[ok_col], "typ"].value_counts()
        for t in sorted(set(typ.index) | {k for k in conf[key]["types"] if "표준" not in k}):
            check("⑪ 적합성 검사", f"{label} 원안 {t}", conf[key]["types"].get(t, 0), int(typ.get(t, 0)))

def km_sql(base: str, cut: int | None = None) -> str:
    """base(t, ev)의 KM 중앙값(med). cut을 주면 cut일까지 다음 단계에 간 비율(reach)도."""
    reach = f", 1 - coalesce(exp(arg_max(ls, t) FILTER (WHERE t <= {cut})), 1) AS reach" if cut is not None else ""
    return f"""
    WITH s AS ({base}), g AS (SELECT t, sum(ev) AS dth, count(*) AS cnt FROM s GROUP BY t),
    r AS (SELECT t, dth, sum(cnt) OVER (ORDER BY t DESC) AS at_risk FROM g),
    k AS (SELECT t, sum(CASE WHEN dth = at_risk THEN -1e9 ELSE ln(1 - dth / at_risk) END) OVER (ORDER BY t) AS ls FROM r)
    SELECT min(t) FILTER (WHERE exp(ls) <= 0.5) AS med{reach} FROM k"""


# ⑫ 21대 절단 실험(1016_09): age21.py(analysis.py 함수) ↔ SQL. T 전 기록만 쓴 표와 끝까지 본 표를 따로 만든다
age_path = OUT / "age21" / "age21_results.json"
if age_path.exists():
    age = json.loads(age_path.read_text(encoding="utf-8"))
    log21 = (ROOT / "data" / "processed" / "age21" / "event_log.csv").as_posix()
    cut = age["cut_date"]
    con.execute(f"""
    CREATE TABLE e21 AS
    SELECT case_id, CAST(date AS DATE) AS d, CAST(step_order AS DOUBLE) AS o,
           CASE WHEN activity IN ('가결', '부결', '폐기') THEN '본회의 처리'
                WHEN activity IN ('대안반영폐기', '수정안반영폐기') THEN '대안 본회의 통과'
                WHEN activity = '소관위 재회부' THEN '소관위 회부'
                WHEN activity = '소관위 상정' AND CAST(step_order AS DOUBLE) > 6 THEN '소관위 상정(소위 뒤)'
                ELSE activity END AS act2
    FROM read_csv('{log21}', header = true, all_varchar = true, quote = '"', escape = '"')
    WHERE case_group = '원안'
    """)
    for suffix, where, cens in (("c", f"WHERE d < DATE '{cut}'", f"DATE '{cut}'"), ("f", "", "NULL")):
        con.execute(f"CREATE TABLE f21{suffix} AS SELECT case_id, act2, min(d) AS d FROM e21 {where} GROUP BY ALL")
        con.execute(f"""CREATE TABLE c21{suffix} AS SELECT case_id, coalesce(min(d) FILTER (WHERE o = 11), {cens}) AS cd
                        FROM e21 {where} GROUP BY case_id""")

    tab = {r["구간"]: r for r in age["table"]}
    for name, r in tab.items():
        a_, b_ = name.split(" → ")
        con.execute(f"""
        CREATE OR REPLACE TABLE b21 AS
        SELECT a.case_id, a.d AS da, bc.d AS dbc, cc.cd AS cdc, bf.d AS dbf, cf.cd AS cdf
        FROM f21c a
        LEFT JOIN f21c bc ON bc.case_id = a.case_id AND bc.act2 = '{b_}'
        LEFT JOIN f21f bf ON bf.case_id = a.case_id AND bf.act2 = '{b_}'
        JOIN c21c cc ON cc.case_id = a.case_id JOIN c21f cf ON cf.case_id = a.case_id
        WHERE a.act2 = '{a_}' AND (bc.d IS NULL OR bc.d >= a.d) AND (bf.d IS NULL OR bf.d >= a.d)
        """)
        n = q("SELECT count(*) AS n, count(dbc) AS reach, median(dbc - da) AS obs, avg((dbf IS NOT NULL)::INT) AS share FROM b21").iloc[0]
        km_c = q(km_sql("SELECT greatest(coalesce(dbc, cdc) - da, 0) AS t, (dbc IS NOT NULL)::INT AS ev FROM b21"))["med"][0]
        km_f = q(km_sql("SELECT greatest(coalesce(dbf, cdf) - da, 0) AS t, (dbf IS NOT NULL)::INT AS ev FROM b21"))["med"][0]
        check("⑫ 21대 절단 실험", f"{name} 대상 의안", r["대상 의안"], n["n"])
        check("⑫ 21대 절단 실험", f"{name} T 전 도달", r["T 전 도달"], n["reach"])
        check("⑫ 21대 절단 실험", f"{name} ① 끝난 의안 기준", r["① 끝난 의안 기준"], n["obs"])
        check("⑫ 21대 절단 실험", f"{name} ② T 시점 KM", r["② T 시점 KM"], km_c)
        check("⑫ 21대 절단 실험", f"{name} ③ 정답(끝까지 KM)", r["③ 정답(끝까지 KM)"], km_f)
        check("⑫ 21대 절단 실험", f"{name} 임기 끝까지 도달 비율", r["임기 끝까지 도달 비율"], round(float(n["share"]), 3))
    check("⑫ 21대 절단 실험", "21대 원안 수", age["cases_21"], int(q("SELECT count(DISTINCT case_id) AS n FROM e21")["n"][0]))

# ⑬ 지연 법률안 특징(1016_11): delay_factors.py ↔ SQL. 법 이름·종류 규칙을 SQL 정규식으로 따로 작성
delay_path = OUT / "delay_results.json"
if delay_path.exists():
    dres = json.loads(delay_path.read_text(encoding="utf-8"))
    names = []
    for pg in sorted((ROOT / "data" / "raw" / "TVBPMBILL11" / f"AGE22_{QUERY_DATE.replace('-', '')}").glob("p*.json")):
        names += [(r["BILL_ID"], r["BILL_NAME"]) for r in json.loads(pg.read_text(encoding="utf-8"))["TVBPMBILL11"][1]["row"]]
    nm = pd.DataFrame(names, columns=["case_id", "bill_name"])
    con.register("nm_df", nm)
    con.execute("""
    CREATE TABLE nm AS
    SELECT case_id, bill_name,
           CASE WHEN regexp_matches(trim(bill_name), '(일부개정|전부개정|폐지)법률안$')
                THEN regexp_replace(trim(bill_name), '\s*(일부개정|전부개정|폐지)법률안$', '')
                ELSE regexp_replace(trim(bill_name), '안$', '') END AS law,
           CASE WHEN bill_name LIKE '%일부개정%' THEN '일부개정'
                WHEN bill_name LIKE '%전부개정%' OR bill_name LIKE '%폐지%' THEN '전부개정·폐지'
                ELSE '제정' END AS kind
    FROM nm_df WHERE case_id IN (SELECT DISTINCT case_id FROM e)
    """)
    con.execute("""
    CREATE TABLE dl AS
    WITH base AS (
      SELECT a.case_id, greatest(coalesce(b.d, c.cd) - a.d, 0) AS t, (b.d IS NOT NULL)::INT AS ev
      FROM f a LEFT JOIN f b ON b.case_id = a.case_id AND b.act2 = '소위 상정'
      JOIN cens c ON c.case_id = a.case_id
      WHERE a.act2 = '소위 회부' AND (b.d IS NULL OR b.d >= a.d)
    ), lawn AS (SELECT law, count(*) AS n FROM nm GROUP BY law),
    sc AS (SELECT DISTINCT case_id, first_value(cmt) OVER (PARTITION BY case_id ORDER BY d, rn) AS cmt FROM e WHERE activity = '소위 회부')
    SELECT base.*, nm.kind, any_value(e.proposer_kind) AS pk, sc.cmt,
           CASE WHEN lawn.n = 1 THEN '1건(혼자)' WHEN lawn.n <= 4 THEN '2～4' WHEN lawn.n <= 9 THEN '5～9'
                WHEN lawn.n <= 49 THEN '10～49' ELSE '50건 이상' END AS f1
    FROM base JOIN nm USING (case_id) JOIN lawn USING (law) JOIN e USING (case_id) LEFT JOIN sc USING (case_id)
    GROUP BY ALL
    """)
    gt = pd.read_csv(OUT / "tables" / "delay_groups.csv").set_index(["특징", "집단"])
    for feat, col in (("F1 같은 법 법률안 수", "f1"), ("F2 법률안 종류", "kind"), ("F3 제안자", "pk")):
        for grp in gt.loc[feat].index:
            r = q(km_sql(f"SELECT t, ev FROM dl WHERE {col} = '{grp}'", 365)).iloc[0]
            n = q(f"SELECT count(*) AS n FROM dl WHERE {col} = '{grp}'")["n"][0]
            check("⑬ 지연 법률안 특징", f"{grp} 법률안 수", gt.loc[(feat, grp), "법률안"], n)
            check("⑬ 지연 법률안 특징", f"{grp} KM 중앙값", gt.loc[(feat, grp), "KM 중앙값"], r["med"])
            check("⑬ 지연 법률안 특징", f"{grp} 1년 안 상정 비율", gt.loc[(feat, grp), "1년 안 상정 비율(보조)"],
                  round(float(r["reach"]), 4), tol=1e-4)
    check("⑬ 지연 법률안 특징", "D1 10건 이상 KM 중앙값", dres["D1"]["ten_plus_km"],
          q(km_sql("SELECT t, ev FROM dl WHERE f1 IN ('10～49', '50건 이상')"))["med"][0])
    # 위원회 안 비교: 두 집단 각 30건 이상인 위원회 수와 같은 방향(엄격한 부등호, 중앙값 없음 = ∞)인 위원회 수
    inf_ = 10**9
    for key, col, slow, fast in (("D1", "f1", "('1건(혼자)')", "('10～49', '50건 이상')"), ("D2", "kind", "('제정')", "('일부개정')"),
                                 ("D3", "pk", "('의원')", "('정부')")):
        cm = q(f"""SELECT cmt FROM dl WHERE cmt NOT LIKE '%특별위원회%' GROUP BY cmt
                   HAVING count(*) FILTER (WHERE {col} IN {slow}) >= 30 AND count(*) FILTER (WHERE {col} IN {fast}) >= 30""")["cmt"]
        same = 0
        for c in cm:
            ms = q(km_sql(f"SELECT t, ev FROM dl WHERE cmt = '{c}' AND {col} IN {slow}"))["med"][0]
            mf = q(km_sql(f"SELECT t, ev FROM dl WHERE cmt = '{c}' AND {col} IN {fast}"))["med"][0]
            same += (inf_ if pd.isna(ms) else ms) > (inf_ if pd.isna(mf) else mf)
        check("⑬ 지연 법률안 특징", f"{key} 비교 위원회 수", dres[key]["within"]["committees"], len(cm))
        check("⑬ 지연 법률안 특징", f"{key} 같은 방향 위원회 수", dres[key]["within"]["same_direction"], same)
    share = q("""
    WITH p AS (SELECT e.case_id, e.d, e.cmt, nm.law FROM e JOIN nm USING (case_id) WHERE e.activity = '소위 상정'),
    c AS (SELECT *, count(*) OVER (PARTITION BY cmt, d, law) AS n FROM p)
    SELECT avg((n >= 2)::INT) AS s, count(*) AS cnt FROM c""").iloc[0]
    check("⑬ 지연 법률안 특징", "소위 상정 이벤트 수", dres["sub_present_events"], share["cnt"])
    check("⑬ 지연 법률안 특징", "같은 날 같은 법과 함께 상정된 비율", dres["same_law_same_day_share"], round(float(share["s"]), 3))

# ⑭ 소위 반복 심사(1016_10): meetings.py ↔ SQL. 회의정보 원본 JSON을 DuckDB가 직접 읽는다(pandas 미사용)
meet_path = OUT / "meetings_results.json"
if meet_path.exists() and delay_path.exists():
    mres = json.loads(meet_path.read_text(encoding="utf-8"))
    conf_dir = (ROOT / "data" / "raw" / "BILLJUDGECONF" / f"AGE22_{mres['conf_date']}").as_posix()
    con.execute(f"""
    CREATE TABLE cf AS
    SELECT parse_filename(filename, true) AS case_id, content
    FROM read_text('{conf_dir}/*.json') WHERE NOT filename LIKE '%manifest.json'
    """)
    con.execute("""
    CREATE TABLE cr AS
    SELECT case_id, r.JRCMIT_CONF_NM AS nm, CAST(r.JRCMIT_CONF_DT AS DATE) AS d, r.JRCMIT_CONF_RSLT AS rs
    FROM (SELECT case_id, unnest(from_json(json_extract(content, '$.BILLJUDGECONF[1].row'),
          '[{"JRCMIT_CONF_NM": "VARCHAR", "JRCMIT_CONF_DT": "VARCHAR", "JRCMIT_CONF_RSLT": "VARCHAR"}]')) AS r
          FROM cf WHERE json_extract(content, '$.BILLJUDGECONF') IS NOT NULL)
    """)
    con.execute(f"CREATE TABLE crq AS SELECT * FROM cr WHERE d <= DATE '{QUERY_DATE}' AND nm LIKE '%소위회의%'")
    con.execute("""
    CREATE TABLE mp AS
    WITH p AS (SELECT DISTINCT case_id, d FROM crq WHERE rs = '상정'),
    k AS (SELECT case_id, d, row_number() OVER (PARTITION BY case_id ORDER BY d) AS k FROM p),
    lawn AS (SELECT law, count(*) AS n FROM nm GROUP BY law)
    SELECT case_id, count(*) AS n, min(d) AS first, max(d) FILTER (WHERE k = 2) - min(d) AS gap,
           any_value(CASE WHEN lawn.n = 1 THEN '1건(혼자)' WHEN lawn.n >= 50 THEN '50건 이상' ELSE '그 밖' END) AS f1
    FROM k JOIN nm USING (case_id) JOIN lawn USING (law) GROUP BY case_id
    """)
    b = q("SELECT count(*) AS req, count(*) FILTER (WHERE json_extract(content, '$.BILLJUDGECONF') IS NULL) AS nd FROM cf").iloc[0]
    cnt = q(f"SELECT count(*) AS r, count(*) FILTER (WHERE d > DATE '{QUERY_DATE}') AS late FROM cr").iloc[0]
    m = q("""SELECT count(*) AS n, sum((n >= 2)::INT) AS rep, avg((n >= 2)::INT) AS share,
                    quantile_cont(gap, 0.5) AS med, quantile_cont(gap, 0.25) AS q1, quantile_cont(gap, 0.75) AS q3,
                    avg((n >= 2)::INT) FILTER (WHERE f1 = '1건(혼자)') AS alone, count(*) FILTER (WHERE f1 = '1건(혼자)') AS n_alone,
                    avg((n >= 2)::INT) FILTER (WHERE f1 = '50건 이상') AS big, count(*) FILTER (WHERE f1 = '50건 이상') AS n_big,
                    max(n) AS mx FROM mp""").iloc[0]
    have = int(b["req"] - b["nd"])
    check("⑭ 소위 반복 심사", "받은 법률안 수", mres["bills_requested"], b["req"])
    check("⑭ 소위 반복 심사", "자료 없음", mres["no_data"], b["nd"])
    check("⑭ 소위 반복 심사", "회의 기록 행 수", mres["rows"], cnt["r"])
    check("⑭ 소위 반복 심사", "조회일 뒤 행 수", mres["rows_after_query_date"], cnt["late"])
    check("⑭ 소위 반복 심사", "소위 상정 있는 법률안", mres["bills_sub_present"], m["n"])
    check("⑭ 소위 반복 심사", "소위 상정 없음", mres["no_sub_present"], have - m["n"])
    check("⑭ 소위 반복 심사", "R1 반복 심사 법률안", mres["R1"]["repeat"], m["rep"])
    check("⑭ 소위 반복 심사", "R1 반복 심사 비율", mres["R1"]["share"], round(float(m["share"]), 4), tol=1e-4)
    check("⑭ 소위 반복 심사", "R2 재상정 간격 중앙값", mres["R2"]["median_gap"], m["med"])
    check("⑭ 소위 반복 심사", "재상정 간격 1사분위", mres["aux"]["gap_quartiles"][0], m["q1"])
    check("⑭ 소위 반복 심사", "재상정 간격 3사분위", mres["aux"]["gap_quartiles"][1], m["q3"])
    check("⑭ 소위 반복 심사", "R3 혼자 법률안 수", mres["R3"]["n_alone"], m["n_alone"])
    check("⑭ 소위 반복 심사", "R3 혼자 반복 비율", mres["R3"]["alone_share"], round(float(m["alone"]), 4), tol=1e-4)
    check("⑭ 소위 반복 심사", "R3 50건 이상 법률안 수", mres["R3"]["n_50plus"], m["n_big"])
    check("⑭ 소위 반복 심사", "R3 50건 이상 반복 비율", mres["R3"]["50plus_share"], round(float(m["big"]), 4), tol=1e-4)
    check("⑭ 소위 반복 심사", "최대 상정 횟수", mres["aux"]["max_presentations"], m["mx"])
    dist = q("SELECT least(n, 4) AS k, count(*) AS c FROM mp GROUP BY k ORDER BY k")
    for (label, v), c in zip(mres["aux"]["distribution"].items(), dist["c"]):
        check("⑭ 소위 반복 심사", f"상정 횟수 {label}", v, c)
    cm = q("""SELECT count(*) FILTER (WHERE mp.first = ef.d) AS c FROM cf
              LEFT JOIN mp USING (case_id)
              LEFT JOIN (SELECT case_id, min(d) AS d FROM e WHERE activity = '소위 상정' GROUP BY case_id) ef USING (case_id)
              WHERE json_extract(content, '$.BILLJUDGECONF') IS NOT NULL""")["c"][0]
    check("⑭ 소위 반복 심사", "C 첫 소위 상정 날짜 일치", mres["C"]["match"], cm)
    ov = q("""SELECT count(DISTINCT mp.case_id) FILTER (WHERE v.case_id IS NOT NULL) * 1.0 / count(DISTINCT mp.case_id) AS s
              FROM mp LEFT JOIN (SELECT DISTINCT case_id, d FROM crq WHERE rs = '의결') v
              ON v.case_id = mp.case_id AND v.d = mp.first WHERE mp.n = 1""")["s"][0]
    check("⑭ 소위 반복 심사", "1회 상정 중 그날 의결", mres["aux"]["one_present_voted_same_day"], round(float(ov), 4), tol=1e-4)
    op = q("""SELECT avg(only_p::INT) AS s FROM (SELECT case_id, d, bool_and(rs = '상정') AS only_p FROM crq GROUP BY case_id, d)""")["s"][0]
    check("⑭ 소위 반복 심사", "상정만 있는 소위 회의 비율", mres["aux"]["sub_meeting_days_only_present"], round(float(op), 4), tol=1e-4)

# ⑮ OCEL(1016_12): ocel.py ↔ SQL. 사건 합치기 규칙·O1·O2를 SQL로 따로 작성(I1은 모델 품질 지표라 SQL 대조 없음)
ocel_path = OUT / "ocel_results.json"
if ocel_path.exists() and delay_path.exists():
    ores = json.loads(ocel_path.read_text(encoding="utf-8"))
    con.register("nm_all_df", nm)
    con.execute("""
    CREATE TABLE nm2 AS
    WITH s AS (SELECT case_id, regexp_replace(trim(bill_name), '\\s*\\((대안|위원회안)\\)\\s*$', '') AS b FROM nm_all_df)
    SELECT case_id, CASE WHEN regexp_matches(b, '(일부개정|전부개정|폐지)법률안$')
                         THEN regexp_replace(b, '\\s*(일부개정|전부개정|폐지)법률안$', '')
                         ELSE regexp_replace(b, '안$', '') END AS law
    FROM s
    """)
    con.execute("""
    CREATE TABLE oe AS
    WITH k AS (
      SELECT *, CASE WHEN activity IN ('소관위 상정', '소관위 처리') THEN '소관위'
                     WHEN activity IN ('소위 상정', '소위 처리') THEN '소위'
                     WHEN activity IN ('법사위 상정', '법사위 처리') THEN '법사위'
                     WHEN activity IN ('가결', '부결') THEN '본회의' END AS stage
      FROM eall),
    m AS (
      SELECT *, CASE WHEN stage IS NULL THEN NULL
                     WHEN stage = '법사위' THEN stage || '|법제사법위원회|' || CAST(d AS VARCHAR)
                     WHEN stage = '본회의' THEN stage || '|본회의|' || CAST(d AS VARCHAR)
                     WHEN cmt IS NULL THEN NULL
                     ELSE stage || '|' || cmt || '|' || CAST(d AS VARCHAR) END AS mk
      FROM k),
    alt_plen AS (
      SELECT case_id AS alt_id, arg_min(activity || '|' || mk, rn) AS peid, arg_min(d, rn) AS pd
      FROM m WHERE case_group = '위원회안' AND activity IN ('가결', '부결') GROUP BY case_id)
    SELECT m.case_id, m.activity, m.d, m.case_group,
           CASE WHEN m.activity IN ('대안반영폐기', '수정안반영폐기') AND m.case_group = '원안' AND ap.pd = m.d THEN ap.peid
                WHEN m.mk IS NOT NULL THEN m.activity || '|' || m.mk
                ELSE 'e' || CAST(m.rn AS VARCHAR) END AS eid,
           (m.activity IN ('대안반영폐기', '수정안반영폐기') AND m.case_group = '원안') AS is_abs,
           (m.activity IN ('대안반영폐기', '수정안반영폐기') AND m.case_group = '원안' AND ap.pd = m.d) AS abs_merged,
           CASE WHEN m.activity IN ('대안반영폐기', '수정안반영폐기') AND m.case_group = '원안' AND ap.pd = m.d
                THEN split_part(ap.peid, '|', 2) || '|' || split_part(ap.peid, '|', 3) || '|' || split_part(ap.peid, '|', 4)
                ELSE m.mk END AS mk
    FROM m LEFT JOIN lk ON lk.orig_bill_id = m.case_id
           LEFT JOIN alt_plen ap ON ap.alt_id = lk.alt_bill_id
    """)
    t = q("""SELECT count(*) AS rows_, count(DISTINCT eid) AS ev, sum(is_abs::INT) AS ab, sum(abs_merged::INT) AS abm,
                    count(DISTINCT mk) AS meetings FROM oe""").iloc[0]
    check("⑮ OCEL", "케이스 중심 사건 수", ores["case_events"], t["rows_"])
    check("⑮ OCEL", "OCEL 사건 수", ores["ocel_events"], t["ev"])
    check("⑮ OCEL", "대안반영폐기·수정안반영폐기 행", ores["absorb"]["rows"], t["ab"])
    check("⑮ OCEL", "대안 본회의 사건에 합쳐진 행", ores["absorb"]["merged_same_day"], t["abm"])
    check("⑮ OCEL", "회의 객체 수", ores["objects"]["회의"], t["meetings"])
    check("⑮ OCEL", "법 객체 수", ores["objects"]["법"],
          q("SELECT count(DISTINCT law) AS n FROM nm2 WHERE case_id IN (SELECT DISTINCT case_id FROM eall)")["n"][0])
    infl = q("""
    WITH p AS (SELECT activity, eid, count(DISTINCT case_id) AS n FROM oe
               WHERE mk IS NOT NULL AND activity IN ('소관위 상정', '소관위 처리', '소위 상정', '소위 처리',
                                                    '법사위 상정', '법사위 처리', '가결', '부결') GROUP BY ALL)
    SELECT activity, count(*) AS ev, sum(n) AS cs, quantile_cont(n, 0.5) AS med FROM p GROUP BY activity""").set_index("activity")
    for a, r in ores["inflation"].items():
        check("⑮ OCEL", f"{a} OCEL 사건 수", r["ocel"], infl.loc[a, "ev"])
        check("⑮ OCEL", f"{a} 케이스 중심 사건 수", r["case"], infl.loc[a, "cs"])
        check("⑮ OCEL", f"{a} 사건당 법률안 중앙값", r["median_bills"], infl.loc[a, "med"])
    # O1: (위원회, 날짜, 법)마다 기다리던 같은 법 원안이 모두 그날 상정됐나
    o1 = q("""
    WITH r AS (SELECT case_id, cmt, min(d) AS rd FROM e WHERE activity = '소위 회부' AND cmt IS NOT NULL GROUP BY ALL),
    fp AS (SELECT case_id, min(d) AS fp FROM e WHERE activity = '소위 상정' GROUP BY case_id),
    en AS (SELECT case_id, min(d) AS ed FROM e
           WHERE activity IN ('가결', '부결', '폐기', '철회', '대안반영폐기', '수정안반영폐기') GROUP BY case_id),
    rr AS (SELECT r.*, n.law, fp.fp, en.ed FROM r JOIN nm2 n USING (case_id) LEFT JOIN fp USING (case_id) LEFT JOIN en USING (case_id)),
    pr AS (SELECT DISTINCT e.case_id, e.cmt, e.d, n.law FROM e JOIN nm2 n USING (case_id)
           WHERE e.activity = '소위 상정' AND e.cmt IS NOT NULL AND n.law IS NOT NULL),
    pairs AS (SELECT DISTINCT cmt, d, law FROM pr WHERE (cmt, law) IN (SELECT cmt, law FROM rr)),
    w AS (SELECT p.cmt, p.d, p.law, rr.case_id FROM pairs p JOIN rr ON rr.cmt = p.cmt AND rr.law = p.law
          WHERE rr.rd <= p.d AND (rr.fp IS NULL OR rr.fp >= p.d) AND (rr.ed IS NULL OR rr.ed >= p.d)),
    agg AS (SELECT p.cmt, p.d, p.law, count(w.case_id) AS nw,
                   count(w.case_id) FILTER (WHERE w.case_id NOT IN (SELECT case_id FROM pr x
                                            WHERE x.cmt = p.cmt AND x.d = p.d AND x.law = p.law)) AS miss
            FROM pairs p LEFT JOIN w ON w.cmt = p.cmt AND w.d = p.d AND w.law = p.law GROUP BY ALL)
    SELECT count(*) AS pairs, count(*) FILTER (WHERE nw <= 1) AS alone,
           count(*) FILTER (WHERE nw > 1 AND miss = 0) AS together, count(*) FILTER (WHERE nw > 1) AS used FROM agg""").iloc[0]
    check("⑮ OCEL", "O1 (위원회, 날짜, 법) 수", ores["O1"]["pairs"], o1["pairs"])
    check("⑮ OCEL", "O1 혼자 기다림(분모 제외)", ores["O1"]["alone_excluded"], o1["alone"])
    check("⑮ OCEL", "O1 모두 함께 상정", ores["O1"]["all_together"], o1["together"])
    check("⑮ OCEL", "O1 비율", ores["O1"]["share"], round(float(o1["together"] / o1["used"]), 4), tol=1e-4)
    o2 = q("""
    WITH r AS (SELECT case_id, min(d) AS rd FROM e WHERE activity = '소관위 회부' GROUP BY case_id),
    x AS (SELECT lk.alt_bill_id, r.rd FROM lk LEFT JOIN r ON r.case_id = lk.orig_bill_id),
    g AS (SELECT alt_bill_id, max(rd) - min(rd) AS span FROM x WHERE rd IS NOT NULL GROUP BY alt_bill_id HAVING count(*) >= 2)
    SELECT (SELECT count(*) FROM x WHERE rd IS NULL) AS no_ref, count(*) AS n, quantile_cont(span, 0.5) AS med,
           quantile_cont(span, 0.25) AS q1, quantile_cont(span, 0.75) AS q3 FROM g""").iloc[0]
    check("⑮ OCEL", "O2 회부일 없는 원안", ores["O2"]["orig_without_ref"], o2["no_ref"])
    check("⑮ OCEL", "O2 대상 대안 수", ores["O2"]["alts_used"], o2["n"])
    check("⑮ OCEL", "O2 회부일 범위 중앙값", ores["O2"]["median_span"], o2["med"])
    check("⑮ OCEL", "O2 1사분위", ores["O2"]["quartiles"][0], o2["q1"])
    check("⑮ OCEL", "O2 3사분위", ores["O2"]["quartiles"][1], o2["q3"])

# ⑯ 회부 지연 확인(1017_13): referral_check.py ↔ SQL
ref_path = OUT / "referral_results.json"
if ref_path.exists():
    rres = json.loads(ref_path.read_text(encoding="utf-8"))
    rc = q("""
    WITH p AS (SELECT case_id, min(d) AS pd FROM e WHERE activity = '제안' GROUP BY case_id),
    r AS (SELECT case_id, min(d) AS rd FROM e WHERE activity = '소관위 회부' GROUP BY case_id),
    c AS (SELECT DISTINCT case_id, first_value(cmt) OVER (PARTITION BY case_id ORDER BY rn) AS cmt FROM e),
    fr AS (SELECT cmt, min(d) AS f, min(d) FILTER (WHERE d >= DATE '2026-05-30') AS h FROM e
           WHERE activity IN ('소관위 회부', '소관위 재회부') GROUP BY cmt),
    x AS (SELECT r.case_id, r.rd, p.pd, r.rd - p.pd AS days, c.cmt, fr.f, fr.h
          FROM r JOIN p USING (case_id) JOIN c USING (case_id) LEFT JOIN fr USING (cmt) WHERE r.rd - p.pd > 30),
    y AS (SELECT *, count(*) OVER (PARTITION BY cmt, rd) AS nb FROM x)
    SELECT CASE WHEN rd = h AND pd < h THEN '1 후반기 원구성'
                WHEN cmt LIKE '%특별위원회%' AND pd < f THEN '2 특별위원회 생기기 전 제안'
                WHEN nb >= 5 THEN '3 같은 날 한 위원회로 일괄 회부' ELSE '4 그 밖' END AS cat, count(*) AS n
    FROM y GROUP BY cat""").set_index("cat")["n"]
    check("⑯ 회부 지연 확인", "30일 초과 원안", rres["over_30"], int(rc.sum()))
    for k, v in rres["categories"].items():
        check("⑯ 회부 지연 확인", k, v, rc.get(k, 0))

# ⑯ 화면 확인(1017_13 6절): referral_screen.py(정규식) ↔ lxml XPath로 원본 조각을 따로 읽음
scr_path = OUT / "referral_screen.json"
if scr_path.exists():
    from lxml import html as lh
    sres = json.loads(scr_path.read_text(encoding="utf-8"))
    prop = q("SELECT bill_no, min(CAST(date AS DATE)) AS pd FROM ev WHERE activity = '제안' GROUP BY bill_no").set_index("bill_no")["pd"]
    multi = early = ret = 0
    files = sorted((ROOT / "data" / "raw" / "likms_billInfo").glob("*/*.html"))
    for f in files:
        tb = lh.fromstring(f.read_text(encoding="utf-8")).xpath("//h4[text()='소관위 심사정보']/following-sibling::table[1]/tbody/tr")
        refs = [t.xpath("string(td[contains(@class,'submitDt')]/span)").strip() for t in tb]
        res_ = [t.xpath("string(td[contains(@class,'inscResultCd')]/span)").strip() for t in tb]
        if len(refs) > 1:
            multi += 1
            early += (pd.Timestamp(min(refs)) - pd.Timestamp(prop[f.stem])).days > 30
        ret += "회송" in res_
    check("⑯ 화면 확인", "받은 화면 수", sres["bills"], len(files))
    check("⑯ 화면 확인", "앞선 회부 있음", sres["screen"].get("앞선 회부 있음", 0), multi)
    check("⑯ 화면 확인", "앞선 회부 중 처음 회부도 30일 초과", sres["first_days_over_30"], early)
    check("⑯ 화면 확인", "회송 기록 있음", sres["returned_any"], ret)

# 기본 건수
n = q("SELECT count(*) AS ev, count(DISTINCT case_id) AS cs FROM e").iloc[0]
na = q("SELECT count(*) AS ev, count(DISTINCT case_id) AS cs FROM eall").iloc[0]
check("기본", "원안 이벤트 수", res["events"], n["ev"])
check("기본", "원안 케이스 수", res["cases"], n["cs"])
check("기본", "전체 이벤트 수", res["events_all"], na["ev"])
check("기본", "전체 케이스 수", res["cases_all"], na["cs"])

out = pd.DataFrame(rows)
out.to_csv(OUT / "cross_check.csv", index=False, encoding="utf-8-sig")
summary = out.groupby("지표")["일치"].agg(["size", "sum"]).rename(columns={"size": "대조", "sum": "일치"})
print(summary.to_string())
bad = out[~out["일치"]]
print(f"전체 {len(out)}개 중 일치 {int(out['일치'].sum())}개")
if len(bad):
    print(bad.to_string(index=False))
    sys.exit("불일치 있음")
