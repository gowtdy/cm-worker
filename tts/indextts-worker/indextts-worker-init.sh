# 建立 tts-worker 所需宿主机目錄。啟動容器時建議傳入 AIGC_UID/AIGC_GID 與宿主机 aigc 對齊，例如：
#   export AIGC_UID=$(id -u aigc) AIGC_GID=$(id -g aigc)
#   docker compose -f tts-worker/docker-compose.yml up -d
# 未傳則 compose 使用預設 1001。
echo "set tts worker host directory permission..."

# 确保目录存在
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/indextts-worker1-logs
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/indextts-worker2-logs
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/indextts-worker3-logs
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/indextts-worker-preview-logs
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/indextts-worker-5070ti-logs
sudo mkdir -p /home/aigc/data/aioutput/wavcl
sudo mkdir -p /home/aigc/data/ssh_control/indextts-worker-1
sudo mkdir -p /home/aigc/data/ssh_control/indextts-worker-2
sudo mkdir -p /home/aigc/data/ssh_control/indextts-worker-3
sudo mkdir -p /home/aigc/data/ssh_control/indextts-worker-preview
sudo mkdir -p /home/aigc/data/ssh_control/indextts-worker-5070ti
# 设置目录所有者为 aigc 用户
sudo chown -R aigc:aigc /home/aigc/data/aioutput/worker-logs/
sudo chown -R aigc:aigc /home/aigc/data/aioutput/wavcl
sudo chown -R aigc:aigc /home/aigc/data/ssh_control
# 设置目录权限，确保可以写入
sudo chmod -R 755 /home/aigc/data/aioutput/
sudo chmod -R 755 /home/aigc/data/ssh_control

echo "permission set completed!"
echo "directory permission information:"
ls -la /home/aigc/data/aioutput/worker-logs/
ls -la /home/aigc/data/aioutput/wavcl/ 