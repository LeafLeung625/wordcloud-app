# gunicorn_config.py
import multiprocessing

bind = "0.0.0.0:5000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "gevent"
worker_connections = 1000
timeout = 120
keepalive = 5
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
