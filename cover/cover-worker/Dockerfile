FROM coverimage:0.4

USER root
# 安装系统依赖
#RUN apt-get update && apt-get install -y vim 

# 复制项目文件
COPY build_assets/ /app/

# 创建必要的目录结构（chown 由 base entrypoint 在 run 時依 AIGC_UID/AIGC_GID 處理）
RUN mkdir -p /app/log /app/output /app/data \
    /app/assets/rmvpe \
    /app/assets/hubert \
    /app/assets/uvr5_weights \
    /app/assets/pretrained_v2 \
    /app/assets/pretrained \
    /app/models/service_models \
    /app/models/service_model/common_models \
    /app/data/uploads \
    /app/data/middle \
    /app/data/final \
    /app/data/sepaudio \
    /app/data/sepvocal \
    /app/data/sepinstrum

# 给脚本执行权限
RUN chmod +x /app/start_cover_worker.sh

WORKDIR /app

# 使用 base 的 ENTRYPOINT ["/entrypoint.sh"]，僅覆蓋 CMD；entrypoint 會建立 aigc、chown 後 gosu aigc 執行 start_cover_worker.sh
CMD ["./start_cover_worker.sh"]
