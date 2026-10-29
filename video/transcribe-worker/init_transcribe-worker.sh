# 建立 transcribe-worker 所需宿主机目錄。啟動容器時建議傳入 AIGC_UID/AIGC_GID 與宿主机 aigc 對齊，例如：
#   export AIGC_UID=$(id -u aigc) AIGC_GID=$(id -g aigc)
#   docker compose -f transcribe-worker/docker-compose-whisper.yml up -d
# 未傳則 compose 使用預設 1001。
echo "set transcribe worker host directory permission..."

# 确保目录存在
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/whisper-transcribe-worker1-logs
sudo mkdir -p /home/aigc/data/aioutput/transcribe
sudo mkdir -p /home/aigc/data/aioutput/video/transcribe
sudo mkdir -p /home/aigc/data/ssh_control/whisper-transcribe-worker-1
sudo mkdir -p /home/aigc/data/video/tts

# 设置目录所有者为 aigc 用户
sudo chown -R aigc:aigc /home/aigc/data/aioutput/worker-logs/
sudo chown -R aigc:aigc /home/aigc/data/aioutput/transcribe
sudo chown -R aigc:aigc /home/aigc/data/aioutput/video/transcribe
sudo chown -R aigc:aigc /home/aigc/data/ssh_control
sudo chown -R aigc:aigc /home/aigc/data/video/tts

# 设置目录权限，确保可以写入
sudo chmod -R 755 /home/aigc/data/aioutput/
sudo chmod -R 755 /home/aigc/data/ssh_control

echo "permission set completed!"
echo "directory permission information:"
ls -la /home/aigc/data/aioutput/worker-logs/whisper-transcribe-worker1-logs
ls -la /home/aigc/data/aioutput/transcribe
ls -la /home/aigc/data/aioutput/video/transcribe
ls -la /home/aigc/data/ssh_control/whisper-transcribe-worker-1
ls -la /home/aigc/data/video
