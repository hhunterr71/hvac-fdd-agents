"""Altair charts for the dashboard. Colors are the dataviz reference palette, per theme.

Color jobs: one blue for "this unit" (identity), neutral gray for its siblings (context),
a single-hue blue ramp for fault hours (magnitude). Status is never a chart color; it is
shown as icon + label in tables (common.py).
"""

from dataclasses import dataclass

import altair as alt
import pandas as pd
import streamlit as st


@dataclass(frozen=True)
class Tokens:
    focus: str
    context: str
    text: str
    text2: str
    muted: str
    grid: str
    axis: str
    ramp: tuple


LIGHT = Tokens(focus="#2a78d6", context="#898781", text="#0b0b0b", text2="#52514e", muted="#898781",
               grid="#e1e0d9", axis="#c3c2b7", ramp=("#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"))
DARK = Tokens(focus="#3987e5", context="#898781", text="#f0efec", text2="#c3c2b7", muted="#898781",
              grid="#2c2c2a", axis="#383835", ramp=("#184f95", "#256abf", "#3987e5", "#86b6ef", "#cde2fb"))
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def tokens() -> Tokens:
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except AttributeError:
        return LIGHT


def show(chart: alt.TopLevelMixin) -> None:
    """Apply the shared chrome (recessive axes, transparent surface) and render."""
    tk = tokens()
    chart = (chart.configure(background="transparent", font=FONT)
             .configure_view(stroke=None)
             .configure_axis(gridColor=tk.grid, domainColor=tk.axis, tickColor=tk.axis, labelColor=tk.muted,
                             titleColor=tk.text2, labelFontSize=11, titleFontSize=11, titleFontWeight="normal")
             .configure_legend(labelColor=tk.text2, titleColor=tk.text2, labelFontSize=11, titleFontSize=11,
                               titleFontWeight="normal", orient="top")
             .configure_text(color=tk.text2))
    st.altair_chart(chart, theme=None, width="stretch")


def fault_heatmap(daily: pd.DataFrame, units: list[str]) -> alt.Chart:
    """Units x days, shaded by fault hours above siblings. daily: unit, day, hours, rules."""
    tk = tokens()
    d = daily[daily.hours > 0].assign(day_end=lambda x: x.day + pd.Timedelta(days=1))
    return alt.Chart(d).mark_rect(stroke=None, cornerRadius=2).encode(
        x=alt.X("day:T", title=None, axis=alt.Axis(format="%b %d", labelOverlap=True, grid=False)),
        x2="day_end:T",
        y=alt.Y("unit:N", sort=units, title=None, scale=alt.Scale(domain=units, paddingInner=0.15),
                axis=alt.Axis(labelColor=tk.text2, labelFontSize=12)),
        color=alt.Color("hours:Q", title="Worst rule's hours per day above siblings",
                        scale=alt.Scale(range=list(tk.ramp), domain=[0, 24], clamp=True),
                        legend=alt.Legend(gradientLength=200, gradientThickness=8, titleLimit=400)),
        tooltip=[alt.Tooltip("unit:N", title="Unit"), alt.Tooltip("day:T", title="Day", format="%a %b %d"),
                 alt.Tooltip("hours:Q", title="Worst rule, h above siblings", format=".1f"),
                 alt.Tooltip("rules:N", title="All rules")],
    ).properties(height=max(120, 34 * len(units)))


def ranking_bars(ranking: list[list]) -> alt.Chart:
    """Signature model probabilities, most likely first; 'none' (noise) in gray."""
    tk = tokens()
    df = pd.DataFrame(ranking, columns=["fault", "p"])
    df["label"] = df.fault.map(lambda f: "none (noise)" if f == "none" else f.replace("_", " "))
    df["kind"] = df.fault.map(lambda f: "noise" if f == "none" else "fault")
    base = alt.Chart(df).encode(
        y=alt.Y("label:N", sort=None, title=None, axis=alt.Axis(labelColor=tk.text2, labelFontSize=12, labelLimit=220)),
        x=alt.X("p:Q", title=None, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", tickCount=5)),
        tooltip=[alt.Tooltip("label:N", title="Fault"), alt.Tooltip("p:Q", title="Probability", format=".0%")])
    bars = base.mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, height=14).encode(
        color=alt.Color("kind:N", scale=alt.Scale(domain=["fault", "noise"], range=[tk.focus, tk.context]),
                        legend=None))
    labels = base.mark_text(align="left", dx=6, fontSize=11).encode(text=alt.Text("p:Q", format=".0%"))
    return (bars + labels).properties(height=34 * len(df))


def compare_lines(df: pd.DataFrame, focus: str, label: str) -> alt.LayerChart:
    """One point on this unit (blue) vs. the same point on each sibling (gray). df: t, series, value."""
    tk = tokens()
    df = df.assign(group=df.series.map(lambda s: f"{focus} (this unit)" if s == focus else "siblings"))
    domain = [f"{focus} (this unit)", "siblings"]
    hover = alt.selection_point(fields=["t"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    span = pd.to_datetime(df.t).max() - pd.to_datetime(df.t).min() if len(df) else pd.Timedelta(0)
    fmt = "%b %d" if span > pd.Timedelta(days=2) else "%b %d %H:%M"
    color = alt.Color("group:N", scale=alt.Scale(domain=domain, range=[tk.focus, tk.context]), title=None)
    base = alt.Chart(df).encode(x=alt.X("t:T", title=None, axis=alt.Axis(format=fmt, labelOverlap=True)))
    lines = base.mark_line(interpolate="monotone").encode(
        y=alt.Y("value:Q", title=label, scale=alt.Scale(zero=False)),
        color=color,
        strokeWidth=alt.condition(alt.datum.group == domain[0], alt.value(2), alt.value(1.25)),
        detail="series:N",
    )
    wide = df.pivot_table(index="t", columns="series", values="value").reset_index()
    series = [c for c in wide.columns if c != "t"]
    rule = alt.Chart(wide).mark_rule(color=tk.muted, strokeWidth=1).encode(
        x="t:T",
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("t:T", title="Time", format="%a %b %d %H:%M")]
                + [alt.Tooltip(field=_field(s), type="quantitative", title=s, format=".2f") for s in series],
    ).add_params(hover)
    dots = base.mark_point(filled=True, size=64).encode(
        y="value:Q", color=color, opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    ).transform_filter(hover)
    return alt.layer(lines, rule, dots).properties(height=280)


def _field(name: str) -> str:
    """Escape a column name for Vega-Lite (point names can contain dots and brackets)."""
    return name.replace("\\", "\\\\").replace(".", "\\.").replace("[", "\\[").replace("]", "\\]")
