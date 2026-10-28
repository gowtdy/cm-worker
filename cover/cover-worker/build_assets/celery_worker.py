import os
from urllib.parse import quote_plus
from celery import Celery

# 从环境变量获取配置，提供默认值 - 与 .env_mqdb / audio-worker 对齐
RABBITMQ_USER = os.getenv('TTS_RABBITMQ_USER', 'aigc')
RABBITMQ_PASS = os.getenv('TTS_RABBITMQ_PASS', 'password')
RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.9')
RABBITMQ_PORT = os.getenv('TTS_RABBITMQ_PORT', '5672')

# URL编码密码中的特殊字符
encoded_password = quote_plus(RABBITMQ_PASS)

celery_app = Celery(
    "cover_worker",
    #broker="amqp://aigc:ygj%230508@host.docker.internal:5672//",
    broker=f"amqp://{RABBITMQ_USER}:{encoded_password}@{RABBITMQ_HOST}:{RABBITMQ_PORT}//",
    backend="rpc://"
)

celery_app.conf.task_routes = {
    'gpu_cover_tasks.gpu_cover': {'queue': 'gpu_cover_tasks'},
    'gpu_vocal_tasks.gpu_vocal': {'queue': 'gpu_vocal_tasks'},
    'gpu_instrum_tasks.gpu_instrum': {'queue': 'gpu_instrum_tasks'}
}
