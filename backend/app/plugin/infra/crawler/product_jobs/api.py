import hashlib
import hmac
import json
import math
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from .schema import JobError, JobRequest


def authenticate(path, header):
    from ..service_auth import authenticate as verify, ServiceAuthError
    try:
        return verify(path, header)
    except ServiceAuthError:
        raise JobError(401, "unauthorized") from None


def handler(store, registry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data=None, reason="success"):
            body = json.dumps({"code": 0 if status < 400 else status, "msg": reason, "data": data}, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self):
            try:
                self.connection.settimeout(10)
                owner = authenticate(registry, self.headers.get("Authorization"))
                target = urlsplit(self.path)
                if target.query:
                    raise JobError(422, "invalid_request")
                if self.command == "GET" and target.path == "/v1/capabilities":
                    return self.reply(200, {"protocol_version": 1, "result_version": 1, "platforms": ["1688"],
                                            "states": ["queued","running","succeeded","failed","needs_login","needs_verification"],
                                            "max_running": 1, "idempotency_retention_seconds": store.retention,
                                            "save_modes": ["overwrite", "history"], "default_save_mode": "overwrite"})
                if self.command == "POST" and target.path == "/v1/jobs":
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise JobError(422, "invalid_request")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 8192:
                        raise JobError(422, "invalid_request")
                    key = self.headers.get("Idempotency-Key", "")
                    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", key):
                        raise JobError(422, "invalid_idempotency_key")
                    request = JobRequest.model_validate_json(self.rfile.read(size)).model_dump(mode="json")
                    job, replayed = store.submit(owner, key, request)
                    return self.reply(200 if replayed else 202, {**job, "replayed": replayed})
                match = re.fullmatch(r"/v1/jobs/([a-f0-9]{32})(/result)?", target.path)
                if self.command == "GET" and match:
                    return self.reply(200, store.get(owner, match[1], result=bool(match[2])))
                raise JobError(404, "not_found")
            except JobError as error:
                return self.reply(error.status, error.data, error.reason)
            except (ValueError, ValidationError, KeyError, TypeError, UnicodeError):
                return self.reply(422, reason="invalid_request")
            except Exception:
                return self.reply(503, reason="service_unavailable")

        do_GET = dispatch
        do_POST = dispatch
    return Handler
