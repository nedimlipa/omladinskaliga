"""Shared Jinja2Templates instance with custom filters."""
import datetime
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

# Sarajevo = UTC+2 (CET) / UTC+3 (CEST)
# Python 3.9+ has zoneinfo; as a fallback we use a fixed +2h offset.
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Sarajevo")
except Exception:
    _TZ = datetime.timezone(datetime.timedelta(hours=2))


def local_dt_str(value: datetime.datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Convert a UTC datetime to a Sarajevo-localised display string."""
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(_TZ).strftime(fmt)


templates.env.filters["local_dt"] = lambda v: local_dt_str(v)
