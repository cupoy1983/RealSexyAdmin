"""业务采集适配器使用的状态回报接口；注入已有认证 HTTP client。"""


class SessionStatusClient:
    def __init__(self, client, root, collector_id):
        self.client, self.root, self.collector = client, root.rstrip("/") + "/selection/platform-session-worker", collector_id

    async def state(self, platform, account="default"):
        response = await self.client.get(self.root + "/state", params={"collector_id": self.collector, "platform": platform, "account": account})
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError("后台拒绝平台状态查询")
        return body["data"]

    async def observe(self, platform, account, generation, state, reason, *, observed_at):
        response = await self.client.post(self.root + "/observation", json={"platform": platform, "account": account,
            "generation": generation, "observed_at": observed_at, "state": state, "reason": reason})
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError("后台拒绝平台状态回报")
        return body["data"]
