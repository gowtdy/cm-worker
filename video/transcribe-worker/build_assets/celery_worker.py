import os
from urllib.parse import quote_plus
from celery import Celery

RABBITMQ_USER = os.getenv('TTS_RABBITMQ_USER', 'aigc')
RABBITMQ_PASS = os.getenv('TTS_RABBITMQ_PASS', 'password')
RABBITMQ_HOST = os.getenv('TTS_RABBITMQ_HOST', '192.168.3.9')
RABBITMQ_PORT = os.getenv('TTS_RABBITMQ_PORT', '5672')

encoded_password = quote_plus(RABBITMQ_PASS)

celery_app = Celery(
    "transcribe_worker",
    broker=f"amqp://{RABBITMQ_USER}:{encoded_password}@{RABBITMQ_HOST}:{RABBITMQ_PORT}//",
    backend="rpc://",
)

celery_app.conf.task_routes = {
    'gpu_transcribe_tasks.transcribe_whisper': {'queue': 'gpu_transcribe_whisper_tasks'},
    # future:
    # 'gpu_transcribe_tasks.transcribe_funasr': {'queue': 'gpu_transcribe_funasr_tasks'},
    # 'gpu_transcribe_tasks.transcribe_parakeet': {'queue': 'gpu_transcribe_parakeet_tasks'},
}