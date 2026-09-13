"""Build the animated reveal (wheel, race, name shuffle) for a draw round.

The winners are always decided in Python before anything is animated; the stage only plays back
the result. Uploaded names are passed as escaped JSON and rendered with ``textContent``/canvas,
never as HTML.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, Tuple

from .draw import Round

WHEEL, RACE, SHUFFLE, REVEAL = "wheel", "race", "shuffle", "reveal"
MODES: Dict[str, str] = {
    WHEEL: "🎡 Wheel",
    RACE: "🏁 Race",
    SHUFFLE: "🎰 Shuffle",
    REVEAL: "⚡ Instant",
}
RACE_THEMES: Dict[str, str] = {
    "cars": "🏎️ Cars",
    "rockets": "🚀 Rockets",
    "planes": "✈️ Planes",
}
RACE_THEMES_HELP = "Race cars, rockets or paper planes racing to the finish line."
DEFAULT_RACE_THEME = "cars"
MAX_WINNERS = {WHEEL: 5, RACE: 10, SHUFFLE: 10}
STAGE_HEIGHT = 480
_NAMES_ON_STAGE = {WHEEL: 60, SHUFFLE: 400}


def _template() -> str:
    return Path(__file__).with_name("stage.html").read_text(encoding="utf-8")


def effective_mode(mode: str, winners: int) -> Tuple[str, str]:
    """Long shows get boring: fall back to an instant reveal for big rounds."""
    limit = MAX_WINNERS.get(mode)
    if limit is not None and winners > limit:
        return REVEAL, f"{MODES[mode]} supports up to {limit} winners per round, so this round uses an instant reveal."
    return mode, ""


def _json_for_script(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def build_stage(draw: Round, mode: str, theme: str = DEFAULT_RACE_THEME, animate: bool = True,
                show_email: bool = False) -> str:
    mode, _ = effective_mode(mode, len(draw.winners))
    rng = random.Random(draw.seed)
    winner_keys = {winner.key for winner in draw.winners}
    others = [entry for entry in draw.pool if entry.key not in winner_keys]

    if mode == RACE:
        capacity = max(8, min(len(draw.winners) + 5, 14))
    else:
        capacity = _NAMES_ON_STAGE.get(mode, len(draw.winners))
    on_stage = list(draw.winners) + rng.sample(others, max(0, min(len(others), capacity - len(draw.winners))))
    rng.shuffle(on_stage)
    slots = {entry.key: index for index, entry in enumerate(on_stage)}

    count, pool = len(draw.winners), len(draw.pool)
    chances = "chances weighted by meetings attended" if draw.weighted else "equal chances"
    if mode in (WHEEL, RACE) and pool > len(on_stage):
        caption = (f"Winners were drawn from all {pool} eligible people ({chances}); "
                   f"the show features {len(on_stage)} of them.")
    else:
        caption = f"Drawn from {pool} eligible {'person' if pool == 1 else 'people'} with {chances}."

    payload = {
        "mode": mode,
        "theme": theme if theme in RACE_THEMES else DEFAULT_RACE_THEME,
        "animate": bool(animate),
        "seed": draw.seed % 4294967296,
        "title": f"🎁 {draw.prize}" if draw.prize else "🎉 Giveaway",
        "caption": f"{caption} Draw ID {draw.draw_id}.",
        "done": f"Round {draw.number} · {count} winner{'s' if count != 1 else ''}",
        "names": [entry.name for entry in on_stage],
        "winners": [
            {"name": winner.name, "slot": slots[winner.key], "detail": winner.email if show_email else ""}
            for winner in draw.winners
        ],
    }
    return _template().replace("__PAYLOAD__", _json_for_script(payload))


def build_idle_stage(prize: str, pool_size: int) -> str:
    people = f"{pool_size} {'person' if pool_size == 1 else 'people'} in the draw"
    payload = {
        "mode": "idle",
        "animate": False,
        "seed": 1,
        "title": f"🎁 {prize}" if prize else "",
        "caption": "",
        "done": "",
        "names": [],
        "winners": [],
        "idleTitle": "Ready when you are" if pool_size else "Nobody is eligible yet",
        "idleSubtitle": people if pool_size else "Load participants or relax the rules",
    }
    return _template().replace("__PAYLOAD__", _json_for_script(payload))
