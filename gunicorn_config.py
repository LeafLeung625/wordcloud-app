import multiprocessing

bind = "0.0.0.0:8000"
workers = 2
worker_class = "sync"
timeout = 120
keepalive = 5

# 不写入日志文件，直接输出到标准输出
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 禁用 worker 超时重启
max_requests = 1000
max_requests_jitter = 50
