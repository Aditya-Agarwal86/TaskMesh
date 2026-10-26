import asyncio
import signal
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.workers.engine import WorkerEngine

setup_logging()
logger = get_logger("taskmesh.worker")


async def main():
    engine = WorkerEngine(
        concurrency=settings.WORKER_CONCURRENCY,
        lease_timeout=settings.DEFAULT_JOB_LEASE_TIMEOUT_SECONDS,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(engine.stop()))
        except NotImplementedError:
            pass

    await engine.start()


if __name__ == "__main__":
    asyncio.run(main())
