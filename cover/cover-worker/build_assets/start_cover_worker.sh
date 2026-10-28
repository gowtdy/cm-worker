#!/bin/sh


PYTHONPATH=/app celery -A gpu_cover_tasks worker -Q gpu_cover_tasks --pool=solo --pool=threads --concurrency=${CELERY_CONCURRENCY:-3} --loglevel=info
