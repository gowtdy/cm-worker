import os
from urllib.parse import quote_plus
from celery import Celery

# 从环境变量获取配置，提供默认值 - TTS队列配置
# ussv-db-vultr
US_RABBITMQ_HOST = os.getenv('US_RABBITMQ_HOST', '149.28.202.108')
US_RABBITMQ_PORT = os.getenv('US_RABBITMQ_PORT', '5672')
US_RABBITMQ_USER = os.getenv('US_RABBITMQ_USER', 'aigc')
US_RABBITMQ_PASS = os.getenv('US_RABBITMQ_PASS', 'password')

# URL编码密码中的特殊字符
us_encoded_rabbitmq_pass = quote_plus(US_RABBITMQ_PASS)

celery_app = Celery(
    "indextts_worker",
    #broker="amqp://aigc:ygj%230508@host.docker.internal:5672//",
    broker=f"amqp://{US_RABBITMQ_USER}:{us_encoded_rabbitmq_pass}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//",
    backend="rpc://"
)

celery_app.conf.task_routes = {
    'task_gen_indextts': {'queue': 'us_indextts_tasks'}
}
