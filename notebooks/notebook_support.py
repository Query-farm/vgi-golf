"""Presentation helpers for the golf scorecard accounting example."""

from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display


def theme() -> None:
    """Apply readable shared chart settings."""
    sns.set_theme(style="whitegrid", context="notebook", font="DejaVu Sans")
    plt.rcParams.update(
        {"figure.dpi": 130, "text.parse_math": False, "axes.spines.top": False, "axes.spines.right": False}
    )


def prose(text: str) -> None:
    """Display result-derived Markdown."""
    display(Markdown(text))


def finish(fig: plt.Figure, title: str, subtitle: str, filename: str, mode: str) -> None:
    """Add source context and save the chart for review."""
    fig.suptitle(title, x=0.07, y=0.98, ha="left", fontsize=16, weight="bold", color="#253d31")
    fig.text(0.07, 0.925, subtitle, fontsize=10.5, color="#52675a")
    fig.text(
        0.07,
        0.025,
        f"Source: ESPN Masters 2026 · {mode} · real vgi-golf v0.2.0 through Haybarn / VGI\n"
        "One player’s regulation scorecard · negative = under par · no field-difficulty inference",
        fontsize=9,
        color="#52675a",
    )
    directory = Path("validation/charts")
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / filename, dpi=150)
    plt.show()
