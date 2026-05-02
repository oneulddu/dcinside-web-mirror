import multiprocessing
import os

from env_loader import env_int, load_dotenv

load_dotenv()


bind = os.getenv("MIRROR_BIND", "[::]:6100")
workers = env_int("MIRROR_WORKERS", max(multiprocessing.cpu_count() * 2 + 1, 2), minimum=1)
threads = env_int("MIRROR_THREADS", 2, minimum=1)
timeout = env_int("MIRROR_TIMEOUT", 60, minimum=1)
graceful_timeout = env_int("MIRROR_GRACEFUL_TIMEOUT", 30, minimum=1)
keepalive = env_int("MIRROR_KEEPALIVE", 5, minimum=0)

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("MIRROR_LOG_LEVEL", "info")
