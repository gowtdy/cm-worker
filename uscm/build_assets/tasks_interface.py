import os
from urllib.parse import quote_plus
from celery import Celery

# 从环境变量获取配置，提供默认值 - TTS队列配置
US_RABBITMQ_HOST = os.getenv('US_RABBITMQ_USER', '149.28.202.108')
US_RABBITMQ_PORT = os.getenv('US_RABBITMQ_PORT', '5672')
US_RABBITMQ_USER = os.getenv('US_MYSQL_USERNAME', 'aigc')
US_RABBITMQ_PASS = os.getenv('US_RABBITMQ_PASS', 'password')

# URL编码密码中的特殊字符
us_encoded_rabbitmq_pass = quote_plus(US_RABBITMQ_PASS)

# 队列1配置：TTS和Cover任务（共享一个队列）
us_celery_app = Celery(
    "ttscover_worker",
    broker=f"amqp://{US_RABBITMQ_USER}:{us_encoded_rabbitmq_pass}@{US_RABBITMQ_HOST}:{US_RABBITMQ_PORT}//",
    backend="rpc://"
)

us_celery_app.conf.task_routes = {
    'us_indextts25_tasks.*': {'queue': 'us_indextts25_tasks'},
    'us_tts_tasks.*': {'queue': 'us_tts_tasks'},
    'us_sound_tasks.*': {'queue': 'us_sound_tasks'},
    'us_cover_tasks.*': {'queue': 'us_cover_tasks'},
    'us_audio_tasks.*': {'queue': 'us_audio_tasks'},
    'us_transcribe_tasks.transcribe_whisper': {'queue': 'us_transcribe_whisper_tasks'}
}

# 队列1任务签名：TTS和Cover任务
gpu_gen_indextts25 = us_celery_app.signature('us_indextts25_tasks.us_gen_indextts25')
gpu_gen_tts = us_celery_app.signature('us_tts_tasks.us_gen_tts')
gpu_gen_sound = us_celery_app.signature('us_sound_tasks.us_gen_sound')
gpu_cover = us_celery_app.signature('us_cover_tasks.us_cover')
gpu_vocal = us_celery_app.signature('us_cover_tasks.us_vocal')
gpu_instrument = us_celery_app.signature('us_cover_tasks.us_instrument')
task_audio_extract = us_celery_app.signature('us_audio_tasks.extract_audio')
task_transcribe_whisper = us_celery_app.signature('us_transcribe_tasks.transcribe_whisper')
