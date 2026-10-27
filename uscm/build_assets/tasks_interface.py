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

# 队列配置
us_celery_app = Celery(
    "uscm_producer",
    broker=f"amqp://{US_RABBITMQ_USER}:{us_encoded_rabbitmq_pass}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//",
    backend="rpc://"
)

us_celery_app.conf.task_routes = {
    'task_gen_indextts': {'queue': 'us_indextts_queue'},
    'task_gen_tts': {'queue': 'us_tts_queue'},
    'task_gen_sound': {'queue': 'us_sound_queue'},
    'task_cover.*': {'queue': 'us_cover_queue'},
    'task_audio_extract': {'queue': 'us_audio_queue'},
    'task_transcribe_whisper': {'queue': 'us_transcribe_whisper_queue'}
}

# 任务签名
gpu_gen_indextts = us_celery_app.signature('task_gen_indextts')
gpu_gen_tts = us_celery_app.signature('task_gen_tts')
gpu_gen_sound = us_celery_app.signature('task_gen_sound')
gpu_cover = us_celery_app.signature('task_cover.cover')
gpu_vocal = us_celery_app.signature('task_cover.vocal')
gpu_instrument = us_celery_app.signature('task_cover.instrument')
audio_extract = us_celery_app.signature('task_audio_extract')
transcribe_whisper = us_celery_app.signature('task_transcribe_whisper')
