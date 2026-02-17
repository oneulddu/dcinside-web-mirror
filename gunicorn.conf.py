import multiprocessing
import os


bind = os.getenv("MIRROR_BIND", "[::]:6100")
workers = int(os.getenv("MIRROR_WORKERS", str(max(multiprocessing.cpu_count() * 2 + 1, 2))))
threads = int(os.getenv("MIRROR_THREADS", "2"))
timeout = int(os.getenv("MIRROR_TIMEOUT", "60"))
graceful_timeout = int(os.getenv("MIRROR_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("MIRROR_KEEPALIVE", "5"))

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("MIRROR_LOG_LEVEL", "info")
