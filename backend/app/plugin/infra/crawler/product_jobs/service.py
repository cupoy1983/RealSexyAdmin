import asyncio
import os
import signal
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from ..state import write_private_json
from ..traffic import TrafficGate
from .ali1688 import CollectionFailure, collect
from .api import handler
from .store import JobStore


class JobRunner:
    def __init__(self, store, adapter, *, gate=None, timeout=120, results_dir=None):
        self.store, self.adapter, self.gate, self.timeout = store, adapter, gate, timeout
        self.results_dir = Path(results_dir) if results_dir else None

    async def run_once(self):
        if self.gate and self.gate.remaining > 0:
            return False
        job = self.store.claim()
        if not job:
            return False
        identifier=job['job_id']
        try:
            if self.gate:await self.gate.navigate()
            result=await asyncio.wait_for(self.adapter(job['request'],identifier),timeout=self.timeout)
            if self.results_dir:write_private_json(self.results_dir/(identifier+'.json'),result)
            self.store.finish(identifier,'succeeded',result=result)
        except CollectionFailure as error:
            self.store.finish(identifier,error.state,reason=error.reason,diagnostic=error.diagnostic)
        except asyncio.TimeoutError:
            self.store.finish(identifier,'failed',reason='timeout')
        except asyncio.CancelledError:
            self.store.finish(identifier,'failed',reason='process_interrupted')
            raise
        except Exception:
            self.store.finish(identifier,'failed',reason='internal_error')
        return True

    async def serve(self):
        while True:
            if not await self.run_once():await asyncio.sleep(0.5)


async def main():
    # Execution-only state: not the RealSexyAdmin business database.
    os.umask(0o077)
    root=Path(os.environ.get('COLLECTOR_JOB_DATA','/data/private/product-jobs'))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    lock=(root/'service.lock').open('a')
    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    store=JobStore(root/'jobs.sqlite3')
    store.recover()
    gate=TrafficGate(root/'1688-traffic.json',interval_seconds=5)
    adapter=partial(collect,auth_dir=os.environ.get('COLLECTOR_AUTH_DIR','/auth'),
        config_path=os.environ.get('COLLECTOR_SITES','/config/sites.json'),
        diagnostics_dir=root/'diagnostics',executable=os.environ.get('COLLECTOR_CHROME','/opt/google/chrome/chrome'),gate=gate)
    registry=os.environ['COLLECTOR_CLIENT_REGISTRY']
    server=ThreadingHTTPServer(('0.0.0.0',8090),handler(store,registry));server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    runner=JobRunner(store,adapter,gate=gate,results_dir=os.environ.get('COLLECTOR_RESULT_DIR','/data/product-results'))
    task=asyncio.create_task(runner.serve())
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
    stop_task=asyncio.create_task(stop.wait())
    try:
        done,_=await asyncio.wait([task,stop_task],return_when=asyncio.FIRST_COMPLETED)
        if task in done:await task
    finally:
        task.cancel();stop_task.cancel()
        await asyncio.gather(task,stop_task,return_exceptions=True)
        await asyncio.to_thread(server.shutdown);server.server_close();lock.close()


if __name__=='__main__':asyncio.run(main())
