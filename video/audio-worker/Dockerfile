FROM audio-extract-image:0.4

USER root

# 复制项目文件
COPY build_assets/ /app/

# 创建必要的目录结构（chown 由 base entrypoint 在 run 時依 AIGC_UID/AIGC_GID 處理）
RUN mkdir -p /app/log /app/output /app/data \
    /app/data/uploads \
    /app/data/sepaudio

# 给脚本执行权限
RUN chmod +x /app/start_audio_worker.sh

WORKDIR /app

# 使用 base 的 ENTRYPOINT ["/entrypoint.sh"]，僅覆蓋 CMD；entrypoint 會建立 aigc、chown 後 gosu aigc 執行 start_audio_worker.sh
CMD ["./start_audio_worker.sh"]