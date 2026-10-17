"""객체 중심 이벤트 로그(OCEL): 원안·대안·법·회의를 함께 본다. 정의·가설: docs/의사결정/1016_12_OCEL.md

- 사건 합치기(결정 2): 회의에서 일어나는 활동(상정·처리·본회의 가결·부결)은 같은 활동·같은 회의(단계·위원회·날짜)면 사건 하나.
  원안의 대안반영폐기·수정안반영폐기는 흡수한 대안의 본회의 처리와 날짜가 같으면 그 사건에 합친다
- 객체(결정 1): 원안, 대안(위원장 제안), 법(1016_11 법 이름), 회의(단계|위원회|날짜)
- 측정: 부풀림·끊김. 가설: O1 같은 법 원안은 함께 상정되나, O2 대안이 모은 원안의 회부일 범위, I1 Inductive Miner 대 국회법 모델
- 출력: data/processed/ocel/bills.jsonocel(올리지 않음), outputs/ocel_results.json, outputs/tables/ocel_*.csv,
  outputs/figures/fig15_ocel.png

실행: .venv/Scripts/python src/ocel.py
"""

import json
import re
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

warnings.filterwarnings("ignore")
import pm4py  # noqa: E402
from pm4py.objects.log.obj import Event, EventLog, Trace  # noqa: E402
from pm4py.objects.ocel.obj import OCEL  # noqa: E402

from analysis import SOURCE, load  # noqa: E402
from conformance import build_net  # noqa: E402
from delay_factors import bill_names, law_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
MERGE = {"소관위 상정": "소관위", "소관위 처리": "소관위", "소위 상정": "소위", "소위 처리": "소위",
         "법사위 상정": "법사위", "법사위 처리": "법사위", "가결": "본회의", "부결": "본회의"}
ABSORB = ("대안반영폐기", "수정안반영폐기")
END = ("가결", "부결", "폐기", "철회") + ABSORB
NOISE = 0.2  # I1 판정용(1016_12 5절). 0.0·0.4는 보조
O1_MIN, O2_MIN, MIN_SPAN = 0.70, 2, 180

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False


def law_of(name: str) -> str:
    """법률안 이름 → 법 이름. 대안 이름 끝의 "(대안)"·"(위원회안)"을 먼저 뗀다(1016_12 3절)."""
    return law_name(re.sub(r"\s*\((대안|위원회안)\)\s*$", "", name.strip()))


def meeting_key(r) -> str | None:
    stage = MERGE.get(r["activity"])
    if stage is None:
        return None
    place = {"법사위": "법제사법위원회", "본회의": "본회의"}.get(stage, r["committee_n"])
    if pd.isna(place):
        return None
    return f"{stage}|{place}|{r['date']}"


def build(full: pd.DataFrame, names: pd.Series, links: pd.DataFrame):
    ev = full.reset_index(drop=True).copy()
    ev["row"] = range(len(ev))
    ev["meeting"] = ev.apply(meeting_key, axis=1)
    ev["eid"] = [f"{a}|{m}" if pd.notna(m) else f"e{i}" for i, a, m in zip(ev["row"], ev["activity"], ev["meeting"])]

    # 대안반영폐기·수정안반영폐기 → 흡수한 대안의 본회의 처리 사건(날짜가 같을 때만)
    alt_of = links.set_index("orig_bill_id")["alt_bill_id"]
    plen = ev[(ev["case_group"] == "위원회안") & ev["activity"].isin(["가결", "부결"])]
    plen_eid = plen.drop_duplicates("case_id").set_index("case_id")[["eid", "date"]]
    ab = ev["activity"].isin(ABSORB) & (ev["case_group"] == "원안")
    alt = ev.loc[ab, "case_id"].map(alt_of)
    tgt = plen_eid.reindex(alt.values)
    same = (tgt["date"].values == ev.loc[ab, "date"].values) & tgt["eid"].notna().values
    ev.loc[ev.index[ab][same], "eid"] = tgt["eid"].values[same]
    ev.loc[ev.index[ab][same], "meeting"] = [e.split("|", 1)[1] for e in tgt["eid"].values[same]]
    absorb = {"rows": int(ab.sum()), "no_alt_link": int(alt.isna().sum()),
              "alt_no_plenary": int((alt.notna().values & tgt["eid"].isna().values).sum()),
              "merged_same_day": int(same.sum())}

    # 사건: 합쳐진 사건의 활동·날짜는 대표 행(대안의 본회의 처리가 있으면 그것)
    rep = ev.sort_values(by="activity", key=lambda s: s.isin(ABSORB)).drop_duplicates("eid")
    # 같은 날 사건은 단계 순서(step_order)로 정렬한다. pm4py는 같은 시각이면 표 순서를 따른다
    rep = rep.sort_values(["ts", "step_order"], kind="mergesort")
    events = pd.DataFrame({"ocel:eid": rep["eid"], "ocel:activity": rep["activity"],
                           "ocel:timestamp": pd.to_datetime(rep["date"])}).reset_index(drop=True)
    pos = pd.Series(range(len(events)), index=events["ocel:eid"])

    law = names.map(law_of)
    otype = {"원안": "원안", "위원회안": "대안"}
    case_obj = ev.drop_duplicates("case_id")[["case_id", "case_group"]]
    objects = pd.concat([
        pd.DataFrame({"ocel:oid": case_obj["case_id"], "ocel:type": case_obj["case_group"].map(otype)}),
        pd.DataFrame({"ocel:oid": "법:" + law.reindex(case_obj["case_id"]).unique(), "ocel:type": "법"}),
        pd.DataFrame({"ocel:oid": "회의:" + ev["meeting"].dropna().unique(), "ocel:type": "회의"}),
    ], ignore_index=True)

    rel = pd.concat([
        pd.DataFrame({"ocel:eid": ev["eid"], "ocel:oid": ev["case_id"], "ocel:qualifier": ev["activity"]}),
        pd.DataFrame({"ocel:eid": ev["eid"], "ocel:oid": "법:" + law.reindex(ev["case_id"]).values, "ocel:qualifier": "법"}),
        pd.DataFrame({"ocel:eid": ev["eid"], "ocel:oid": "회의:" + ev["meeting"], "ocel:qualifier": "회의"}).dropna(),
    ], ignore_index=True).drop_duplicates(["ocel:eid", "ocel:oid"])
    rel = rel.merge(events, on="ocel:eid").merge(objects, on="ocel:oid")
    rel = rel.iloc[rel["ocel:eid"].map(pos).argsort(kind="mergesort")].reset_index(drop=True)

    o2o = pd.concat([
        pd.DataFrame({"ocel:oid": case_obj["case_id"], "ocel:oid_2": "법:" + law.reindex(case_obj["case_id"]).values,
                      "ocel:qualifier": "고친다"}),
        pd.DataFrame({"ocel:oid": links["orig_bill_id"], "ocel:oid_2": links["alt_bill_id"], "ocel:qualifier": "흡수됨"}),
    ], ignore_index=True)
    o2o = o2o[o2o["ocel:oid"].isin(objects["ocel:oid"]) & o2o["ocel:oid_2"].isin(objects["ocel:oid"])]
    return ev, OCEL(events=events, objects=objects, relations=rel, o2o=o2o), absorb


def o1_together(full: pd.DataFrame, law: pd.Series) -> dict:
    """소위 상정(위원회·날짜·법)마다, 그날 같은 위원회 소위에서 기다리던 같은 법 원안이 모두 함께 상정됐나."""
    log = full[full["case_group"] == "원안"]
    ref = log[log["activity"] == "소위 회부"].groupby(["case_id", "committee_n"])["ts"].min().reset_index()
    first_pres = log[log["activity"] == "소위 상정"].groupby("case_id")["ts"].min()
    end = log[log["activity"].isin(END)].groupby("case_id")["ts"].min()
    ref["law"] = law.reindex(ref["case_id"]).values
    ref["first_pres"] = first_pres.reindex(ref["case_id"]).values
    ref["end"] = end.reindex(ref["case_id"]).values
    pres = log[log["activity"] == "소위 상정"][["case_id", "committee_n", "ts"]].drop_duplicates()
    pres["law"] = law.reindex(pres["case_id"]).values
    cand = dict(tuple(ref.groupby(["committee_n", "law"])))
    rows = []
    for (c, d, lw), g in pres.groupby(["committee_n", "ts", "law"]):
        r = cand.get((c, lw))
        if r is None:
            continue
        w = r[(r["ts"] <= d) & ~(r["first_pres"] < d) & ~(r["end"] < d)]
        waiting = set(w["case_id"])
        rows.append({"committee": c, "date": d, "presented": g["case_id"].nunique(), "waiting": len(waiting),
                     "all_together": waiting <= set(g["case_id"])})
    t = pd.DataFrame(rows)
    alone = t["waiting"] <= 1
    u = t[~alone]
    share = float(u["all_together"].mean())
    return {"pairs": len(t), "alone_excluded": int(alone.sum()), "pairs_used": len(u),
            "all_together": int(u["all_together"].sum()), "share": round(share, 4), "support": share >= O1_MIN}


def o2_span(full: pd.DataFrame, links: pd.DataFrame) -> tuple[dict, pd.Series]:
    log = full[full["case_group"] == "원안"]
    refd = log[log["activity"] == "소관위 회부"].groupby("case_id")["ts"].min()
    lk = links.assign(ref=refd.reindex(links["orig_bill_id"]).values)
    no_ref = int(lk["ref"].isna().sum())
    g = lk.dropna(subset=["ref"]).groupby("alt_bill_id")["ref"].agg(["size", "min", "max"])
    g = g[g["size"] >= O2_MIN]
    span = (g["max"] - g["min"]).dt.days
    med = float(span.median())
    return {"alts_with_links": int(links["alt_bill_id"].nunique()), "orig_without_ref": no_ref, "alts_used": len(g),
            "median_span": med, "quartiles": [float(x) for x in span.quantile([0.25, 0.75])],
            "support": med >= MIN_SPAN}, span


def i1_models(full: pd.DataFrame) -> dict:
    """끝난 원안: Inductive Miner 모델 대 국회법 모델(1016_08)의 정렬 기반 적합도·정밀도."""
    log = full[(full["case_group"] == "원안") & (full["case_status"] == "완료")]
    traces = log.groupby("case_id", sort=False)["activity"].agg(tuple)
    elog = EventLog([Trace([Event({"concept:name": a}) for a in t]) for t in traces])
    res = {"cases": len(elog)}
    models = {"국회법 모델": build_net()}
    for nz in (NOISE, 0.0, 0.4):
        models[f"Inductive Miner {nz}"] = pm4py.discover_petri_net_inductive(elog, noise_threshold=nz)
    for name, (net, im, fm) in models.items():
        t0 = time.time()
        fa = pm4py.fitness_alignments(elog, net, im, fm)
        pa = pm4py.precision_alignments(elog, net, im, fm)
        tr = pm4py.fitness_token_based_replay(elog, net, im, fm)
        res[name] = {"fitness": round(float(fa["average_trace_fitness"]), 4),
                     "perfect_share": round(float(fa["percentage_of_fitting_traces"]) / 100, 4),
                     "precision": round(float(pa), 4), "token_fitness": round(float(tr["average_trace_fitness"]), 4),
                     "transitions": len(net.transitions), "places": len(net.places), "sec": round(time.time() - t0, 1)}
    law_m, im_m = res["국회법 모델"], res[f"Inductive Miner {NOISE}"]
    res["support"] = bool(round(im_m["fitness"], 3) > round(law_m["fitness"], 3)
                          and round(im_m["precision"], 3) < round(law_m["precision"], 3))
    return res


def main() -> None:
    full = load()
    names = bill_names()
    links = pd.read_csv(ROOT / "data" / "processed" / "alt_links.csv", dtype=str)
    ev, ocel, absorb = build(full, names, links)
    out_dir = ROOT / "data" / "processed" / "ocel"
    out_dir.mkdir(parents=True, exist_ok=True)
    pm4py.write_ocel2_json(ocel, str(out_dir / "bills.jsonocel"))

    # 처리 전후 대조
    n_rows, n_events = len(ev), ocel.events["ocel:eid"].nunique()
    linked = set(ocel.relations["ocel:oid"])
    case_types = ocel.objects[ocel.objects["ocel:type"].isin(["원안", "대안"])]["ocel:oid"]
    assert case_types.isin(linked).all()
    ot = ocel.objects["ocel:type"].value_counts()

    # 부풀림: 합친 활동별 케이스 중심 사건 수 ÷ OCEL 사건 수, 사건 하나에 연결된 법률안 수
    m = ev[ev["meeting"].notna() & ev["activity"].isin(MERGE)]
    per_event = m.groupby(["activity", "eid"])["case_id"].nunique()
    infl = per_event.groupby("activity").agg(["size", "sum", "median"])
    infl.columns = ["OCEL 사건", "케이스 중심 사건(법률안)", "사건당 법률안 중앙값"]
    infl["부풀림 배수"] = (infl["케이스 중심 사건(법률안)"] / infl["OCEL 사건"]).round(2)
    infl = infl.reindex([a for a in MERGE if a in infl.index])
    infl.to_csv(OUT / "tables" / "ocel_inflation.csv", encoding="utf-8-sig")

    # 객체 중심 흐름도(OC-DFG): 객체 종류별 직접 이어지는 활동 쌍 상위
    dfg = pm4py.discover_ocdfg(ocel)
    edge_rows = []
    # total_objects = 객체가 그 흐름을 지나간 횟수, event_couples = 서로 다른 사건 쌍의 수(합쳐진 사건은 한 번)
    for otp, pairs in dfg["edges"]["total_objects"].items():
        for (a, b), objs in pairs.items():
            edge_rows.append({"객체 종류": otp, "앞 활동": a, "뒤 활동": b, "이어진 횟수": len(objs),
                              "서로 다른 사건 쌍": len(dfg["edges"]["event_couples"][otp][(a, b)])})
    edges = pd.DataFrame(edge_rows).sort_values(["객체 종류", "이어진 횟수"], ascending=[True, False])
    edges.groupby("객체 종류").head(12).to_csv(OUT / "tables" / "ocel_ocdfg_edges.csv", index=False, encoding="utf-8-sig")

    law = names.map(law_of)
    o1 = o1_together(full, law)
    o2, span = o2_span(full, links)
    i1 = i1_models(full)

    res = {
        "case_events": n_rows, "ocel_events": int(n_events), "merged_away": int(n_rows - n_events),
        "objects": {k: int(v) for k, v in ot.items()}, "relations": len(ocel.relations), "o2o": len(ocel.o2o),
        "absorb": absorb | {"merged_share": round(absorb["merged_same_day"] / absorb["rows"], 4)},
        "inflation": {a: {"ocel": int(r["OCEL 사건"]), "case": int(r["케이스 중심 사건(법률안)"]),
                          "ratio": float(r["부풀림 배수"]), "median_bills": float(r["사건당 법률안 중앙값"])}
                      for a, r in infl.iterrows()},
        "O1": o1, "O2": o2, "I1": i1,
    }
    draw(infl, span, i1, OUT / "figures" / "fig15_ocel.png")
    (OUT / "ocel_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pd.set_option("display.width", 200)
    print(infl.to_string())
    print(edges.groupby("객체 종류").head(6).to_string(index=False))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


def draw(infl: pd.DataFrame, span: pd.Series, i1: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.4), gridspec_kw={"width_ratios": [1.4, 1, 1]})
    a = axes[0]
    y = range(len(infl))
    a.barh([i + 0.2 for i in y], infl["케이스 중심 사건(법률안)"], height=0.4, color="#bbb", label="케이스 중심(법률안마다)")
    a.barh([i - 0.2 for i in y], infl["OCEL 사건"], height=0.4, color="#1f4e89", label="OCEL(회의 단위)")
    for i, (r, c) in enumerate(zip(infl["부풀림 배수"], infl["케이스 중심 사건(법률안)"])):
        a.text(c, i + 0.2, f" ×{r:.1f}", va="center", fontsize=8)
    a.set_xlim(0, infl["케이스 중심 사건(법률안)"].max() * 1.12)
    a.set_yticks(list(y), infl.index)
    a.invert_yaxis()
    a.set_title("같은 회의의 사건을 하나로 세면")
    a.set_xlabel("사건 수")
    a.legend(fontsize=8, loc="lower right")
    b = axes[1]
    b.hist(span, bins=range(0, int(span.max()) + 31, 30), color="#5b8fc7")
    b.axvline(span.median(), color="#c0392b", ls="--", label=f"중앙값 {span.median():.0f}일")
    b.set_title(f"대안이 모은 원안들의 소관위 회부일 범위 (대안 {len(span):,}건)")
    b.set_xlabel("가장 늦은 회부일 - 가장 이른 회부일(일, 30일 간격)")
    b.set_ylabel("대안 수")
    b.legend()
    c = axes[2]
    names = ["국회법 모델", f"Inductive Miner {NOISE}"]
    for k, (metric, lab) in enumerate((("fitness", "적합도"), ("precision", "정밀도"))):
        vals = [i1[n][metric] for n in names]
        xs = [k + (j - 0.5) * 0.35 for j in range(2)]
        c.bar(xs, vals, width=0.35, color=["#c0392b", "#1f4e89"], label=names if k == 0 else None)
        for x, v in zip(xs, vals):
            c.text(x, v, f"{v:.3f}", ha="center", va="bottom", fontsize=9)
    c.set_xticks([0, 1], ["적합도(기록이 모델에 맞는 정도)", "정밀도(모델이 기록에 없는\n경로를 덜 허용하는 정도)"], fontsize=8)
    c.set_ylim(0, 1.25)
    c.set_title(f"규정 모델 대 자동으로 찾은 모델 (끝난 원안 {i1['cases']:,}건)")
    c.legend(["국회법 모델(1016_08)", f"Inductive Miner(잡음 {NOISE})"], fontsize=8, loc="upper right", ncol=2)
    fig.suptitle("객체 중심으로 다시 보기 (22대, OCEL)", fontsize=13)
    fig.text(0.01, 0.005, SOURCE, fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
