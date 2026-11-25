echo "set us host directory permission..."

# 确保目录存在
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs/claude
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs/codex
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs/grok
# 设置目录所有者为 aigc 用户
sudo chown -R aigc:aigc /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs
# 设置目录权限，确保可以写入
sudo chmod -R 755 /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs

sudo mkdir -p /home/aigc/data/aioutput/htmlvideos
sudo chown -R aigc:aigc /home/aigc/data/aioutput/htmlvideos
sudo chmod -R 755 /home/aigc/data/aioutput/htmlvideos

sudo mkdir -p /home/aigc/data/ssh_control/htmlvideo-worker
sudo chown -R aigc:aigc /home/aigc/data/ssh_control/htmlvideo-worker
sudo chmod -R 755 /home/aigc/data/ssh_control/htmlvideo-worker

sudo mkdir -R /home/aigc/data/agent/gemini/antigravity-cli
sudo chown -R aigc:aigc /home/aigc/data/agent/gemini/antigravity-cli
sudo chmod -R 755 /home/aigc/data/agent/gemini/antigravity-cli

cp conf/agy_settings.json /home/aigc/data/agent/gemini/antigravity-cli/settings.json

echo "permission set completed!"
echo "directory permission information:"
ls -la /home/aigc/data/aioutput/worker-logs/htmlvideo-worker-logs
ls -la /home/aigc/data/aioutput/htmlvideos