import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


METRICS = (
    ("Время ответа, мс", (("p50_ms", "p50"), ("p95_ms", "p95"))),
    ("Пропускная способность, запр./с", (("requests_per_second", "RPS"),)),
    ("Доля ошибок, %", (("failure_percent", "Ошибки"),)),
    ("CPU процесса, % одного ядра", (("mean_cpu_percent", "Среднее"), ("peak_cpu_percent", "Пик"))),
    ("Память процесса, МиБ RSS", (("mean_rss_mib", "Среднее"), ("peak_rss_mib", "Пик"))),
    ("Запросов за сценарий", (("requests", "Всего"),)),
)


def main():
    parser = argparse.ArgumentParser(description="Plot the measured DeanFlow Locust results.")
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.summary.read_text(encoding="utf-8"))
    scenarios = data["scenarios"]
    labels = [str(row["users"]) for row in scenarios]

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 6.4), layout="constrained")
    fig.patch.set_facecolor("#f3f5f9")
    mode = "с авторизацией" if data["authenticated"] else "базовый профиль"
    pool = f" · {data['sqlite_pool']}" if data.get("sqlite_pool") else ""
    fig.suptitle(f"DeanFlow · нагрузка Locust · {mode}{pool}", fontsize=18, color="#17233c", fontweight="bold")
    for ax, (title, series_specs) in zip(axes.flat, METRICS, strict=True):
        ax.set_facecolor("white")
        ax.set_title(title, loc="left", fontweight="bold", fontsize=10, color="#17233c", pad=10)
        for (key, series_label), color in zip(series_specs, ("#285cac", "#098774")):
            values = [row[key] for row in scenarios]
            ax.plot(labels, values, marker="o", linewidth=2, color=color, label=series_label)
        if len(series_specs) > 1:
            ax.legend(loc="lower right", frameon=False, fontsize=8)
        if series_specs[0][0] == "p50_ms" and min(values) > 0 and max(values) >= 10 * min(values):
            ax.set_yscale("symlog", linthresh=1)
            ax.set_title(f"{title} · лог. шкала", loc="left", fontweight="bold", fontsize=10, color="#17233c", pad=10)
        ax.set_ylim(bottom=0)
        if series_specs[0][0] == "failure_percent" and not any(values):
            ax.set_ylim(-0.002, 0.045)
            ax.set_yticks((0, 0.02, 0.04))
        ax.set_xlabel("Виртуальные пользователи", fontsize=8, color="#475570")
        ax.grid(axis="y", color="#e3e8f2")
        ax.tick_params(axis="both", labelsize=8, labelcolor="#475570", length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=150)


if __name__ == "__main__":
    main()
