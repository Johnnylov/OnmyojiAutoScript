"""Explicit, side-effect-free business prerequisites known before execution.

Only proven task rules belong here. A returned False or a text exception is
never enough evidence to classify arbitrary tasks as skipped.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class BusinessSkip:
    reason: str
    next_run: datetime


def business_preflight(command, now):
    if command != 'DemonEncounter':
        return None
    # Preserve the existing task's actual rule: allowed from 17:00 until
    # 23:00; an out-of-window attempt is deferred to the next 17:30.
    if now.hour < 17:
        target = now.replace(hour=17, minute=30, second=0, microsecond=0)
    elif now.hour >= 23:
        target = (now + timedelta(days=1)).replace(hour=17, minute=30, second=0, microsecond=0)
    else:
        return None
    return BusinessSkip('activity_window_closed', target)
