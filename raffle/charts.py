"""Altair charts for the insights views.

Colors follow a categorical palette validated for color-vision deficiencies (adjacent pairs) in light
and dark mode. Series keep their color when filtered, text uses neutral ink, and every chart has a
table with the same numbers next to it in the app.
"""

from __future__ import annotations

from typing import List, Sequence

import altair as alt
import pandas as pd

from .insights import EventInsight, Insights
from .models import ENGAGEMENT_KINDS, ENGAGEMENT_LABELS

LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
MAX_OVERLAY_SERIES = len(LIGHT)
HEIGHT = 260


class Theme:
    def __init__(self, dark: bool = False):
        self.series = DARK if dark else LIGHT
        self.surface = "#0e1117" if dark else "#ffffff"
        self.ink = "#c3c2b7" if dark else "#52514e"
        self.muted = "#898781"


def _events_frame(insights: Insights) -> pd.DataFrame:
    return pd.DataFrame([{
        "number": e.number, "event": e.short_label, "label": e.label, "audience": e.audience,
        "new": e.new_people, "returning": e.returning_people,
        "avg_minutes": None if e.avg_minutes is None else round(e.avg_minutes),
        "score": None if e.score is None else round(e.score),
    } for e in insights.events])


def _event_axis(insights: Insights, title=None) -> alt.X:
    return alt.X("event:N", sort=[e.short_label for e in insights.events], title=title,
                 axis=alt.Axis(labelAngle=0, labelLimit=90))


def audience_per_event(insights: Insights, theme: Theme) -> alt.Chart:
    rows = []
    for e in insights.events:
        rows.append({"event": e.short_label, "label": e.label, "group": "Returning", "order": 0,
                     "people": e.returning_people, "audience": e.audience})
        rows.append({"event": e.short_label, "label": e.label, "group": "New", "order": 1,
                     "people": e.new_people, "audience": e.audience})
    frame = pd.DataFrame(rows)
    bars = alt.Chart(frame).mark_bar(size=24, stroke=theme.surface, strokeWidth=2).encode(
        x=_event_axis(insights),
        y=alt.Y("sum(people):Q", title="People", stack="zero"),
        color=alt.Color("group:N", scale=alt.Scale(domain=["Returning", "New"], range=theme.series[:2]),
                        legend=alt.Legend(title=None, orient="top")),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("label:N", title="Event"), alt.Tooltip("group:N", title="People"),
                 alt.Tooltip("people:Q", title="Count"), alt.Tooltip("audience:Q", title="Audience")],
    )
    totals = alt.Chart(_events_frame(insights)).mark_text(dy=-6, baseline="bottom", color=theme.ink).encode(
        x=_event_axis(insights), y=alt.Y("audience:Q"), text=alt.Text("audience:Q"),
    )
    return (bars + totals).properties(height=HEIGHT)


def minutes_per_event(insights: Insights, theme: Theme) -> alt.Chart:
    frame = _events_frame(insights).dropna(subset=["avg_minutes"])
    base = alt.Chart(frame).encode(x=_event_axis(insights), y=alt.Y("avg_minutes:Q", title="Minutes"))
    bars = base.mark_bar(size=24, color=theme.series[0], cornerRadiusEnd=4).encode(
        tooltip=[alt.Tooltip("label:N", title="Event"), alt.Tooltip("avg_minutes:Q", title="Average minutes")])
    labels = base.mark_text(dy=-6, baseline="bottom", color=theme.ink).encode(text="avg_minutes:Q")
    return (bars + labels).properties(height=HEIGHT)


def events_attended(insights: Insights, theme: Theme) -> alt.Chart:
    most = max(insights.distribution, default=0)
    frame = pd.DataFrame([{"events": str(n), "people": insights.distribution.get(n, 0)} for n in range(1, most + 1)])
    base = alt.Chart(frame).encode(
        x=alt.X("events:N", sort=[str(n) for n in range(1, most + 1)], title="Events attended",
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("people:Q", title="People"),
    )
    bars = base.mark_bar(size=24, color=theme.series[0], cornerRadiusEnd=4).encode(
        tooltip=[alt.Tooltip("events:N", title="Events attended"), alt.Tooltip("people:Q", title="People")])
    labels = base.mark_text(dy=-6, baseline="bottom", color=theme.ink).encode(text="people:Q")
    return (bars + labels).properties(height=HEIGHT)


def relevance(insights: Insights, theme: Theme) -> alt.Chart:
    frame = _events_frame(insights).dropna(subset=["score"])
    base = alt.Chart(frame).encode(
        x=_event_axis(insights),
        y=alt.Y("score:Q", title="Relevance (0–100)", scale=alt.Scale(domain=[0, 100])),
    )
    bars = base.mark_bar(size=24, color=theme.series[0], cornerRadiusEnd=4).encode(
        tooltip=[alt.Tooltip("label:N", title="Event"), alt.Tooltip("score:Q", title="Relevance")])
    labels = base.mark_text(dy=-6, baseline="bottom", color=theme.ink).encode(text="score:Q")
    return (bars + labels).properties(height=HEIGHT)


def audience_over_time(insights: Insights, numbers: Sequence[int], theme: Theme) -> alt.Chart:
    """Share of each event's audience connected over time. Overlaid when colors can stay unique per event."""
    events = [e for e in insights.events if e.number in set(numbers) and e.timeline]
    rows = [{
        "event": e.label, "minute": round(minute, 1), "people": people,
        "share": people / e.audience if e.audience else 0.0,
    } for e in events for minute, people in e.timeline]
    frame = pd.DataFrame(rows, columns=["event", "minute", "people", "share"])
    x = alt.X("minute:Q", title="Minutes since start")
    y = alt.Y("share:Q", title="Audience connected", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1]))
    tooltip = [alt.Tooltip("event:N", title="Event"), alt.Tooltip("minute:Q", title="Minute", format=".0f"),
               alt.Tooltip("people:Q", title="People connected"),
               alt.Tooltip("share:Q", title="Share of audience", format=".0%")]

    timed = [e for e in insights.events if e.timeline]
    if len(timed) <= MAX_OVERLAY_SERIES:
        # Fixed domain: an event keeps its color when others are hidden.
        color = alt.Color("event:N", scale=alt.Scale(domain=[e.label for e in timed],
                                                     range=theme.series[:len(timed)]),
                          legend=alt.Legend(title=None, orient="top", columns=2, labelLimit=260))
        hover = alt.selection_point(on="mouseover", nearest=True, fields=["minute"], empty=False)
        base = alt.Chart(frame).encode(x=x, y=y, color=color)
        lines = base.mark_line(strokeWidth=2, interpolate="monotone")
        points = base.mark_point(size=64, filled=True, stroke=theme.surface, strokeWidth=2).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)), tooltip=tooltip).add_params(hover)
        rule = alt.Chart(frame).mark_rule(color=theme.muted, strokeWidth=1).encode(x="minute:Q").transform_filter(hover)
        # The height includes the legend and axes when the chart stretches to the container width.
        return (lines + rule + points).properties(height=HEIGHT + 120 + 22 * ((len(timed) + 1) // 2))

    return alt.Chart(frame).mark_line(strokeWidth=2, color=theme.series[0], interpolate="monotone").encode(
        x=x, y=y, tooltip=tooltip,
    ).properties(width=220, height=140).facet(facet=alt.Facet("event:N", title=None), columns=3)


def _kind_color(theme: Theme, kinds: Sequence[str]) -> alt.Color:
    return alt.Color("kind:N", scale=alt.Scale(domain=[ENGAGEMENT_LABELS[k] for k in ENGAGEMENT_KINDS],
                                               range=theme.series[:len(ENGAGEMENT_KINDS)]),
                     legend=alt.Legend(title=None, orient="top", values=[ENGAGEMENT_LABELS[k] for k in kinds]))


def engagement_per_event(insights: Insights, theme: Theme) -> alt.Chart:
    tracked = [e for e in insights.events if e.engagement_tracked]
    kinds = [k for k in ENGAGEMENT_KINDS if any(e.engagement.get(k) for e in tracked)]
    rows = [{"event": e.short_label, "label": e.label, "kind": ENGAGEMENT_LABELS[k], "order": i,
             "count": e.engagement.get(k, 0)} for e in tracked for i, k in enumerate(kinds)]
    frame = pd.DataFrame(rows, columns=["event", "label", "kind", "order", "count"])
    return alt.Chart(frame).mark_bar(size=24, stroke=theme.surface, strokeWidth=2).encode(
        x=alt.X("event:N", sort=[e.short_label for e in tracked], title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("sum(count):Q", title="Count", stack="zero"),
        color=_kind_color(theme, kinds),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("label:N", title="Event"), alt.Tooltip("kind:N", title="Type"),
                 alt.Tooltip("count:Q", title="Count")],
    ).properties(height=HEIGHT)


def engagement_over_time(event: EventInsight, theme: Theme, bin_minutes: int = 5) -> alt.Chart:
    kinds = [k for k in ENGAGEMENT_KINDS if any(kind == k for _, kind in event.engagement_timeline)]
    rows = [{"bin": int(minute // bin_minutes) * bin_minutes, "kind": ENGAGEMENT_LABELS[kind],
             "order": ENGAGEMENT_KINDS.index(kind)} for minute, kind in event.engagement_timeline]
    frame = pd.DataFrame(rows, columns=["bin", "kind", "order"])
    frame["range"] = frame["bin"].map(lambda b: f"{b}–{b + bin_minutes} min")
    return alt.Chart(frame).mark_bar(stroke=theme.surface, strokeWidth=2).encode(
        x=alt.X("bin:O", title=f"Minutes since start ({bin_minutes}-minute intervals)", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("count():Q", title="Count", stack="zero"),
        color=_kind_color(theme, kinds),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("range:N", title="When"), alt.Tooltip("kind:N", title="Type"),
                 alt.Tooltip("count():Q", title="Count")],
    ).properties(height=HEIGHT)


def default_overlay(insights: Insights) -> List[int]:
    timed = [e.number for e in insights.events if e.timeline]
    return timed[-4:] if len(timed) > 4 else timed
