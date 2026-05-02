module.exports = {
  apps: [
    {
      name: 'dc-mirror',
      script: './.venv/bin/gunicorn',
      args: '-c gunicorn.conf.py wsgi:app',
      interpreter: 'none',
      cwd: '/home/ubuntu/mirror',
      watch: false,
      ignore_watch: ['.git', 'logs', '__pycache__', '*.pyc', '.venv', 'venv', 'legacy', 'instance'],
      autorestart: true,
      max_restarts: 20,
      restart_delay: 1000,
      env: {
        MIRROR_BIND: '0.0.0.0:6100'
      }
    }
  ]
};
