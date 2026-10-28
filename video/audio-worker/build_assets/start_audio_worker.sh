#!/bin/sh


PYTHONPATH=/app celery -A audio_tasks worker -Q us_audio_queue --pool=solo --pool=threads --concurrency=${CELERY_CONCURRENCY:-3} --loglevel=info
