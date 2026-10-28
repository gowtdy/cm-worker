#!/bin/sh


PYTHONPATH=/app celery -A gpu_cover_tasks worker -Q us_cover_queue --pool=solo --pool=threads --concurrency=${CELERY_CONCURRENCY:-3} --loglevel=info
