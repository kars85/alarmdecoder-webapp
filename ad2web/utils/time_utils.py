# ad2web/utils/time_utils.py

"""Date and time utilities for current time and human-readable durations."""

from datetime import datetime


def get_current_time() -> datetime:
    """Get the current UTC time as a datetime object."""
    return datetime.now(datetime.UTC)


def pretty_date(dt: datetime, default: str = "just now") -> str:
    """Return a human-readable string representing time since the given datetime.

    Examples: "3 days ago", "5 hours ago", "just now" (if within a minute).
    If an unsupported interval, returns the `default` string.
    """
    now = datetime.now(datetime.UTC)
    diff = now - dt

    periods = (
        (diff.days // 365, "year", "years"),
        (diff.days // 30, "month", "months"),
        (diff.days // 7, "week", "weeks"),
        (diff.days, "day", "days"),
        (diff.seconds // 3600, "hour", "hours"),
        (diff.seconds // 60, "minute", "minutes"),
        (diff.seconds, "second", "seconds"),
    )

    for period, singular, plural in periods:
        if period:
            # If the period is 1, use singular form; otherwise use plural
            return f"{period} {singular if period == 1 else plural} ago"

    return default
