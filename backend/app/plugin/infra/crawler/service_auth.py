"""Collector backend service authentication, independent of business modules."""
import hashlib
import hmac
import json
import math
import re
import time
from pathlib import Path

class ServiceAuthError(Exception):
    pass

def authenticate(path, header):
    if not isinstance(header, str) or not re.fullmatch(r"Bearer colsvc_[A-Za-z0-9_-]{32,128}", header):
        raise ServiceAuthError("unauthorized")
    location = Path(path)
    if location.is_symlink() or location.stat().st_size > 65536:
        raise ServiceAuthError("unauthorized")
    digest = hashlib.sha256(header[7:].encode()).hexdigest()
    registry = json.loads(location.read_text())
    for client in registry.get("clients", [])[:128]:
        expiry = client.get("expires_at")
        valid_time = expiry is None or (type(expiry) in (float, int) and math.isfinite(expiry) and expiry > time.time())
        if (client.get("enabled") is True and valid_time and re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", client.get("id", ""))
                and hmac.compare_digest(client.get("token_sha256", ""), digest)):
            return client["id"]
    raise ServiceAuthError("unauthorized")
