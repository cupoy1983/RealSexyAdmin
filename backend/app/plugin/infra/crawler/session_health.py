"""Version-bound failure evidence shared by login and collection processes."""
import hashlib
import re
import time
from pathlib import Path

from .state import read_private_json, write_private_json


class SessionHealth:
    def __init__(self, directory, *, clock=time.time):
        self.directory, self.clock = Path(directory), clock

    def path(self, scope, version):
        if not re.fullmatch(r'[a-f0-9]{64}', version):
            raise ValueError('Invalid authorization version')
        key = hashlib.sha256((scope + '\0' + version).encode()).hexdigest()
        return self.directory / (key + '.json')

    def read(self, scope, version):
        return read_private_json(self.path(scope, version), max_bytes=8192)

    def report(self, scope, version, state, reason):
        if state not in {'not_logged_in', 'login_required', 'verification_required', 'access_denied', 'network_error', 'unknown'}:
            raise ValueError('Invalid session observation')
        if not re.fullmatch(r'[a-z_]{1,64}', reason):
            raise ValueError('Invalid session reason')
        write_private_json(self.path(scope, version), {'state': state, 'reason': reason, 'observed_at': self.clock()})
