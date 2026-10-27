FROM index-tts-image:0.6

USER root
# 安装系统依赖
#RUN apt-get update && apt-get install -y vim 

# 复制项目文件
RUN rm -rf /app/*
COPY build_assets/ /app/

# 创建必要的目录（chown 由 base entrypoint 在 run 時依 AIGC_UID/AIGC_GID 處理）
RUN mkdir -p /app/log /app/output /app/data /app/models /app/models/nltk_data /app/models/text

# 给脚本执行权限
RUN chmod +x /app/start_indextts_worker.sh

WORKDIR /app

# 使用 base 的 ENTRYPOINT ["/entrypoint.sh"]，僅覆蓋 CMD；entrypoint 會建立 aigc、chown 後 gosu aigc 執行 start_tts_worker.sh
CMD ["./start_indextts_worker.sh"]
