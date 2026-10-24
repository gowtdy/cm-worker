#!/bin/bash
# 清理可能存在的旧容器
docker-compose -f docker-compose.yml down --remove-orphans 2>/dev/null || true
# 启动服务
docker-compose -f docker-compose.yml up -d