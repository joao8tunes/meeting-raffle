from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from raffle.stage import MODE_HELP, MODES

APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


def start() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception
    return app


def button(app: AppTest, text: str):
    return next(b for b in app.button if text in b.label)


def test_first_screen_has_no_names_and_no_errors():
    app = start()
    assert not app.error
    assert "Try it with sample data" in [b.label for b in app.button]
    for select in app.multiselect:
        assert select.value == []


@pytest.mark.parametrize("mode", list(MODES))
def test_sample_data_draw_in_every_mode(mode):
    app = start()
    button(app, "Try it with sample data").click().run()
    assert not app.exception
    assert [m.value for m in app.metric][:3] == ["4", "39", "39"]

    app.session_state["mode"] = mode
    if mode == "race":
        app.session_state["theme"] = "balloons"
    app.run()
    assert MODE_HELP[mode] in [caption.value for caption in app.caption]
    app.number_input(key="winners").set_value(2).run()
    button(app, "Draw 2 winners").click().run()
    assert not app.exception
    assert len(app.session_state["rounds"]) == 1
    assert len(app.session_state["rounds"][0].winners) == 2
    assert app.metric[3].value == "2"


def test_rules_exclusions_undo_and_repeat_protection():
    app = start()
    button(app, "Try it with sample data").click().run()
    app.number_input(key="min_minutes").set_value(45).run()
    app.toggle(key="require_end").set_value(True).run()
    app.number_input(key="min_meetings").set_value(4).run()
    eligible = int(app.metric[2].value)
    assert 0 < eligible < 40

    app.number_input(key="winners").set_value(eligible).run()
    button(app, "Draw").click().run()
    assert not app.exception
    assert app.metric[3].value == str(eligible)
    assert button(app, "Draw").disabled, "everyone already won, nobody is left in the draw"

    button(app, "Undo last round").click().run()
    assert app.session_state["rounds"] == []
    assert not button(app, "Draw").disabled


def test_winners_file_from_a_previous_event_can_exclude_people():
    app = start()
    button(app, "Try it with sample data").click().run()
    app.text_area(key="exclude_text").set_value("avery.nimbus@example.com\nQuasar, Blake").run()
    assert not app.exception
    assert app.metric[2].value == "37"


@pytest.mark.parametrize("anonymize", [False, True])
def test_every_insights_view_renders(anonymize):
    app = start()
    button(app, "Try it with sample data").click().run()
    app.toggle(key="anonymize").set_value(anonymize).run()
    for view in ["Overview", "Events", "People", "Engagement", "Prizes"]:
        app.session_state["insights_view"] = view
        app.run()
        assert not app.exception, (view, app.exception)
    report = next(b for b in app.get("download_button") if "Insights report" in b.proto.label)
    assert report is not None


def test_pasted_names_work_without_files():
    app = start()
    app.text_area(key="pasted").set_value("Ana Nova\nDoe, John\njohn doe\n").run()
    assert not app.exception
    assert [m.value for m in app.metric][:3] == ["1", "2", "2"]
