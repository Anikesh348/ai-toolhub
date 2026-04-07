from datetime import datetime
from zoneinfo import ZoneInfo

from bson.codec_options import CodecOptions


IST_TIMEZONE_NAME = "Asia/Kolkata"
IST = ZoneInfo(IST_TIMEZONE_NAME)
IST_CODEC_OPTIONS = CodecOptions(tz_aware=True, tzinfo=IST)


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def as_ist(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)
