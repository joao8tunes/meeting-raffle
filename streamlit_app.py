"""Meeting Raffle: fair and fun prize draws from meeting attendance files.

Run with ``streamlit run streamlit_app.py``.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

from raffle import charts
from raffle.charts import Theme
from raffle.draw import Entry, Round, run_round
from raffle.export import (audit_frame, files_frame, participants_frame, results_workbook, to_csv, to_excel,
                           winners_frame)
from raffle.insights import Insights, ScoreWeights, build_insights, pseudonyms
from raffle.loader import SUPPORTED_EXTENSIONS, load_file, load_people_list, plain_list
from raffle.models import ENGAGEMENT_KINDS, ENGAGEMENT_LABELS, LoadResult, Meeting
from raffle.people import Person, Roster, Rules, build_roster
from raffle.reports import (engagement_frame, events_frame, insights_workbook, matrix_frame, people_frame,
                            prizes_frame)
from raffle.samples import demo_files, previous_winners_csv
from raffle.stage import (DEFAULT_RACE_THEME, MODE_HELP, MODES, RACE, RACE_THEMES, RACE_THEMES_HELP, STAGE_HEIGHT,
                          WHEEL, build_idle_stage, build_stage, effective_mode)
from raffle.timeparse import AUTO, DAY_FIRST, MONTH_FIRST

DATE_ORDER_LABELS = {AUTO: "Detect automatically", DAY_FIRST: "Day / month / year", MONTH_FIRST: "Month / day / year"}

st.set_page_config(page_title="Meeting Raffle", page_icon="🎟️", layout="wide", initial_sidebar_state="expanded")
st.html(
    "<style>.block-container{padding-top:2.2rem} h1{padding-top:0}"
    # Let rows of pills wrap onto a second line instead of scrolling sideways on narrower screens.
    '[data-testid="stButtonGroup"]>div{flex-wrap:wrap;row-gap:.5rem;overflow-x:visible}</style>'
)


# ---------------------------------------------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------------------------------------------

def init_state() -> None:
    defaults = {
        "rounds": [],             # List[Round]
        "shows": {},              # draw_id -> (mode, theme)
        "animate_draw": None,     # draw_id to animate on this run
        "use_sample": False,
        "editor_version": 0,
        "load_cache": {},         # per-session parse cache: (digest, name, date order) -> LoadResult
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def rounds() -> List[Round]:
    return st.session_state["rounds"]


def viewer_timezone() -> Optional[str]:
    try:
        return st.context.timezone
    except Exception:  # noqa: BLE001 - not available in every runtime
        return None


# ---------------------------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def sample_files() -> List[Tuple[str, bytes]]:
    return demo_files()


def load_cached(name: str, data: bytes, date_order: str) -> LoadResult:
    cache: Dict = st.session_state["load_cache"]
    key = (hashlib.sha256(data).hexdigest(), name, date_order)
    if key not in cache:
        if len(cache) > 50:
            cache.clear()
        cache[key] = load_file(data, name, date_order)
    return cache[key]


def collect_meetings(uploads: Sequence, pasted: str, date_order: str) -> Tuple[List[Meeting], List[LoadResult], int]:
    files: List[Tuple[str, bytes]] = [(upload.name, upload.getvalue()) for upload in uploads or []]
    if st.session_state["use_sample"]:
        files = sample_files() + files

    meetings: List[Meeting] = []
    results: List[LoadResult] = []
    seen, duplicates = set(), 0
    for name, data in files:
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)
        result = load_cached(name, data, date_order)
        results.append(result)
        meetings.extend(result.meetings)

    if pasted.strip():
        pasted_meeting = plain_list(pasted.splitlines(), "Pasted names")
        if pasted_meeting:
            meetings.append(pasted_meeting)
    return meetings, results, duplicates


# ---------------------------------------------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------------------------------------------

def keep_selected(key: str, options: Sequence[str], default: str) -> None:
    """Pills and segmented controls let people unselect the active option: keep their last choice instead.

    The last choice lives under a separate key, so it also survives while the control is hidden.
    """
    remembered = f"last_{key}"
    if st.session_state.get(key) not in options:
        last = st.session_state.get(remembered)
        st.session_state[key] = last if last in options else default
    st.session_state[remembered] = st.session_state[key]


def bounded_state(key: str, low: int, high: int, default: int = 1) -> None:
    """Keep a number widget's value valid when its limits change (e.g. fewer people left in the draw)."""
    st.session_state[key] = max(low, min(high, st.session_state.get(key, default)))


def default_window(meetings: Sequence[Meeting]) -> Tuple[time, time]:
    starts = [m.start or m.first_seen for m in meetings if (m.start or m.first_seen)]
    ends = [m.end or m.last_seen for m in meetings if (m.end or m.last_seen)]
    if not starts or not ends:
        return time(9, 0), time(10, 0)
    start = min(starts, key=lambda d: d.time())
    start = start.replace(minute=start.minute - start.minute % 15, second=0, microsecond=0)
    end = max(ends, key=lambda d: d.time()) + timedelta(minutes=14)
    end = end.replace(minute=end.minute - end.minute % 15, second=0, microsecond=0)
    return start.time(), end.time()


def sidebar() -> Tuple[List[Meeting], List[LoadResult], int, Rules, Roster]:
    with st.sidebar:
        st.markdown("#### 1 · Participants")
        uploads = st.file_uploader(
            "Attendance files",
            type=list(SUPPORTED_EXTENSIONS),
            accept_multiple_files=True,
            help="Microsoft Teams attendance reports in any language or version, Zoom/Google Meet/Webex exports, "
                 "spreadsheets or plain lists. Add several files to combine meetings.",
        )
        with st.expander("Paste names instead"):
            pasted = st.text_area("One person per line", key="pasted", height=120,
                                  placeholder="Jane Doe\nDoe, John\nsomeone@example.com")
        if st.session_state["use_sample"]:
            st.info("Using sample data with fictional names.", icon=":material/science:")
            if st.button("Remove sample data", icon=":material/close:", width="stretch"):
                st.session_state["use_sample"] = False
                st.rerun()
        elif not uploads:
            if st.button("Try with sample data", icon=":material/auto_awesome:", width="stretch"):
                st.session_state["use_sample"] = True
                st.rerun()

        date_order = st.session_state.get("date_order", AUTO)
        meetings, results, duplicates = collect_meetings(uploads, pasted, date_order)
        identity_rules = Rules(merge_similar_names=st.session_state.get("merge_similar", False),
                               reorder_names=st.session_state.get("reorder_names", True))
        preview = build_roster(meetings, identity_rules)
        has_timing = any(m.has_timing for m in meetings)

        st.markdown("#### 2 · Who can win")
        min_minutes = st.number_input(
            "Minimum minutes attended", min_value=0, max_value=24 * 60, step=5, key="min_minutes",
            disabled=not has_timing,
            help="Total time connected (reconnections are added up). Needs files with join/leave times.",
        )
        require_end = st.toggle(
            "Must be there at the end", key="require_end", disabled=not has_timing,
            help="Only people still connected when the meeting (or the time window) ended. Tip: if the host "
                 "stayed connected long after the session, also set a time window that ends with it.",
        )
        grace = 5
        if require_end:
            grace = st.slider("Tolerance before the end (minutes)", 0, 30, 5, key="grace")

        window = None
        if st.toggle("Only count a time window", key="use_window", disabled=not has_timing,
                     help="Ignore time spent before or after the event, e.g. setup or informal chats afterwards."):
            default_start, default_end = default_window(meetings)
            left, right = st.columns(2)
            window = (left.time_input("From", default_start, step=300),
                      right.time_input("To", default_end, step=300))

        min_meetings = 1
        if len(meetings) > 1:
            bounded_state("min_meetings", 1, len(meetings))
            min_meetings = st.number_input(
                "Minimum meetings attended", min_value=1, max_value=len(meetings), key="min_meetings",
                help="Useful for a series of events: count only people who qualified in at least this many.",
            )

        exclude_roles = st.multiselect("Exclude roles", preview.roles, placeholder="e.g. Organizer",
                                       disabled=not preview.roles)
        people_by_key: Dict[str, Person] = {person.key: person for person in preview.people}
        exclude_people = st.multiselect(
            "Exclude people", list(people_by_key), format_func=lambda key: people_by_key[key].name,
            placeholder="Search by name", disabled=not people_by_key,
            help="Nobody is excluded by default.",
        )
        with st.expander("Exclusion list (e.g. previous winners)"):
            typed = st.text_area("Names or emails, one per line", key="exclude_text", height=100)
            exclusion_file = st.file_uploader("…or upload a list", type=list(SUPPORTED_EXTENSIONS),
                                              key="exclude_file",
                                              help="Any supported file, including a winners file from this app.")
        terms = [line for line in typed.splitlines() if line.strip()]
        if exclusion_file is not None:
            terms += load_people_list(exclusion_file.getvalue(), exclusion_file.name)

        with st.expander("Advanced"):
            st.toggle("Merge near-identical names", key="merge_similar",
                      help="Joins typos such as 'Jon Smith' and 'John Smith'. People with different emails are "
                           "never merged.")
            st.toggle("Show 'Last, First' as 'First Last'", value=True, key="reorder_names")
            st.selectbox("Dates in files", list(DATE_ORDER_LABELS), format_func=DATE_ORDER_LABELS.get,
                         key="date_order", help="Only matters for ambiguous dates such as 05/06/2026.")
            st.toggle("Show emails on screen", key="show_emails",
                      help="Emails are always included in downloads. Keep this off when projecting.")

        st.caption("Files are processed in memory and never stored.")

    rules = Rules(
        min_minutes=min_minutes,
        require_present_at_end=require_end,
        end_grace_minutes=grace,
        window=window,
        min_meetings=min_meetings,
        exclude_roles=frozenset(exclude_roles),
        exclude_people=frozenset(exclude_people),
        exclude_terms=tuple(terms),
        merge_similar_names=identity_rules.merge_similar_names,
        reorder_names=identity_rules.reorder_names,
    )
    return meetings, results, duplicates, rules, build_roster(meetings, rules)


# ---------------------------------------------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------------------------------------------

def lazy(producer: Callable[[], bytes]):
    """Build big downloads only when clicked (Streamlit versions that accept a callable), otherwise right away."""
    return producer if "callable" in (st.download_button.__doc__ or "") else producer()


def show_html(html: str) -> None:
    if hasattr(st, "iframe"):
        st.iframe(html, height=STAGE_HEIGHT)
    else:  # Streamlit versions before st.iframe
        import streamlit.components.v1 as components

        components.html(html, height=STAGE_HEIGHT)


def onboarding() -> None:
    st.markdown("### Run a fair giveaway in three steps")
    steps = st.columns(3, border=True)
    steps[0].markdown(
        "**1. Load participants**  \n"
        "Upload attendance files from Teams (any language), Zoom, Meet or Webex, a spreadsheet, or paste names."
    )
    steps[1].markdown(
        "**2. Set the rules**  \n"
        "Minimum minutes, staying until the end, a time window, excluding organizers or previous winners."
    )
    steps[2].markdown(
        "**3. Draw and measure**  \n"
        "Reveal winners with a wheel, a race, a spotlight, a lottery and more, then compare events in Insights and "
        "download auditable reports."
    )
    st.write("")
    if st.button("Try it with sample data", type="primary", icon=":material/auto_awesome:"):
        st.session_state["use_sample"] = True
        st.rerun()
    st.caption("New here? The **Help** section below explains how to export attendance from Teams.")
    with st.expander("Help", icon=":material/help:"):
        help_content()


def draw_pool(roster: Roster, allow_repeat: bool, weighted: bool) -> List[Entry]:
    won = {winner.key for draw in rounds() for winner in draw.winners}
    no_shows = {key for draw in rounds() for key in draw.no_shows}
    pool = []
    for person in roster.eligible:
        if person.key in no_shows or (not allow_repeat and person.key in won):
            continue
        tickets = max(1, person.qualifying_meetings) if weighted else 1
        pool.append(Entry(key=person.key, name=person.name, email=person.email, tickets=tickets))
    return pool


def draw_tab(meetings: Sequence[Meeting], roster: Roster, show_emails: bool) -> None:
    settings, stage = st.columns([1, 2.6], gap="large")
    with stage:
        keep_selected("mode", list(MODES), WHEEL)
        mode = st.pills("Show", list(MODES), format_func=MODES.get, key="mode") or WHEEL
        theme = DEFAULT_RACE_THEME
        if mode == RACE:
            keep_selected("theme", list(RACE_THEMES), DEFAULT_RACE_THEME)
            theme = st.pills("Racers", list(RACE_THEMES), format_func=RACE_THEMES.get, key="theme",
                             help=RACE_THEMES_HELP) or DEFAULT_RACE_THEME
        st.caption(MODE_HELP[mode])
    with settings:
        prize = st.text_input("Prize", key="prize", placeholder="e.g. Gift card", max_chars=80).strip()
        allow_repeat = st.toggle("Past winners can win again", key="allow_repeat")
        weighted = False
        if len(meetings) > 1:
            weighted = st.toggle("Extra chances per meeting", key="weighted",
                                 help="Each meeting a person qualified in counts as one ticket, so regulars "
                                      "have better odds.")

        pool = draw_pool(roster, allow_repeat, weighted)
        bounded_state("winners", 1, max(1, len(pool)))
        count = st.number_input("Winners in this round", min_value=1, max_value=max(1, len(pool)), key="winners",
                                disabled=not pool)
        final_mode, note = effective_mode(mode, int(count))
        if note:
            st.caption(note)

        clicked = st.button(
            f"Draw {'winner' if count == 1 else f'{count} winners'}", type="primary", icon=":material/casino:",
            disabled=not pool, width="stretch",
        )
        st.caption(f"{len(pool)} {'person' if len(pool) == 1 else 'people'} in the draw"
                   + (" · each has an equal chance" if not weighted else " · weighted by meetings"))

    if clicked and pool:
        draw = run_round(len(rounds()) + 1, prize, pool, int(count), weighted=weighted)
        rounds().append(draw)
        st.session_state["shows"][draw.draw_id] = (final_mode, theme)
        st.session_state["animate_draw"] = draw.draw_id
        st.session_state["editor_version"] += 1
        st.session_state["stage_idle"] = False
        st.rerun()  # refresh counters and limits before the show starts

    with stage:
        last = rounds()[-1] if rounds() else None
        if last is None or st.session_state.get("stage_idle"):
            show_html(build_idle_stage(prize, len(pool)))
            return
        animate = st.session_state.get("animate_draw") == last.draw_id
        st.session_state["animate_draw"] = None
        show_mode, show_theme = st.session_state["shows"].get(last.draw_id, (final_mode, theme))
        show_html(build_stage(last, show_mode, show_theme, animate=animate, show_email=show_emails))
        if st.button("Clear stage for the next round", icon=":material/refresh:"):
            st.session_state["stage_idle"] = True
            st.rerun()


def people_tab(meetings: Sequence[Meeting], roster: Roster, show_emails: bool) -> None:
    for note in roster.notes:
        st.info(note, icon=":material/info:")
    search_col, filter_col = st.columns([3, 2])
    search = search_col.text_input("Search", placeholder="Name or email", label_visibility="collapsed")
    view = filter_col.segmented_control("Filter", ["All", "Eligible", "Not eligible"], default="All",
                                        label_visibility="collapsed") or "All"

    frame = participants_frame(roster, meetings)
    if frame.empty:
        st.caption("No participants loaded yet.")
        return
    if view != "All":
        frame = frame[frame["Eligible"] == (view == "Eligible")]
    if search.strip():
        needle = search.strip().casefold()
        frame = frame[frame["Name"].str.casefold().str.contains(needle, regex=False)
                      | frame["Email"].str.casefold().str.contains(needle, regex=False)]

    has_timing = any(m.has_timing for m in meetings)
    hidden = [] if show_emails else ["Email"]
    if not has_timing:
        hidden += ["Minutes", "At the end"]
    if len(meetings) <= 1:
        hidden += ["Meetings", "Qualifying meetings"]
    shown = frame.drop(columns=[c for c in hidden if c in frame.columns])
    shown["Eligible"] = shown["Eligible"].map({True: "✅ Yes", False: "⛔ No"})
    if "At the end" in shown.columns:
        shown["At the end"] = shown["At the end"].map({True: "Yes", False: "No"}).fillna("–")
    st.dataframe(
        shown,
        hide_index=True,
        width="stretch",
        height=min(620, 38 + 35 * max(1, len(frame))),
        column_config={
            "Eligible": st.column_config.TextColumn("Eligible", width="small"),
            "At the end": st.column_config.TextColumn("At the end", width="small"),
            "Minutes": st.column_config.NumberColumn("Minutes", format="%d min", width="small"),
            "Meetings": st.column_config.NumberColumn("Meetings", width="small"),
            "Qualifying meetings": st.column_config.NumberColumn("Qualified in", width="small"),
            "Not eligible because": st.column_config.TextColumn("Not eligible because", width="medium"),
        },
    )
    if roster.merged_names:
        with st.expander(f"Merged near-identical names ({len(roster.merged_names)})"):
            st.markdown("\n".join(f"- {a} ⟷ {b}" for a, b in roster.merged_names))

    full = participants_frame(roster, meetings)
    left, right = st.columns(2)
    left.download_button("Download CSV", to_csv(full), "participants.csv", "text/csv", icon=":material/download:",
                         width="stretch")
    right.download_button("Download Excel",
                          lazy(lambda: to_excel({"Participants": full, "Files": files_frame(meetings)})),
                          "participants.xlsx", icon=":material/table_view:", width="stretch",
                          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def winners_tab(meetings: Sequence[Meeting], roster: Roster, show_emails: bool) -> None:
    if not rounds():
        st.info("No winners yet. Draw a round in the **Draw** tab.", icon=":material/emoji_events:")
        return

    timezone_name = viewer_timezone()
    frame = winners_frame(rounds(), timezone_name, include_email=show_emails)
    edited = st.data_editor(
        frame,
        key=f"winners_editor_{st.session_state['editor_version']}",
        hide_index=True,
        width="stretch",
        disabled=[column for column in frame.columns if column != "No-show"],
        column_config={
            "No-show": st.column_config.CheckboxColumn(
                "No-show", help="The winner didn't claim the prize. They leave the draw; run another round to "
                                "pick a replacement."),
            "Position": st.column_config.NumberColumn("#", width="small"),
            "Round": st.column_config.NumberColumn("Round", width="small"),
        },
    )
    by_number = {draw.number: draw for draw in rounds()}
    for _, row in edited.iterrows():
        draw = by_number.get(int(row["Round"]))
        if draw is None or int(row["Position"]) > len(draw.winners):
            continue
        key = draw.winners[int(row["Position"]) - 1].key
        if bool(row["No-show"]) and key not in draw.no_shows:
            draw.no_shows.append(key)
        elif not bool(row["No-show"]) and key in draw.no_shows:
            draw.no_shows.remove(key)

    snapshot = list(rounds())  # downloads may be built on another thread, after this run
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    columns = st.columns(4)
    columns[0].download_button("Winners CSV", to_csv(winners_frame(snapshot, timezone_name)),
                               f"winners-{stamp}.csv", "text/csv", icon=":material/download:", width="stretch")
    columns[1].download_button("Full results (Excel)",
                               lazy(lambda: results_workbook(snapshot, roster, meetings, timezone_name)),
                               f"raffle-results-{stamp}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               icon=":material/table_view:", width="stretch", type="primary")
    if columns[2].button("Undo last round", icon=":material/undo:", width="stretch",
                         help="Removes the last round; its winners return to the draw."):
        rounds().pop()
        st.session_state["editor_version"] += 1
        st.rerun()
    with columns[3].popover("Clear all", icon=":material/delete:", width="stretch"):
        st.write("Remove every round from this session?")
        if st.button("Yes, clear results", type="primary"):
            st.session_state["rounds"] = []
            st.session_state["shows"] = {}
            st.session_state["editor_version"] += 1
            st.rerun()
    st.caption("Results live only in this browser tab. Download them before closing or refreshing the page.")

    with st.expander("Audit trail", icon=":material/verified:"):
        st.dataframe(audit_frame(rounds(), timezone_name), hide_index=True, width="stretch")
        st.markdown(
            "Every round uses a fresh unpredictable seed. The Excel file lists each round's pool "
            "(*Pools* sheet) and seed (*Audit* sheet), so anyone can reproduce an equal-chance round:"
        )
        st.code("import random\n"
                "keys = sorted(pool_keys)   # 'Draw key' column for that round\n"
                "random.Random(seed).sample(keys, number_of_winners)", language="python")


def files_tab(meetings: Sequence[Meeting], results: Sequence[LoadResult], duplicates: int) -> None:
    for result in results:
        if result.error:
            st.error(f"**{result.source}**: {result.error}", icon=":material/error:")
    if duplicates:
        st.warning(f"{duplicates} duplicate file(s) were ignored.", icon=":material/content_copy:")
    if not meetings:
        st.caption("No files loaded yet.")
        return
    st.dataframe(files_frame(meetings), hide_index=True, width="stretch", column_config={
        "People": st.column_config.NumberColumn("People", width="small"),
        "Notes": st.column_config.TextColumn("Notes", width="large"),
    })


INSIGHT_VIEWS = ["Overview", "Events", "People", "Engagement", "Prizes"]
PERCENT = {"min_value": 0, "max_value": 100, "format": "%.0f%%"}


def show_chart(chart) -> None:
    if "width" in inspect.signature(st.altair_chart).parameters:
        st.altair_chart(chart, width="stretch")
    else:  # Streamlit versions before the width parameter
        st.altair_chart(chart, use_container_width=True)


def chart_theme() -> Theme:
    try:
        return Theme(dark=st.context.theme.type == "dark")
    except Exception:  # noqa: BLE001 - theme detection is not available in every runtime
        return Theme()


def _fmt(value: Optional[float], pattern: str = "{:,.0f}") -> str:
    return "–" if value is None else pattern.format(value)


def _pct_text(value: Optional[float]) -> str:
    return "–" if value is None else f"{100 * value:.0f}%"


def report_options(meetings: Sequence[Meeting]) -> Tuple[bool, List[str], ScoreWeights]:
    with st.expander("Report options", icon=":material/tune:"):
        left, right = st.columns(2, gap="large")
        with left:
            anonymize = st.toggle(
                "Anonymize people", key="anonymize",
                help="Show codes such as 'Person 007' instead of names and hide emails, on screen and in the "
                     "report. Meeting titles are kept as they are.",
            )
            uploads = st.file_uploader(
                "Prize history", type=list(SUPPORTED_EXTENSIONS), accept_multiple_files=True, key="prize_history",
                help="Winners files from previous events (for example the winners CSV from this app). Each row "
                     "is one prize; rows marked as no-show are ignored.",
            )
        with right:
            st.markdown("**Relevance score**")
            st.caption("Weighted average of audience (compared with the largest event), retention (average "
                       "share of the event attended) and engagement (share of people who interacted).")
            audience = st.slider("Audience weight", 0, 100, 40, step=5, key="w_audience")
            retention = st.slider("Retention weight", 0, 100, 40, step=5, key="w_retention")
            engagement = st.slider("Engagement weight", 0, 100, 20, step=5, key="w_engagement")

    history: List[str] = []
    for upload in uploads or []:
        history += load_people_list(upload.getvalue(), upload.name, keep_repeats=True)
    if st.session_state["use_sample"]:
        history += load_people_list(previous_winners_csv(), "previous-winners.csv", keep_repeats=True)
    return anonymize, history, ScoreWeights(audience / 100, retention / 100, engagement / 100)


def insights_tab(meetings: Sequence[Meeting], roster: Roster, rules: Rules, show_emails: bool) -> None:
    anonymize, history, weights = report_options(meetings)
    insights = build_insights(meetings, roster, rules.window, rounds(), history, weights)
    theme = chart_theme()

    view_col, download_col = st.columns([3, 1], vertical_alignment="bottom")
    keep_selected("insights_view", INSIGHT_VIEWS, "Overview")
    view = view_col.segmented_control("View", INSIGHT_VIEWS, key="insights_view",
                                      label_visibility="collapsed") or "Overview"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    download_col.download_button(
        "Insights report (Excel)", lazy(lambda: insights_workbook(insights, meetings, anonymize)),
        f"insights-{'anonymized-' if anonymize else ''}{stamp}.xlsx", icon=":material/download:",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch", type="primary",
    )
    for note in insights.notes:
        st.caption(f"ℹ️ {note}")

    if view == "Overview":
        insights_overview(insights, theme)
    elif view == "Events":
        insights_events(insights, theme, anonymize)
    elif view == "People":
        insights_people(insights, anonymize, show_emails)
    elif view == "Engagement":
        insights_engagement(insights, theme, anonymize)
    else:
        insights_prizes(insights, anonymize, show_emails)


def insights_overview(insights: Insights, theme: Theme) -> None:
    o = insights.overview
    first = st.columns(4)
    first[0].metric("Events", o.events, border=True)
    first[1].metric("Unique people", o.people, border=True)
    first[2].metric("Person-hours", _fmt(o.person_hours, "{:,.1f}"), border=True,
                    help="Total time everyone spent in the events.")
    first[3].metric("Average audience", _fmt(o.avg_audience, "{:,.1f}"), border=True)
    second = st.columns(4)
    second[0].metric("Minutes per attendance", _fmt(o.avg_minutes), border=True,
                     help="Average time a person spent in an event.")
    second[1].metric("Came back", _pct_text(o.returning_share), border=True,
                     help="People who attended two or more events.")
    second[2].metric("Stayed until the end", _pct_text(o.stayed_share), border=True)
    second[3].metric("Engaged", _pct_text(o.engaged_share), border=True,
                     help="People who reacted, unmuted, raised a hand or turned the camera on (events with "
                          "engagement data only).")

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### Audience per event")
        show_chart(charts.audience_per_event(insights, theme))
        st.caption("Returning people attended an earlier event of this set.")
    with right:
        st.markdown("##### Average minutes per event")
        if insights.has_timing:
            show_chart(charts.minutes_per_event(insights, theme))
        else:
            st.caption("The files have no join/leave times.")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### How many events did people attend?")
        show_chart(charts.events_attended(insights, theme))
    with right:
        st.markdown("##### Relevance by event")
        show_chart(charts.relevance(insights, theme))
        st.caption("Weights can be changed under Report options.")


def insights_events(insights: Insights, theme: Theme, anonymize: bool) -> None:
    frame = events_frame(insights, anonymize)
    st.dataframe(frame, hide_index=True, width="stretch", column_config={
        "#": st.column_config.NumberColumn("#", width="small"),
        "Avg attended (%)": st.column_config.ProgressColumn("Avg attended", **PERCENT),
        "Stayed to the end (%)": st.column_config.ProgressColumn("Stayed to the end", **PERCENT),
        "Engaged (%)": st.column_config.ProgressColumn("Engaged", **PERCENT),
        "Relevance": st.column_config.ProgressColumn("Relevance", min_value=0, max_value=100, format="%d"),
    })
    st.download_button("Events CSV", to_csv(frame), "events.csv", "text/csv", icon=":material/download:")

    timed = [e for e in insights.events if e.timeline]
    if timed:
        st.markdown("##### Audience over time")
        chosen = st.multiselect("Events to compare", [e.number for e in timed],
                                default=charts.default_overlay(insights),
                                format_func=lambda n: insights.event(n).label, key="timeline_events")
        if chosen:
            show_chart(charts.audience_over_time(insights, chosen, theme))
            st.caption("Share of each event's audience connected at each minute. A steady line means people "
                       "stayed; drops show when they left.")

    st.markdown("##### Event details")
    number = st.selectbox("Event", [e.number for e in insights.events], index=len(insights.events) - 1,
                          format_func=lambda n: insights.event(n).label, key="event_detail")
    event = insights.event(number)
    cells = st.columns(6)
    cells[0].metric("Duration", _fmt(event.duration_minutes, "{:,.0f} min"))
    cells[1].metric("Audience", event.audience, help=f"{event.new_people} new, {event.returning_people} returning")
    cells[2].metric("Peak", _fmt(event.peak), help=f"at {event.peak_at:%H:%M}" if event.peak_at else None)
    cells[3].metric("Avg minutes", _fmt(event.avg_minutes))
    cells[4].metric("Stayed to the end", _pct_text(event.stayed_share))
    cells[5].metric("Engaged", _pct_text(event.engaged_share))
    names = pseudonyms(p.key for p in insights.people) if anonymize else {p.key: p.name for p in insights.people}
    attendees = sorted(((p, p.minutes_by_event[number]) for p in insights.people if number in p.minutes_by_event),
                       key=lambda item: (-(item[1] or 0), names[item[0].key]))
    st.dataframe(pd.DataFrame([{
        "Person": names[p.key],
        "Minutes": None if minutes is None else round(minutes),
        **{ENGAGEMENT_LABELS[k]: p.engagement.get(k, 0) for k in ENGAGEMENT_KINDS if event.engagement.get(k)},
    } for p, minutes in attendees]), hide_index=True, width="stretch", height=280, column_config={
        "Minutes": st.column_config.ProgressColumn(
            "Minutes", min_value=0, max_value=max(1, round(event.duration_minutes or 1)), format="%d min"),
    })
    st.caption(f"File: {event.source} · {event.kind}" if not anonymize else event.kind)


def insights_people(insights: Insights, anonymize: bool, show_emails: bool) -> None:
    search = st.text_input("Search", placeholder="Search people", label_visibility="collapsed",
                           key="insights_search").strip().casefold()
    frame = people_frame(insights, anonymize, include_email=show_emails)
    matrix = matrix_frame(insights, anonymize)
    if search and not frame.empty:
        haystack = frame["Person"].str.casefold()
        if "Email" in frame.columns:
            haystack = haystack + " " + frame["Email"].fillna("").str.casefold()
        frame = frame[haystack.str.contains(search, regex=False)]
        matrix = matrix[matrix["Person"].str.casefold().str.contains(search, regex=False)]

    st.markdown("##### Participation by person")
    st.dataframe(frame, hide_index=True, width="stretch", height=420, column_config={
        "Attendance rate (%)": st.column_config.ProgressColumn("Attendance rate", **PERCENT),
        "Eligible now": st.column_config.CheckboxColumn("Eligible now"),
        "Longest streak": st.column_config.NumberColumn("Longest streak", help="Consecutive events attended"),
    })
    st.download_button("People CSV", to_csv(frame), "people-insights.csv", "text/csv", icon=":material/download:")

    st.markdown("##### Minutes per event")
    config = {}
    for event in insights.events:
        if event.duration_minutes is not None:
            config[event.label] = st.column_config.ProgressColumn(
                event.short_label, help=event.label, min_value=0, max_value=max(1, round(event.duration_minutes)),
                format="%d min")
        else:
            config[event.label] = st.column_config.TextColumn(event.short_label, help=event.label)
    st.dataframe(matrix, hide_index=True, width="stretch", height=420, column_config=config)
    st.caption("Empty cells mean the person did not attend that event.")
    st.download_button("Minutes per event CSV", to_csv(matrix), "minutes-per-event.csv", "text/csv",
                       icon=":material/download:")


def insights_engagement(insights: Insights, theme: Theme, anonymize: bool) -> None:
    if not insights.has_engagement:
        st.info("No engagement data in these files. Microsoft Teams attendance reports can include reactions, "
                "camera, raised hands and unmute counts; they appear here automatically when present.",
                icon=":material/info:")
        return
    tracked = [e for e in insights.events if e.engagement_tracked]
    totals = {k: sum(e.engagement.get(k, 0) for e in tracked) for k in ENGAGEMENT_KINDS}
    kinds = [k for k in ENGAGEMENT_KINDS if totals[k]]
    cells = st.columns(len(kinds) + 1)
    for cell, kind in zip(cells, kinds):
        cell.metric(ENGAGEMENT_LABELS[kind], f"{totals[kind]:,}", border=True)
    cells[-1].metric("Engaged people", _pct_text(insights.overview.engaged_share), border=True)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### Engagement per event")
        show_chart(charts.engagement_per_event(insights, theme))
        st.dataframe(engagement_frame(insights), hide_index=True, width="stretch", column_config={
            "Engaged (%)": st.column_config.ProgressColumn("Engaged", **PERCENT)})
    with right:
        st.markdown("##### Engagement during an event")
        with_times = [e for e in tracked if e.engagement_timeline]
        if with_times:
            number = st.selectbox("Event", [e.number for e in with_times], index=len(with_times) - 1,
                                  format_func=lambda n: insights.event(n).label, key="engagement_event")
            show_chart(charts.engagement_over_time(insights.event(number), theme))
        else:
            st.caption("The files only have totals per person, without times.")

    st.markdown("##### Most engaged people")
    names = pseudonyms(p.key for p in insights.people) if anonymize else {p.key: p.name for p in insights.people}
    ranked = sorted((p for p in insights.people if p.engagement_total),
                    key=lambda p: (-p.engagement_total, names[p.key]))
    st.dataframe(pd.DataFrame([{
        "Person": names[p.key], "Total": p.engagement_total, "Events": p.events,
        **{ENGAGEMENT_LABELS[k]: p.engagement.get(k, 0) for k in kinds},
    } for p in ranked[:25]]), hide_index=True, width="stretch")


def insights_prizes(insights: Insights, anonymize: bool, show_emails: bool) -> None:
    o = insights.overview
    cells = st.columns(4)
    cells[0].metric("Prizes awarded", o.prizes, border=True, help="This session's rounds (no-shows excluded) "
                                                                 "plus the prize history.")
    cells[1].metric("People awarded", o.people_awarded, border=True)
    cells[2].metric("Awarded more than once", o.repeat_winners, border=True)
    cells[3].metric("Share of people awarded", _pct_text(o.people_awarded / o.people if o.people else None),
                    border=True)
    frame = prizes_frame(insights, anonymize, include_email=show_emails)
    if frame.empty:
        st.info("No prizes yet. Draw winners in the Draw tab, or add winners files from previous events under "
                "Report options.", icon=":material/emoji_events:")
    else:
        if o.winners_avg_events is not None:
            st.caption(f"Winners attended {o.winners_avg_events:.1f} events on average, compared with "
                       f"{o.avg_events_per_person:.1f} for everyone.")
        st.dataframe(frame, hide_index=True, width="stretch", column_config={
            "Eligible now": st.column_config.CheckboxColumn("Eligible now")})
        st.download_button("Prizes CSV", to_csv(frame), "prizes.csv", "text/csv", icon=":material/download:")
    if insights.unmatched_prizes:
        st.caption(f"{insights.unmatched_prizes} prize history entries don't match anyone in the loaded files.")
    if st.session_state["use_sample"]:
        st.caption("Sample data includes a fictional winners file from a previous edition.")


def help_content() -> None:
    st.markdown(
        """
**Export attendance from Microsoft Teams**
- *After the meeting*: open the meeting in the Teams calendar or chat → **Attendance** → **Download**.
- *During the meeting*: **People** → **⋯** → **Download attendance list**.
- Any language and both the current report and the classic `meetingAttendanceList.csv` work.

**Other sources**: Zoom and Google Meet participant exports, Webex attendance, Excel/CSV registrations
(a *Name* column, or *First name* + *Last name*, is enough), or a plain list with one name per line.
Separators, encodings and date formats are detected automatically.

**How people are matched**: the same email is always the same person. Without emails, names are compared
ignoring order, accents and case, so *Doe, Jane* and *JANE DOE* count once. Namesakes with different
emails are kept apart.

**Rules**
- *Minimum minutes* adds up every connection of a person, overlapping devices counted once.
- *Must be there at the end* checks the last connection against the end of the meeting (or of the time window).
- *Minimum meetings attended* is for event series: a meeting only counts if the person met the other rules in it.

**Shows**: wheel, race (cars, rockets, paper planes, sailboats, bikes, balloons or trains), spotlight, last one
standing, lottery, name shuffle or instant. Everyone in the draw appears in the show, up to 2,000 people on
screen (the lottery has no limit); for bigger audiences a random sample that always includes the winners is
shown, and the stage says so. Big races get a live leaderboard.

**Insights**: load several meetings to compare audience, new and returning people, retention over time,
minutes per person, attendance streaks, engagement (reactions, camera, raised hands, unmutes when the
Teams report has them), a configurable relevance score and prizes across events. *Report options* can
anonymize people and load winners files from previous events. Everything can be downloaded as Excel.

**Fairness**: winners are chosen in Python with a cryptographically secure seed before any animation starts;
the show is just for fun. Each round's seed and eligible pool are exported for auditing.

**Privacy**: files are processed in memory for your session only and are never stored. Nobody is excluded
by default, and emails stay hidden on screen unless you turn them on.
"""
    )


def main() -> None:
    init_state()
    meetings, results, duplicates, rules, roster = sidebar()
    show_emails = st.session_state.get("show_emails", False)

    st.markdown("# 🎟️ Meeting Raffle")
    st.caption("Fair, fun prize draws and attendance insights for meetings and events.")
    if not meetings:
        errors = [r for r in results if r.error]
        for result in errors:
            st.error(f"**{result.source}**: {result.error}", icon=":material/error:")
        onboarding()
        return

    metrics = st.columns(4)
    metrics[0].metric("Meetings", len(meetings), border=True)
    metrics[1].metric("People", len(roster.people), border=True)
    metrics[2].metric("Eligible", len(roster.eligible), border=True)
    winners_metric = metrics[3].empty()

    draw, insights, people, winners, files, about = st.tabs(
        ["🎉 Draw", "📊 Insights", "👥 People", "🏆 Winners", "📁 Files", "❓ Help"])
    with draw:
        draw_tab(meetings, roster, show_emails)
    with insights:
        insights_tab(meetings, roster, rules, show_emails)
    with people:
        people_tab(meetings, roster, show_emails)
    with winners:
        winners_tab(meetings, roster, show_emails)
    with files:
        files_tab(meetings, results, duplicates)
    with about:
        help_content()

    claimed = sum(len(d.winners) - len(d.no_shows) for d in rounds())
    winners_metric.metric("Winners", claimed, border=True)


main()
