"""Build the animated reveal for a draw round.

The winners are always decided in Python before anything is animated; the stage only plays back
the result. Uploaded names are passed as escaped JSON and rendered with ``textContent``/canvas,
never as HTML.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Tuple

from .draw import Entry, Round
from .text import strip_accents

WHEEL, RACE, SPOTLIGHT, ELIMINATION, LOTTERY, SHUFFLE, REVEAL = (
    "wheel", "race", "spotlight", "elimination", "lottery", "shuffle", "reveal")
MODES: Dict[str, str] = {
    WHEEL: "🎡 Wheel",
    RACE: "🏁 Race",
    SPOTLIGHT: "🔦 Spotlight",
    ELIMINATION: "🏆 Last standing",
    LOTTERY: "🎟️ Lottery",
    SHUFFLE: "🎰 Shuffle",
    REVEAL: "⚡ Instant",
}
MODE_HELP: Dict[str, str] = {
    WHEEL: "Everyone gets a slice and the pointer shows who is passing by. Best for up to a few hundred people.",
    RACE: "Everyone races to the finish line. Big fields get a live leaderboard with the names in front.",
    SPOTLIGHT: "Everyone appears as a tile while a spotlight searches the room. Great for large audiences.",
    ELIMINATION: "Everyone starts on screen and waves knock people out until only the winners remain. "
                 "Great for large audiences.",
    LOTTERY: "Everyone holds a ticket number (alphabetical order) and reels reveal the winning number digit by "
             "digit. Works for any audience size.",
    SHUFFLE: "Names flash on a big display until one of them stops.",
    REVEAL: "No animation: the winners appear right away.",
}
RACE_THEMES: Dict[str, str] = {
    "cars": "🏎️ Cars",
    "rockets": "🚀 Rockets",
    "planes": "✈️ Paper planes",
    "boats": "⛵ Sailboats",
    "bikes": "🚴 Bikes",
    "balloons": "🎈 Balloons",
    "trains": "🚄 Trains",
}
RACE_THEMES_HELP = "What races on the track. The draw is the same whatever you pick."
DEFAULT_RACE_THEME = "cars"
MAX_WINNERS = {WHEEL: 5, RACE: 10, SPOTLIGHT: 5, ELIMINATION: 30, LOTTERY: 5, SHUFFLE: 10}
MAX_ON_STAGE = 2000   # names drawn on screen; above this a random sample (always with the winners) is shown
STAGE_HEIGHT = 480


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


def _people(count: int) -> str:
    return f"{count:,} {'person' if count == 1 else 'people'}"


def lottery_tickets(pool: List[Entry]) -> List[Entry]:
    """Ticket order for the lottery show: alphabetical by name, so anyone can find their own number."""
    return sorted(pool, key=lambda entry: (strip_accents(entry.name).casefold(), entry.key))


def _lottery(draw: Round) -> dict:
    ordered = lottery_tickets(draw.pool)
    number = {entry.key: index + 1 for index, entry in enumerate(ordered)}
    digits = len(str(len(ordered)))
    draws = []
    for winner in draw.winners:
        ticket = number[winner.key]
        # Tickets that share every digit but the last: the finalists shown before the last reel stops.
        decade = ticket // 10 * 10
        low, high = (1, len(ordered)) if digits == 1 else (max(1, decade), min(len(ordered), decade + 9))
        draws.append({"ticket": ticket, "neighbors": [[n, ordered[n - 1].name] for n in range(low, high + 1)]})
    return {"tickets": len(ordered), "digits": digits, "draws": draws}


def build_stage(draw: Round, mode: str, theme: str = DEFAULT_RACE_THEME, animate: bool = True,
                show_email: bool = False) -> str:
    mode, _ = effective_mode(mode, len(draw.winners))
    rng = random.Random(draw.seed)
    winner_keys = {winner.key for winner in draw.winners}
    others = [entry for entry in draw.pool if entry.key not in winner_keys]

    capacity = len(draw.winners) if mode in (REVEAL, LOTTERY) else MAX_ON_STAGE
    on_stage = list(draw.winners) + rng.sample(others, max(0, min(len(others), capacity - len(draw.winners))))
    rng.shuffle(on_stage)
    slots = {entry.key: index for index, entry in enumerate(on_stage)}

    count, pool = len(draw.winners), len(draw.pool)
    chances = "chances weighted by meetings attended" if draw.weighted else "equal chances"
    if mode == LOTTERY:
        caption = f"{_people(pool)} hold tickets 1–{pool:,} in alphabetical order ({chances})."
    elif mode == REVEAL:
        caption = f"Drawn from {_people(pool)} with {chances}."
    elif pool > len(on_stage):
        caption = (f"Winners were drawn from all {_people(pool)} ({chances}); "
                   f"the show features {len(on_stage):,} of them.")
    else:
        caption = f"All {_people(pool)} in the draw are in the show ({chances})."

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
    if mode == LOTTERY:
        payload["lottery"] = _lottery(draw)
    return _template().replace("__PAYLOAD__", _json_for_script(payload))


def build_idle_stage(prize: str, pool_size: int) -> str:
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
        "idleSubtitle": f"{_people(pool_size)} in the draw" if pool_size else "Load participants or relax the rules",
    }
    return _template().replace("__PAYLOAD__", _json_for_script(payload))
