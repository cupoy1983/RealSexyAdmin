"""HTTP 冷却提示解析，不保留原响应头。"""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def retry_after_seconds(value, now=None):
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        seconds = int(value) if value.strip().isdigit() else int((parsedate_to_datetime(value).astimezone(UTC) - (now or datetime.now(UTC))).total_seconds())
        return max(0, min(86400, seconds))
    except (ValueError, TypeError, OverflowError):
        return None
