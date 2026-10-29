ARG TRANS_ENGINE=whisper
ARG IMG_TAG=0.1
FROM ${TRANS_ENGINE}-transcribe-image:${IMG_TAG}

USER root

RUN rm -rf /app/*
COPY build_assets/ /app/

RUN mkdir -p /app/log /app/output /app/data/uploads /app/data/transcribe /app/models/whisper-model

RUN chmod +x /app/start_transcribe_worker.sh

WORKDIR /app

# base 的 ENTRYPOINT ["/entrypoint.sh"] 会建 aigc、chown 后 gosu aigc 执行 CMD
CMD ["./start_transcribe_worker.sh"]