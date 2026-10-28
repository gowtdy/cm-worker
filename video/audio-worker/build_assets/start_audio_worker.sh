#!/bin/sh


PYTHONPATH=/app celery -A audio_tasks worker -Q audio_tasks --pool=solo --pool=threads --concurrency=${CELERY_CONCURRENCY:-3} --loglevel=info
