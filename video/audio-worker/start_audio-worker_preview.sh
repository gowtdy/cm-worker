# 與宿主机 aigc UID/GID 對齊；未設定則從 host 的 aigc 取得，無 aigc 則預設 1001
export AIGC_UID=${AIGC_UID:-$(id -u aigc 2>/dev/null || echo 1000)}
export AIGC_GID=${AIGC_GID:-$(id -g aigc 2>/dev/null || echo 1000)}
echo "use AIGC_UID=${AIGC_UID} AIGC_GID=${AIGC_GID}"
docker compose -f docker-compose-preview.yml up -d