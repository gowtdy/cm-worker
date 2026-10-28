FROM stable-sound-image:0.1

#ENV DEBIAN_FRONTEND=noninteractive

# 临时切换到 root 用户来安装系统依赖
USER root

# 复制项目文件
RUN rm -rf /app/*
COPY build_assets/ /app/

# 创建必要的目录（chown 由 base 的 entrypoint 在 run 時依 AIGC_UID/AIGC_GID 處理）
RUN mkdir -p /app/log /app/output /app/data /app/models

# 目录权限（entrypoint 會 chown -R aigc:aigc /app，此處僅設定 mode）
RUN chmod -R 755 /app
RUN chmod -R 777 /app/output /app/log

# 给脚本执行权限
RUN chmod +x /app/start_worker.sh

WORKDIR /app

ENV PYTHONUNBUFFERED=1
RUN pip install --progress-bar=raw -v -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 使用 base 的 ENTRYPOINT ["/entrypoint.sh"]，僅覆蓋 CMD；entrypoint 會建立 aigc、chown 後 gosu aigc 執行 start_worker.sh
CMD ["./start_worker.sh"]
