"""적합성 검사: 국회법 표준 절차 모델과 실제 경로를 맞춰 본다. 정의·가설: docs/의사결정/1016_08_적합성검사.md

- 모델: 상태 전이도(아래 TRANS). 상태 전이도는 페트리넷이라 pm4py 정렬(alignment)에 그대로 쓴다
- 끝난 원안: pm4py 정렬 → 경로별 적합도, 이탈(로그 이동·모델 이동)
- 처리 중인 원안: 상태 전이도를 따라가며 처음으로 허용되지 않은 활동을 찾는다
- 이탈한 경로에는 예외 유형을 붙인다(EXCEPTION_ORDER)
- 출력: outputs/tables/conf_*.csv, outputs/figures/fig11_conformance.png, outputs/conformance_results.json

실행: .venv/Scripts/python src/conformance.py
"""

import json
import warnings
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

warnings.filterwarnings("ignore")
from pm4py.algo.conformance.alignments.petri_net import algorithm as alignments  # noqa: E402
from pm4py.objects.log.obj import Event, EventLog, Trace  # noqa: E402
from pm4py.objects.petri_net.obj import Marking, PetriNet  # noqa: E402
from pm4py.objects.petri_net.utils import petri_utils  # noqa: E402

import analysis as A

# 표준 절차 모델: (지금 상태, 활동, 다음 상태)
TRANS = [
    ("시작", "제안", "제안됨"),
    ("제안됨", "소관위 회부", "회부됨"),
    ("회부됨", "소관위 상정", "상정됨"),
    ("상정됨", "소위 회부", "소위 회부됨"),
    ("소위 회부됨", "소위 상정", "소위 심사 중"),
    ("소위 심사 중", "소위 상정", "소위 심사 중"),
    ("소위 심사 중", "소위 처리", "소위 처리됨"),
    ("소위 처리됨", "소관위 처리", "소관위 처리됨"),
    ("소관위 처리됨", "법사위 회부", "법사위 회부됨"),
    ("소관위 처리됨", "대안반영폐기", "끝"),
    ("소관위 처리됨", "수정안반영폐기", "끝"),
    ("소관위 처리됨", "폐기", "끝"),
    ("법사위 회부됨", "법사위 상정", "법사위 상정됨"),
    ("법사위 상정됨", "법사위 처리", "법사위 처리됨"),
    ("법사위 처리됨", "가결", "끝"),
    ("법사위 처리됨", "부결", "끝"),
    ("법사위 처리됨", "폐기", "끝"),
]
STATES = sorted({s for s, _, _ in TRANS} | {d for _, _, d in TRANS})
TRANS += [(s, "철회", "끝") for s in STATES if s not in ("시작", "끝")]  # 제안 뒤 언제든 철회 가능
STEP = {(s, a): d for s, a, d in TRANS}

LEGAL = ["재회부", "소위 직접 회부", "신속처리", "예산부수 자동부의", "법사위 소관"]
EXCEPTION_ORDER = LEGAL + ["소위 생략", "그 밖"]


def build_net():
    net = PetriNet("국회법 표준 절차")
    places = {s: PetriNet.Place(s) for s in STATES}
    for p in places.values():
        net.places.add(p)
    for i, (s, a, d) in enumerate(TRANS):
        t = PetriNet.Transition(f"t{i}", a)
        net.transitions.add(t)
        petri_utils.add_arc_from_to(places[s], t, net)
        petri_utils.add_arc_from_to(t, places[d], net)
    return net, Marking({places["시작"]: 1}), Marking({places["끝"]: 1})


def walk(trace: tuple) -> int | None:
    """처음으로 모델이 허용하지 않는 활동의 위치. 끝까지 허용되면 None."""
    state = "시작"
    for i, a in enumerate(trace):
        nxt = STEP.get((state, a))
        if nxt is None:
            return i
        state = nxt
    return None


def classify(g: pd.DataFrame) -> str:
    """이탈한 의안 1건에 예외 유형을 붙인다(1016_08 4절의 순서대로 첫 번째로 맞는 것)."""
    acts = list(g["activity"])
    pos = {a: [i for i, x in enumerate(acts) if x == a] for a in set(acts)}
    first = lambda a: pos[a][0] if a in pos else None  # noqa: E731
    sub_ref = g[g["activity"] == "소위 회부"]
    if "소관위 재회부" in pos or (len(sub_ref) >= 2 and sub_ref["committee"].nunique() >= 2):
        return "재회부"
    s_ref, c_pre = first("소위 회부"), first("소관위 상정")
    if (s_ref is not None and (c_pre is None or s_ref < c_pre)) or (g.loc[g["activity"] == "소관위 상정", "step_order"] > 6).any():
        return "소위 직접 회부"
    l_ref, c_proc = first("법사위 회부"), first("소관위 처리")
    if l_ref is not None and (c_proc is None or l_ref < c_proc):
        return "신속처리"
    if "가결" in pos and c_proc is None:
        return "예산부수 자동부의"
    if ({"가결", "부결"} & pos.keys()) and "법사위 회부" not in pos and g["case_committee"].iloc[0] == "법제사법위원회":
        return "법사위 소관"
    if c_proc is not None and not ({"소위 회부", "소위 상정", "소위 처리"} & pos.keys()):
        return "소위 생략"
    return "그 밖"


def main() -> None:
    full = A.load()
    log = full[full["case_group"] == "원안"].copy()
    traces = log.groupby("case_id", sort=False)["activity"].agg(tuple)
    status = log.drop_duplicates("case_id").set_index("case_id")["case_status"]
    groups = dict(tuple(log.groupby("case_id", sort=False)))

    # 끝난 원안: 변형(경로)별로 한 번씩 정렬
    done = traces[status.reindex(traces.index) == "완료"]
    variants = done.value_counts()
    elog = EventLog([Trace([Event({"concept:name": a}) for a in v]) for v in variants.index])
    net, im, fm = build_net()
    aligned = alignments.apply_log(elog, net, im, fm)
    var_rows = []
    for v, n, al in zip(variants.index, variants.values, aligned):
        moves = [(lm, mm) for lm, mm in al["alignment"]]
        var_rows.append({"경로": " > ".join(v), "의안 수": int(n), "적합도": round(al["fitness"], 4),
                         "로그 이동(모델에 없는 활동)": "; ".join(lm for lm, mm in moves if mm == ">>"),
                         "모델 이동(실제에 없는 단계)": "; ".join(mm for lm, mm in moves if lm == ">>")})
    vt = pd.DataFrame(var_rows)
    fit_of = dict(zip(variants.index, vt["적합도"]))
    done_df = pd.DataFrame({"case_id": done.index, "trace": done.values})
    done_df["fitness"] = done_df["trace"].map(fit_of)
    done_df["walk_fit"] = done_df["trace"].map(lambda t: walk(t) is None)
    done_df["유형"] = [("표준 절차" if f == 1 else classify(groups[c])) for c, f in zip(done_df["case_id"], done_df["fitness"])]

    # 처리 중인 원안: 지금까지 허용되지 않은 활동이 있는지
    prog = traces[status.reindex(traces.index) == "진행중"]
    prog_df = pd.DataFrame({"case_id": prog.index, "trace": prog.values})
    dev_at = [walk(t) for t in prog_df["trace"]]  # 정수 또는 None(pandas 열로 두면 실수가 됨)
    prog_df["dev_at"] = dev_at
    prog_df["유형"] = [("지금까지 표준 절차" if d is None else classify(groups[c]))
                      for c, d in zip(prog_df["case_id"], dev_at)]
    prog_df["이탈 지점"] = [None if d is None else f"{t[d - 1] if d else '시작'} → {t[d]}"
                         for t, d in zip(prog_df["trace"], dev_at)]

    out = A.OUT
    vt.sort_values("의안 수", ascending=False).to_csv(out / "tables" / "conf_variants_completed.csv", index=False, encoding="utf-8-sig")
    tab = pd.DataFrame({
        "끝난 원안": done_df["유형"].value_counts(),
        "처리 중인 원안": prog_df["유형"].value_counts(),
    }).fillna(0).astype(int)
    order = ["표준 절차", "지금까지 표준 절차"] + EXCEPTION_ORDER
    tab = tab.reindex([o for o in order if o in tab.index])
    tab.to_csv(out / "tables" / "conf_exception_types.csv", encoding="utf-8-sig")
    other = pd.concat([
        done_df[done_df["유형"] == "그 밖"].assign(구분="끝난 원안"),
        prog_df[prog_df["유형"] == "그 밖"].assign(구분="처리 중인 원안"),
    ])
    other["경로"] = other["trace"].map(" > ".join)
    other.groupby(["구분", "경로"]).size().rename("의안 수").sort_values(ascending=False).head(30) \
        .to_csv(out / "tables" / "conf_other_paths.csv", encoding="utf-8-sig")
    pts = prog_df["이탈 지점"].value_counts().head(15)
    pts.rename("의안 수").to_csv(out / "tables" / "conf_inprogress_deviation_points.csv", encoding="utf-8-sig")

    n_done, n_fit = len(done_df), int((done_df["fitness"] == 1).sum())
    dev = done_df[done_df["fitness"] < 1]
    legal = int(dev["유형"].isin(LEGAL).sum())
    res = {
        "model": {"states": len(STATES), "transitions": len(TRANS)},
        "completed": {"cases": n_done, "variants": len(variants), "fully_fit": n_fit,
                      "fully_fit_share": round(n_fit / n_done, 4),
                      "mean_fitness": round(float(done_df["fitness"].mean()), 4),
                      "walk_agrees_with_alignment": bool((done_df["walk_fit"] == (done_df["fitness"] == 1)).all()),
                      "deviating": len(dev), "deviating_legal": legal,
                      "deviating_legal_share": round(legal / len(dev), 4) if len(dev) else None,
                      "types": {k: int(v) for k, v in done_df["유형"].value_counts().items()}},
        "in_progress": {"cases": len(prog_df), "compliant_so_far": int(prog_df["dev_at"].isna().sum()),
                        "compliant_share": round(float(prog_df["dev_at"].isna().mean()), 4),
                        "types": {k: int(v) for k, v in prog_df["유형"].value_counts().items()},
                        "top_deviation_points": {k: int(v) for k, v in pts.head(5).items()}},
        "C1": {"criterion": "완전 적합 90% 이상", "support": n_fit / n_done >= 0.9},
        "C2": {"criterion": "이탈 중 법정 예외(유형 1～5) 80% 이상",
               "support": bool(len(dev)) and legal / len(dev) >= 0.8},
    }
    (out / "conformance_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    draw(tab, res, out / "figures" / "fig11_conformance.png")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(tab.to_string())


def draw(tab: pd.DataFrame, res: dict, path) -> None:
    colors = {"표준 절차": "#3b6ea5", "지금까지 표준 절차": "#3b6ea5", "재회부": "#e08e45", "소위 직접 회부": "#f0b27a",
              "신속처리": "#c39bd3", "예산부수 자동부의": "#76b7b2", "법사위 소관": "#9fd3c7",
              "소위 생략": "#c0392b", "그 밖": "#8c8c8c"}
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for ax, col in zip(axes, ["끝난 원안", "처리 중인 원안"]):
        s = tab[col][tab[col] > 0].sort_values()
        ax.barh(s.index, s.values, color=[colors[i] for i in s.index])
        for i, v in enumerate(s.values):
            ax.text(v, i, f" {v:,}건 ({v / s.sum():.1%})", va="center", fontsize=9)
        ax.set_xlim(0, s.max() * 1.4)
        ax.set_xlabel("법률안 수")
    c, p = res["completed"], res["in_progress"]
    axes[0].set_title(f"끝난 법률안 {c['cases']:,}건: 표준 절차를 그대로 따른 비율 {c['fully_fit_share']:.1%}")
    axes[1].set_title(f"처리 중인 법률안 {p['cases']:,}건: 지금까지 표준 절차 {p['compliant_share']:.1%}")
    fig.suptitle("적합성 검사: 국회법 표준 절차를 얼마나 따르고, 벗어나면 어떤 경우인가(의원·정부 법률안)", fontsize=13)
    fig.text(0.01, 0.005, A.SOURCE + " · 파랑 = 표준 절차, 주황·보라·청록 = 법이 허용한 예외, 빨강·회색 = 그 밖",
             fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
