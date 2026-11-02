"""Which methods exist (built-in + the ones you built) and which are switched on."""
from __future__ import annotations

from . import builder, settings
from .strategies import STRATEGIES, Strategy


def available() -> list[Strategy]:
    out = list(STRATEGIES)
    for m in settings.load()["methods"]["custom"]:
        try:
            out.append(builder.compile(m))
        except Exception:
            continue  # a broken saved method must never stop the agent
    return out


def active_names() -> set[str]:
    return set(settings.enabled_methods())


def active() -> list[Strategy]:
    names = active_names()
    return [s for s in available() if s.name in names]
