import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: str = Field(pattern=r"^1688$")
    url: str = Field(max_length=2048)
    session_name: str = Field(pattern=r"^[a-zA-Z0-9_\u3400-\u4dbf\u4e00-\u9fff-]{1,48}$")

    @field_validator('session_name', mode='before')
    @classmethod
    def validate_session_name(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_\u3400-\u4dbf\u4e00-\u9fff-]{1,48}', value):
            raise ValueError('会话名格式无效')
        return value

    @model_validator(mode="after")
    def canonical_url(self):
        target = urlsplit(self.url)
        match = re.fullmatch(r"/offer/([1-9][0-9]{0,19})\.html", target.path)
        if (target.scheme != "https" or target.hostname not in {"detail.1688.com", "m.1688.com"}
                or target.username or target.password or target.port not in {None, 443} or not match):
            raise ValueError("仅接受 1688 HTTPS 商品详情链接")
        self.url = f"https://detail.1688.com/offer/{match[1]}.html"
        return self


class JobError(Exception):
    def __init__(self, status, reason, data=None):
        self.status, self.reason, self.data = status, reason, data
        super().__init__(reason)


TERMINAL = {"succeeded", "failed", "needs_login", "needs_verification"}
REASONS = {"session_missing", "login_required", "challenge", "access_denied", "network_error", "navigation_denied",
           "identity_mismatch", "unsupported_page", "timeout", "process_interrupted", "internal_error"}
