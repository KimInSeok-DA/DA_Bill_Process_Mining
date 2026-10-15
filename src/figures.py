"""가설 H1～H6을 보여 주는 그림. 계산 정의는 analysis.py와 같은 함수를 쓴다(1015_04)

- fig4 H1·H4: 구간별 생존곡선(Kaplan-Meier) — 아직 다음 단계로 못 간 비율
- fig5 H2: 위원회 회의 버블 타임라인 — 점 하나가 (위원회, 날짜), 크기가 함께 처리한 의안 수
- fig6 H3: 진행 중 의안의 대기 단계별 건수와 경과일 분포
- fig7 H5: 위원회별 관찰 중앙값 → KM 중앙값(덤벨)과 소위 상정 도달 비율
- fig8 H6: 연×월 히트맵(끝 활동, 소관위 처리)

실행: .venv/Scripts/python src/figures.py  (analysis.py를 먼저 실행해 outputs/tables가 있어야 함)
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

import analysis as A

FIG = A.OUT / "figures"
sns.set_theme(style="whitegrid", font="Malgun Gothic", rc={"axes.unicode_minus": False})
BLUE, ORANGE, RED, GRAY = "#3b6ea5", "#e08e45", "#c0392b", "#8c8c8c"


def short(name: str) -> str:
    return name.replace("위원회", "위").replace("특별위", "특위")


def fig4_survival(log):
    first, censor = A.first_and_censor(log)
    pairs = [
        ("소위 회부", "소위 상정", RED, 3.2),
        ("소관위 회부", "소관위 상정", BLUE, 2.2),
        ("소위 상정", "소위 처리", ORANGE, 2.2),
        ("법사위 회부", "법사위 처리", "#2e8b57", 2.2),
        ("소위 처리", "소관위 처리", GRAY, 1.6),
    ]
    fig, ax = plt.subplots(figsize=(11, 6.5))
    s21 = None
    for a, b, c, lw in pairs:
        dur, ev = A.interval(first, censor, a, b)
        s = A.km_curve(dur, ev)
        s = pd.concat([pd.Series([1.0], index=[0]), s[s.index > 0]]) if 0 not in s.index else s
        med = A.km_median(dur, ev)
        if (a, b) == ("소위 회부", "소위 상정"):
            s21 = float(s[s.index <= 21].iloc[-1])
        obs = (first[b] - first[a]).dt.days
        obs = obs[obs >= 0].median()
        ax.step(s.index, s.values, where="post", color=c, lw=lw,
                label=f"{a} → {b}  (n={len(dur):,}, 도달 {ev.mean():.0%}) · KM 중앙 {med:.0f}일 / 관찰 중앙 {obs:.0f}일")
        if med is not None and med > 30:
            ax.plot([med, med], [0, 0.5], color=c, ls=":", lw=1.2)
            ax.annotate(f"{med:.0f}일", (med, 0.02), color=c, fontsize=10, ha="center", fontweight="bold")
    ax.axhline(0.5, color="black", lw=0.8, ls="--")
    ax.text(805, 0.51, "절반이 넘어간 시점 = KM 중앙값", fontsize=9, ha="right", va="bottom")
    ax.annotate("관찰 중앙값 21일은\n상정까지 간 44%만 본 값",
                xy=(21, s21), xytext=(140, 0.9), fontsize=9, color=RED,
                arrowprops=dict(arrowstyle="->", color=RED))
    ax.set_xlim(0, 810)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("앞 단계 뒤 경과일")
    ax.set_ylabel("아직 다음 단계로 못 간 비율")
    ax.set_title("H1·H4  소위 상정을 기다리는 시간이 가장 길다 — 법사위는 짧다\n"
                 "구간별 생존곡선(Kaplan-Meier, 진행 중 의안은 조회일에서 관찰 중단)", fontsize=13)
    ax.legend(loc="upper right", fontsize=8.5, frameon=True)
    fig.text(0.01, 0.005, A.SOURCE, fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "fig4_h1_survival.png", dpi=150)
    plt.close(fig)


def fig5_meetings(log):
    cmt = log[log["activity"] == "소관위 처리"]
    m = cmt.groupby(["committee_n", "date"]).agg(n=("case_id", "size"),
                                                 alt=("stage_result", lambda s: s.isin(A.ALT).mean())).reset_index()
    m["ts"] = pd.to_datetime(m["date"])
    order = m.groupby("committee_n")["n"].sum().sort_values().index
    m["y"] = m["committee_n"].map({c: i for i, c in enumerate(order)})
    fig, ax = plt.subplots(figsize=(12, 7.5))
    sc = ax.scatter(m["ts"], m["y"], s=m["n"] * 6, c=m["alt"], cmap="Oranges", vmin=0, vmax=1,
                    edgecolor="#555", linewidth=0.4, alpha=0.85)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([f"{short(c)} ({int(m.loc[m['committee_n'] == c, 'n'].sum()):,})" for c in order], fontsize=9)
    for n in (5, 20, 50, 100):
        ax.scatter([], [], s=n * 6, color="white", edgecolor="#555", label=f"{n}건")
    ax.legend(title="그날 함께 처리한 의안", loc="lower left", fontsize=8, title_fontsize=8, labelspacing=1.3,
              borderpad=1, frameon=True)
    cb = fig.colorbar(sc, ax=ax, pad=0.01, shrink=0.6)
    cb.set_label("그중 대안반영폐기 비율")
    share = (cmt.groupby(["committee_n", "date"])["case_id"].transform("size") >= 2).mean()
    ax.set_title(f"H2  위원회는 의안을 하나씩이 아니라 묶어서 처리한다\n"
                 f"점 하나 = 위원회 회의 하루({len(m)}번) · 소관위 처리 {len(cmt):,}건 중 {share:.1%}가 2건 이상 묶음",
                 fontsize=13)
    ax.set_xlabel("소관위 처리일")
    fig.text(0.01, 0.005, A.SOURCE + " · 위원회 개편 전후 이름 묶음, 괄호는 처리 의안 수", fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "fig5_h2_meetings.png", dpi=150)
    plt.close(fig)


def fig6_backlog(log):
    _, last = A.backlog(log)
    keep = ["소위 상정 대기", "소위 처리 대기", "소관위 상정 대기", "소위 회부 대기", "대안 본회의 대기"]
    last = last.copy()
    last["단계"] = last["state"].where(last["state"].isin(keep), "그 밖")
    order = keep + ["그 밖"]
    cnt = last["단계"].value_counts().reindex(order)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.5), sharey=True, gridspec_kw={"width_ratios": [1, 1.6]})
    colors = [RED if s == "소위 상정 대기" else BLUE for s in order]
    a1.barh(order, cnt.values, color=colors)
    for i, v in enumerate(cnt.values):
        a1.text(v + 150, i, f"{v:,}건 ({v / cnt.sum():.1%})", va="center", fontsize=9)
    a1.set_xlim(0, cnt.max() * 1.45)
    a1.invert_yaxis()
    a1.set_xlabel("진행 중 의안 수")
    a1.set_title("지금 기다리는 단계")
    sns.boxplot(data=last, y="단계", x="elapsed", order=order, ax=a2, orient="h", showfliers=False,
                palette=dict(zip(order, colors)), width=0.55, hue="단계", hue_order=order, legend=False)
    a2.axvline(365, color="black", ls="--", lw=0.8)
    a2.text(368, 5.4, "1년", fontsize=9)
    a2.set_xlabel("마지막 단계 뒤 경과일(조회일 기준, 이상값 제외 상자그림)")
    a2.set_ylabel("")
    a2.set_title("기다린 기간")
    fig.suptitle(f"H3  진행 중 의안 {cnt.sum():,}건의 3분의 2가 소위 상정을 기다린다", fontsize=13)
    fig.text(0.01, 0.005, A.SOURCE, fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(FIG / "fig6_h3_backlog.png", dpi=150)
    plt.close(fig)


def fig7_committee():
    cm = pd.read_csv(A.OUT / "tables" / "h5_committee.csv", index_col=0)
    cm = cm[cm["소위상정"] >= 30].copy()
    cap = 800
    cm["km_plot"] = cm["KM중앙값"].fillna(cap)
    cm = cm.sort_values(["km_plot", "관찰중앙값"])
    y = np.arange(len(cm))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 7), sharey=True, gridspec_kw={"width_ratios": [2.2, 1]})
    for i, (o, k, und) in enumerate(zip(cm["관찰중앙값"], cm["km_plot"], cm["KM중앙값"].isna())):
        a1.plot([o, k], [i, i], color="#bbb", lw=2, zorder=1)
        if und:
            a1.annotate("", xy=(cap + 25, i), xytext=(cap - 5, i), arrowprops=dict(arrowstyle="->", color=RED))
    a1.scatter(cm["관찰중앙값"], y, color=BLUE, s=55, zorder=2, label="관찰 중앙값(상정까지 간 의안만)")
    a1.scatter(cm["km_plot"], y, color=RED, marker="D", s=50, zorder=2, label="KM 중앙값(기다리는 의안 포함)")
    a1.axvspan(cap - 10, cap + 40, color="#fbe3e0", zorder=0, label="→ KM 중앙값 없음(절반 넘게 아직 상정 전)")
    a1.set_yticks(y)
    a1.set_yticklabels([short(c) for c in cm.index])
    a1.set_xlim(-10, cap + 45)
    a1.set_xlabel("소위 회부 → 소위 상정(일)")
    a1.legend(loc="lower right", fontsize=8.5, frameon=True)
    a1.set_title("관찰값만 보면 순서가 뒤집힌다")
    a2.barh(y, cm["상정 도달 비율"], color=ORANGE)
    for i, v in enumerate(cm["상정 도달 비율"]):
        a2.text(v + 0.01, i, f"{v:.0%}", va="center", fontsize=8.5)
    a2.set_xlim(0, 1.1)
    a2.set_xlabel("소위 회부 의안 중 상정까지 간 비율")
    a2.set_title("상정 도달 비율")
    fig.suptitle("H5  소위 상정까지 걸리는 시간은 위원회마다 크게 다르다(상정 30건 이상 16개 상임위)", fontsize=13)
    fig.text(0.01, 0.005, A.SOURCE + " · 위원회 개편 전후 이름 묶음 · 순위 평가가 아니라 차이의 크기를 보이는 그림",
             fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(FIG / "fig7_h5_committee.png", dpi=150)
    plt.close(fig)


def fig8_calendar():
    mt = pd.read_csv(A.OUT / "tables" / "h6_monthly.csv", index_col=0)
    mt.index = pd.PeriodIndex(mt.index, freq="M")
    fig, axes = plt.subplots(2, 1, figsize=(12, 6.5))
    for ax, col, cmap in zip(axes, ["끝 활동", "소관위 처리"], ["Oranges", "Blues"]):
        grid = pd.DataFrame(np.nan, index=sorted({p.year for p in mt.index}), columns=range(1, 13))
        for p, v in mt[col].items():
            grid.loc[p.year, p.month] = v
        # 관찰 기간 밖(2024-05 이전, 2026-10 이후)은 빈칸
        grid.loc[2024, [1, 2, 3, 4]] = np.nan
        sns.heatmap(grid, ax=ax, cmap=cmap, annot=True, fmt=".0f", linewidths=0.5, linecolor="white",
                    cbar_kws={"label": "건수"}, annot_kws={"fontsize": 9})
        ax.grid(False)
        ax.set_xticklabels([f"{m}월" for m in range(1, 13)], rotation=0)
        ax.set_yticklabels([f"{y}년" for y in grid.index], rotation=0)
        ax.set_xlabel("")
        top3 = mt[col].sort_values(ascending=False).head(3)
        ax.set_title(f"{col} — 상위 3개월 {top3.sum() / mt[col].sum():.1%} (고르게 퍼지면 {3 / len(mt):.0%})", fontsize=11)
    fig.suptitle("H6  처리는 달마다 편차가 크지만, 미리 정한 기준(상위 3개월 30%)에는 못 미친다", fontsize=13)
    fig.text(0.01, 0.005, A.SOURCE + " · 끝 활동: 본회의 처리·대안 본회의 통과·철회 등", fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(FIG / "fig8_h6_calendar.png", dpi=150)
    plt.close(fig)


def main() -> None:
    log = A.load()
    fig4_survival(log)
    fig5_meetings(log)
    fig6_backlog(log)
    fig7_committee()
    fig8_calendar()
    print("저장:", ", ".join(sorted(p.name for p in FIG.glob("fig[4-8]*.png"))))


if __name__ == "__main__":
    main()
