"""비전공자용 요약 그림 3장(포트폴리오·README 첫머리). 숫자는 모두 결과 파일에서 읽는다.

- p1_headline.png: 끝난 것만 보면 21일, 기다리는 법률안까지 넣으면 516일. 21대 정답 실험 결과를 옆에
- p2_where_now.png: 22대 원안 20,419건은 지금 어디에 있나(끝난 것과 처리 중인 것)
- p3_how.png: 수집 → 이벤트 로그 → 분석 → 검증 흐름과 검증 숫자

실행: .venv/Scripts/python src/portfolio_figures.py (analysis.py·age21.py·cross_check.py 뒤)
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

import analysis as A

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
FIG = OUT / "portfolio"
SCREEN_CHECKS = 30  # 의안정보시스템 화면 대조 합계(22대 27 · 21대 3), 결정 문서별 건수의 합(1015_01·03·06, 1016_09·10·12, 1017_13)

plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False})
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#c9c8c3"


def bare(ax) -> None:
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def hbars(ax, labels, values, colors, unit, xmax) -> None:
    y = range(len(values))[::-1]
    ax.barh(list(y), values, color=colors, height=0.56, edgecolor="white", linewidth=2)
    for yi, v in zip(y, values):
        ax.text(v + xmax * 0.015, yi, f"{v:,.0f}{unit}", va="center", fontsize=13, color=INK, fontweight="bold")
    ax.set_yticks(list(y), labels, fontsize=11.5, color=INK)
    ax.set_xlim(0, xmax)
    bare(ax)


def p1() -> None:
    res = json.loads((OUT / "analysis_results.json").read_text(encoding="utf-8"))
    waits = pd.read_csv(OUT / "tables" / "h1_waits.csv").set_index("구간")
    a21 = json.loads((OUT / "age21" / "age21_results.json").read_text(encoding="utf-8"))
    seg = "소위 회부 → 소위 상정"
    obs, km, reach = waits.loc[seg, "중앙값(일)"], res["H1"]["km_median"][seg], waits.loc[seg, "관찰 건수"] / waits.loc[seg, "A 있는 의안"]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5.8), gridspec_kw={"width_ratios": [1, 1.15], "wspace": 0.55})
    hbars(a1, ["끝난 법률안만 보면", "기다리는 법률안까지\n넣으면"], [obs, km], [GRAY, ORANGE], "일", 640)
    a1.set_title("22대: 소위원회로 넘어간 뒤 안건에 오르기까지", fontsize=13, color=INK, loc="left", pad=12)
    a1.text(0, -0.17, f"소위로 넘어간 법률안 중 안건에 오른 것은 {reach:.0%}뿐이다.\n"
                      "\"21일\"은 빨리 오른 일부만 보고 낸 값이다.", fontsize=10.5, color=INK2, va="top", transform=a1.transAxes)

    hbars(a2, ["끝난 법률안만 보면", "기다리는 법률안까지\n넣으면", "정답(임기 끝까지 본 값)"],
          [a21["T1"]["observed_at_T"], a21["T2"]["km_at_T"], a21["T1"]["truth"]], [GRAY, ORANGE, BLUE], "일", 640)
    a2.set_title(f"확인: 임기가 끝난 21대를 같은 시점({a21['days']}일)에서 잘라 보면", fontsize=13, color=INK, loc="left", pad=12)
    a2.text(0, -0.17, "끝난 것만 보는 방법은 정답의 5%에 그쳤고,\n기다리는 법률안까지 넣은 값이 훨씬 가까웠다.\n"
                      "다만 정답보다 41% 길어, 516일은 폭이 있는 추정값이다.", fontsize=10.5, color=INK2, va="top", transform=a2.transAxes)

    fig.suptitle("법률안은 어디서 오래 멈추나: 끝난 것만 세면 병목이 보이지 않는다", fontsize=16, color=INK, x=0.06, ha="left", y=0.99)
    fig.text(0.06, 0.005, A.SOURCE, fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.16, right=0.95, top=0.82, bottom=0.3)
    fig.savefig(FIG / "p1_headline.png", dpi=150)
    plt.close(fig)


def p2() -> None:
    backlog = pd.read_csv(OUT / "tables" / "h3_backlog_state.csv").set_index("state")["건수"]
    log = A.load()
    done = log[(log["case_group"] == "원안") & (log["case_status"] == "완료")].drop_duplicates("case_id")
    end = done["final_result"].replace({"원안가결": "가결", "수정가결": "가결"}).value_counts()
    top = ["소위 상정 대기", "소위 처리 대기", "소관위 상정 대기"]
    rows = [("처리 중", "소위원회 안건에 오르기를 기다림", backlog[top[0]]),
            ("처리 중", "소위원회 심사 중", backlog[top[1]]),
            ("처리 중", "위원회 안건에 오르기를 기다림", backlog[top[2]]),
            ("처리 중", "그 밖의 단계", backlog.drop(top).sum()),
            ("끝남", "대안에 반영돼 폐기(대안반영폐기)", end["대안반영폐기"]),
            ("끝남", "가결", end["가결"]),
            ("끝남", "철회·폐기 등", end.drop(["대안반영폐기", "가결"]).sum())]
    n_prog, n_done = backlog.sum(), len(done)

    fig, ax = plt.subplots(figsize=(12, 5.6))
    colors = [ORANGE if i == 0 else (BLUE if r[0] == "처리 중" else GRAY) for i, r in enumerate(rows)]
    hbars(ax, [r[1] for r in rows], [r[2] for r in rows], colors, "건", 11500)
    ax.axhline(2.5, color=INK2, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(11500, 6.45, f"처리 중 {n_prog:,}건", ha="right", fontsize=11, color=INK2)
    ax.text(11500, 2.2, f"끝남 {n_done:,}건", ha="right", fontsize=11, color=INK2)
    fig.suptitle(f"22대 국회 법률안 {n_prog + n_done:,}건은 지금 어디에 있나 (의원·정부 제출, 2026-10-15)",
                 fontsize=15, color=INK, x=0.03, ha="left")
    ax.set_title(f"처리 중인 법률안의 {backlog[top[0]] / n_prog:.0%}가 소위원회 안건에 오르기를 기다린다. "
                 f"끝난 법률안의 {end['대안반영폐기'] / n_done:.0%}는 위원회가 만든 대안에 합쳐졌다",
                 fontsize=11, color=INK2, loc="left", pad=10)
    fig.text(0.03, 0.005, A.SOURCE, fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.28, right=0.97, top=0.84, bottom=0.08)
    fig.savefig(FIG / "p2_where_now.png", dpi=150)
    plt.close(fig)


def p3() -> None:
    br = json.loads((ROOT / "data" / "processed" / "build_report.json").read_text(encoding="utf-8"))
    n_cross = len(pd.read_csv(OUT / "cross_check.csv"))
    a21 = json.loads((OUT / "age21" / "age21_results.json").read_text(encoding="utf-8"))
    steps = [
        ("1  수집", ["열린국회정보 Open API 4종", "원본을 그대로 저장해", "같은 데이터는 다시 부르지 않음"]),
        ("2  이벤트 로그", [f"법률안 {br['cases']:,}건", f"단계 18가지 · {br['events']:,}줄", "처리 중인 법률안도 포함"]),
        ("3  분석", ["프로세스 마이닝(pm4py)", "처리 흐름 · 단계별 기다림", "생존분석(기다리는 건 포함)"]),
        ("4  검증", [f"화면 대조 {SCREEN_CHECKS}건", f"SQL로 다시 계산 {n_cross}개 일치", f"21대 정답 실험({a21['days']}일)"]),
    ]
    checks = ["날짜 칸의 뜻은 이름만 보지 않고 의안정보시스템 화면과 맞춰 봄",
              "규칙으로 뺀 기록은 이유와 함께 남기고, 처리 전후 건수를 단계마다 대조",
              "가설과 판정 기준을 결과를 보기 전에 적어 둠"]

    fig, ax = plt.subplots(figsize=(13, 3.9))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 4.1)
    ax.axis("off")
    w, h, gap, y0 = 2.75, 2.1, 0.5, 1.75
    for i, (title, lines) in enumerate(steps):
        x = 0.25 + i * (w + gap)
        last = i == len(steps) - 1
        ax.add_patch(FancyBboxPatch((x, y0), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor="#fdf0ea" if last else "#eef4fc", edgecolor=ORANGE if last else BLUE, linewidth=1.6))
        ax.text(x + 0.18, y0 + h - 0.3, title, fontsize=13.5, fontweight="bold", color=INK, va="top")
        for j, line in enumerate(lines):
            ax.text(x + 0.18, y0 + h - 0.85 - j * 0.38, line, fontsize=10.5, color=INK2, va="top")
        if not last:
            ax.annotate("", xy=(x + w + gap - 0.06, y0 + h / 2), xytext=(x + w + 0.06, y0 + h / 2),
                        arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.4))
    for j, c in enumerate(checks):
        ax.text(0.3, 1.25 - j * 0.38, "·  " + c, fontsize=10.5, color=INK2, va="top")
    fig.suptitle("어떻게 만들고 확인했나: 이벤트 로그를 직접 만들고, 다른 방법으로 다시 맞춰 봄",
                 fontsize=15, color=INK, x=0.03, ha="left", y=0.98)
    fig.text(0.03, 0.01, A.SOURCE, fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.88, bottom=0.04)
    fig.savefig(FIG / "p3_how.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    p1()
    p2()
    p3()
    print("outputs/portfolio/ 3장")


if __name__ == "__main__":
    main()
