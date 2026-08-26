module.exports = {
  apps: [
    {
      name: 'dc-mirror',
      script: './.venv/bin/gunicorn',
      args: '-c gunicorn.conf.py wsgi:app',
      interpreter: 'none',
      cwd: '/home/ubuntu/mirror',
      watch: false,
      ignore_watch: ['.git', 'logs', '__pycache__', '*.pyc', '.venv', 'venv', 'instance'],
      autorestart: true,
      max_restarts: 20,
      restart_delay: 1000,
      env: {
        MIRROR_BIND: '0.0.0.0:6100',
        // One process keeps cache, cooldown and single-flight state server-wide.
        // Threads retain concurrent request capacity for this I/O-bound service.
        MIRROR_WORKERS: '1',
        MIRROR_THREADS: '12',
        MIRROR_DC_RATE_LIMIT_COOLDOWN: '30',
        MIRROR_READ_CACHE_TTL: '30',
        MIRROR_READ_STALE_TTL: '300',
        MIRROR_READ_FETCH_TIMEOUT: '50',
        MIRROR_READ_SINGLEFLIGHT_TIMEOUT: '55'
      }
    }
  ]
};
