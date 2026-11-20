import multiprocessing
import os

from env_loader import env_int, load_dotenv

load_dotenv()


bind = os.getenv("MIRROR_BIND", "[::]:6100")
workers = env_int("MIRROR_WORKERS", min(max(multiprocessing.cpu_count() * 2 + 1, 2), 4), minimum=1)
threads = env_int("MIRROR_THREADS", 4, minimum=1)
timeout = env_int("MIRROR_TIMEOUT", 60, minimum=1)
graceful_timeout = env_int("MIRROR_GRACEFUL_TIMEOUT", 30, minimum=1)
keepalive = env_int("MIRROR_KEEPALIVE", 5, minimum=0)

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("MIRROR_LOG_LEVEL", "info")


def post_worker_init(worker):
    from app.services.push import start_poller

    worker.inbox_poller = start_poller(worker.wsgi)


def worker_exit(server, worker):
    from app.services.async_bridge import shutdown_async_bridge
    from app.services.push import stop_poller

    stop_poller(getattr(worker, "inbox_poller", None))
    shutdown_async_bridge()
