import os
from urllib.parse import quote_plus
from celery import Celery

# 从环境变量获取配置，提供默认值 - TTS队列配置（Cover任务也使用TTS队列）
RABBITMQ_USER = os.getenv('TTS_RABBITMQ_USER', 'aigc')
RABBITMQ_PASS = os.getenv('TTS_RABBITMQ_PASS', 'password')
#TTS_RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.83')
RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.9')
RABBITMQ_PORT = os.getenv('TTS_RABBITMQ_PORT', '5672')

# URL编码密码中的特殊字符
encoded_password = quote_plus(RABBITMQ_PASS)

celery_app = Celery(
    "audio_worker",
    #broker="amqp://aigc:ygj%230508@host.docker.internal:5672//",
    broker=f"amqp://{RABBITMQ_USER}:{encoded_password}@{RABBITMQ_HOST}:{RABBITMQ_PORT}//",
    backend="rpc://"
)

celery_app.conf.task_routes = {
    'audio_tasks.extract_audio': {'queue': 'audio_tasks'},
}
