FROM htmlframes-image:0.1

#ENV DEBIAN_FRONTEND=noninteractive

# 临时切换到 root 用户来处理文件权限
USER root

# 创建/app目录
RUN mkdir -p /app
COPY build_assets/ /app/

# 创建 log 目录并设置权限
RUN mkdir -p /app/log /app/log/claude /app/log/codex /app/log/grok /app/output /app/data

RUN chmod -R 755 /app
RUN chmod -R 777 /app/log /app/log/claude /app/log/codex /app/log/grok /app/output /app/data

# 给脚本执行权限
RUN chmod +x /app/start_htmlvideo-task.sh

# 安装 HyperFrames agent skills（Claude Code 从 /app/.claude/skills/ 加载）
# 直接拷贝本地 skills，避免 npx skills add 在容器构建时非零退出
RUN mkdir -p /app/.claude/skills \
    && for skill in /app/skills/*/; do \
         name="$(basename "$skill")"; \
         [ "$name" = "_meta" ] && continue; \
         cp -a "$skill" "/app/.claude/skills/"; \
       done

# 设置Python用户安装路径到PATH
ENV PATH="/home/aigc/.local/bin:$PATH"

WORKDIR /app

#ENTRYPOINT ["tail", "-f", "/dev/null"]
CMD ["./start_htmlvideo-task.sh"]
