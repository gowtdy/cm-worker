FROM htmlframes-image:0.1

#ENV DEBIAN_FRONTEND=noninteractive

# 临时切换到 root 用户来处理文件权限
USER root

# 创建/app目录
RUN mkdir -p /app
COPY build_assets/ /app/

# 创建 log 目录并设置权限
RUN mkdir -p /app/log /app/output /app/data

RUN chmod -R 755 /app
RUN chmod -R 777 /app/log /app/output /app/data

# 给脚本执行权限
RUN chmod +x /app/start_htmlvideo-worker.sh

# 设置Python用户安装路径到PATH
ENV PATH="/home/aigc/.local/bin:$PATH"

WORKDIR /app

#ENTRYPOINT ["tail", "-f", "/dev/null"]
CMD ["./start_htmlvideo-task.sh"]
