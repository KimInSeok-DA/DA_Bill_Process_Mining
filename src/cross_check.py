"""핵심 지표를 DuckDB SQL로 처음부터 다시 계산해 pm4py·pandas 결과와 대조한다. 정의: docs/의사결정/1015_05_교차검증.md

- 입력은 이벤트 로그 CSV와 원안-대안 연결 CSV다. analysis.py의 함수는 쓰지 않는다(같은 코드를 다시 돌리는 것은 검증이 아니다)
- ①～⑤는 원안(의원·정부) 기준, ⑥은 의원·정부별 KM, ⑦은 대안반영폐기 원안의 대안 경로(1015_06 Q7·Q9),
  ⑧ 위원회별 KM(H5), ⑨ 월별 건수(H6), ⑩ 대안당 원안 수(Q8)
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
QUERY_DATE = "2026-10-15"

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
