import os

# The HTTPS proxy is the sole public listener. Do not expose this port publicly.
bind = '127.0.0.1:8080'
workers = int(os.environ.get('WASH_WORKERS', '2'))
threads = 2
timeout = 60
graceful_timeout = 30
errorlog = '-'
accesslog = None  # Avoid logging beneficiary URLs or form values.
forwarded_allow_ips = '127.0.0.1,::1'
