import os
from urllib.parse import quote_plus
from celery import Celery

# 从环境变量获取配置，提供默认值 - Sound队列配置
SOUND_RABBITMQ_USER = os.getenv('TTS_RABBITMQ_USER', 'aigc')
SOUND_RABBITMQ_PASS = os.getenv('TTS_RABBITMQ_PASS', 'password')
#SOUND_RABBITMQ_HOST = os.getenv('SOUND_RABBITMQ_HOST', '192.168.3.83')
SOUND_RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.9')
#SOUND_RABBITMQ_HOST = os.getenv('SOUND_RABBITMQ_HOST', '172.17.0.1')
SOUND_RABBITMQ_PORT = os.getenv('TTS_RABBITMQ_PORT', '5672')

# URL编码密码中的特殊字符
SOUND_encoded_password = quote_plus(SOUND_RABBITMQ_PASS)

celery_app = Celery(
    "sound_worker",
    #broker="amqp://aigc:ygj%230508@host.docker.internal:5672//",
    broker=f"amqp://{SOUND_RABBITMQ_USER}:{SOUND_encoded_password}@{SOUND_RABBITMQ_HOST}:{SOUND_RABBITMQ_PORT}//",
    backend="rpc://"
)

celery_app.conf.task_routes = {
    #'gpu_tasks.gpu_gen_sound': {'queue': 'tiktokvoice_tasks'}
    'gpu_sound_tasks.gpu_gen_sound': {'queue': 'gpu_sound_tasks'}
}

# 导入任务模块，确保任务被注册
import gpu_sound_tasks
