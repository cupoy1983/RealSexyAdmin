"""后台侧 Redis 会话协调；浏览器及授权状态留在执行端。"""

import json
import secrets
import time


class SessionConflict(ValueError):
    pass


class SessionBroker:
    def __init__(self, redis, *, clock=time.time, prefix="crawler:session:"):
        self.redis, self.clock, self.prefix = redis, clock, prefix

    def scope_key(self, collector, platform, account):
        return f"{self.prefix}scope:{collector}:{platform}:{account}"

    def operation_key(self, operation):
        return self.prefix + "operation:" + operation

    async def capabilities(self):
        workers = []
        async for key in self.redis.scan_iter(match=self.prefix + "worker:*"):
            raw = await self.redis.get(key)
            if raw:
                name = key.decode() if isinstance(key, bytes) else key
                workers.append({"collector_id": name.removeprefix(self.prefix + "worker:"), "platforms": json.loads(raw)["platforms"]})
            if len(workers) >= 100:
                break
        return {"collectors": workers, "cache_seconds": 60, "protocol_version": 1}

    async def mutate(self, keys, callback):
        from redis.exceptions import WatchError

        for _ in range(8):
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(*keys)
                    values = [json.loads(raw) if raw else None for raw in await pipe.mget(keys)]
                    changes, result = callback(values)
                    pipe.multi()
                    for key, value in changes.items():
                        pipe.set(key, json.dumps(value), ex=86400)
                    await pipe.execute()
                    return result
                except WatchError:
                    continue
        raise SessionConflict("状态正在变化，请稍后重试")

    async def status(self, collector, platform, account):
        raw = await self.redis.get(self.scope_key(collector, platform, account))
        value = json.loads(raw) if raw else {}
        heartbeat = await self.redis.get(self.prefix + "worker:" + collector)
        fresh = self.clock() - value.get("checked_at", 0) < 60
        return {"collector_id": collector, "platform": platform, "account": account,
                "state": value.get("state", "unknown") if fresh else "unknown",
                "last_known_state": value.get("state", "unknown"), "checked_at": value.get("checked_at"),
                "reason": value.get("reason", "not_checked"), "fresh": fresh,
                "online": heartbeat is not None, "generation": value.get("generation", 0),
                "pending_operation": value.get("pending_operation") if value.get("pending_until", 0) > self.clock() else None}

    async def enqueue(self, collector, platform, account, kind, user_id, *, force=False):
        worker = await self.redis.get(self.prefix + "worker:" + collector)
        if not worker or platform not in json.loads(worker)["platforms"]:
            raise SessionConflict("执行端不在线或未声明支持该平台")
        key = self.scope_key(collector, platform, account)
        identifier = secrets.token_hex(16)
        operation_key = self.operation_key(identifier)
        now = self.clock()

        def update(values):
            scope = values[0] or {"generation": 0}
            if kind == "check" and not force and now - scope.get("checked_at", 0) < 60:
                return {}, {"cached": True, "operation_id": None}
            if scope.get("pending_until", 0) > now:
                if kind == "check":
                    return {}, {"cached": False, "operation_id": scope["pending_operation"], "shared": True}
                raise SessionConflict("当前账号已有待处理操作")
            scope.update(generation=scope["generation"] + 1, pending_operation=identifier, pending_until=now + 180)
            scope["checked_at"] = 0
            operation = {"operation_id": identifier, "collector_id": collector, "platform": platform, "account": account,
                         "kind": kind, "user_id": user_id, "generation": scope["generation"], "state": "queued", "deadline": now + 180}
            return {key: scope, operation_key: operation}, {"cached": False, "operation_id": identifier}
        return await self.mutate([key], update)

    async def poll(self, collector, platforms):
        await self.redis.set(self.prefix + "worker:" + collector, json.dumps({"platforms": platforms}), ex=90)
        count = 0
        async for key in self.redis.scan_iter(match=self.prefix + "scope:" + collector + ":*"):
            count += 1
            if count > 1000:
                break
            raw = await self.redis.get(key)
            scope = json.loads(raw) if raw else {}
            if not scope.get("pending_operation"):
                continue
            operation_key = self.operation_key(scope["pending_operation"])

            def claim(values, operation_key=operation_key):
                operation = values[0]
                if not operation or operation["state"] != "queued" or operation["deadline"] <= self.clock() or operation["platform"] not in platforms:
                    return {}, None
                operation.update(state="leased", lease_until=min(operation["deadline"], self.clock() + 120), secret=secrets.token_urlsafe(32))
                return {operation_key: operation}, {k: v for k, v in operation.items() if k != "user_id"}
            result = await self.mutate([operation_key], claim)
            if result:
                return result
        return None

    async def complete(self, collector, identifier, secret, state, reason, entry=None):
        operation_key = self.operation_key(identifier)
        raw = await self.redis.get(operation_key)
        if not raw:
            raise SessionConflict("操作不存在或已过期")
        operation = json.loads(raw)
        key = self.scope_key(operation["collector_id"], operation["platform"], operation["account"])

        def update(values):
            current, scope = values
            if not current or not scope or current["collector_id"] != collector or current["state"] != "leased":
                raise SessionConflict("操作不属于本执行端或已结束")
            if not secrets.compare_digest(current.get("secret", ""), secret) or current["lease_until"] <= self.clock():
                raise SessionConflict("操作租约失效")
            if scope["generation"] != current["generation"]:
                raise SessionConflict("旧代结果不能覆盖新会话状态")
            scope.update(state=state, reason=reason, checked_at=self.clock(), pending_operation=None, pending_until=0)
            current.update(state="completed", result_state=state, reason=reason, completed_at=self.clock())
            current.pop("secret", None)
            if entry and current["kind"] == "access":
                current.update(secret=entry, entry_deadline=self.clock() + 60)
            return {key: scope, operation_key: current}, {"accepted": True}
        return await self.mutate([operation_key, key], update)

    async def operation(self, identifier, user_id, *, claim_entry=False):
        key = self.operation_key(identifier)

        def view(values):
            operation = values[0]
            if not operation or operation["user_id"] != user_id:
                raise SessionConflict("操作不存在或无权查看")
            output = {k: v for k, v in operation.items() if k not in {"secret", "user_id"}}
            if operation["state"] in {"queued", "leased"} and min(operation["deadline"], operation.get("lease_until", operation["deadline"])) <= self.clock():
                output["state"] = "expired"
            if claim_entry and operation.get("entry_deadline", 0) > self.clock() and operation.get("secret"):
                output["secret"] = operation.pop("secret")
                return {key: operation}, output
            return {}, output
        return await self.mutate([key], view)

    async def observe(self, collector, platform, account, generation, state, reason, observed_at=None):
        key = self.scope_key(collector, platform, account)
        observed_at = self.clock() if observed_at is None else observed_at
        if not -30 <= self.clock() - observed_at <= 120:
            raise SessionConflict("状态观测超时或时钟偏差过大，请重新检查")

        def update(values):
            scope = values[0] or {"generation": 0}
            if scope["generation"] != generation or scope.get("pending_until", 0) > self.clock():
                raise SessionConflict("观测已过期或账号正在人工处理")
            scope.update(state=state, reason=reason, checked_at=observed_at, generation=generation + 1)
            return {key: scope}, {"accepted": True, "generation": generation + 1}
        return await self.mutate([key], update)
