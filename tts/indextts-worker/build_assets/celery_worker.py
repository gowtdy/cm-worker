import os
from urllib.parse import quote_plus
from celery import Celery

# 从环境变量获取配置，提供默认值 - TTS队列配置
TTS_RABBITMQ_USER = os.getenv('TTS_RABBITMQ_USER', 'aigc')
TTS_RABBITMQ_PASS = os.getenv('TTS_RABBITMQ_PASS', 'password')
#TTS_RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.83')
TTS_RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.9')
TTS_RABBITMQ_PORT = os.getenv('TTS_RABBITMQ_PORT', '5672')

# URL编码密码中的特殊字符
TTS_encoded_password = quote_plus(TTS_RABBITMQ_PASS)

celery_app = Celery(
    "indextts25_worker",
    #broker="amqp://aigc:ygj%230508@host.docker.internal:5672//",
    broker=f"amqp://{TTS_RABBITMQ_USER}:{TTS_encoded_password}@{TTS_RABBITMQ_HOST}:{TTS_RABBITMQ_PORT}//",
    backend="rpc://"
)

celery_app.conf.task_routes = {
    'gpu_indextts25_tasks.gpu_gen_indextts25': {'queue': 'gpu_indextts25_tasks'}
}
