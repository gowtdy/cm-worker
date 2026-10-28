echo "set sound worker host directory permission..."

# 确保目录存在
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/sound-worker1-logs
sudo mkdir -p /home/aigc/data/aioutput/worker-logs/sound-worker2-logs
sudo mkdir -p /home/aigc/data/aioutput/sounds/tk
sudo mkdir -p /home/aigc/data/ssh_control/sound-worker-1
sudo mkdir -p /home/aigc/data/ssh_control/sound-worker-2
sudo mkdir -p /home/aigc/data/ssh_control/sound-worker-preview

# 设置目录所有者为 aigc 用户
sudo chown -R aigc:aigc /home/aigc/data/aioutput/worker-logs/
sudo chown -R aigc:aigc /home/aigc/data/aioutput/sounds/
sudo chown -R aigc:aigc /home/aigc/data/ssh_control

# 设置目录权限，确保可以写入
sudo chmod -R 755 /home/aigc/data/aioutput/
sudo chmod -R 777 /home/aigc/data/aioutput/sounds/tk
sudo chmod -R 777 /home/aigc/data/aioutput/worker-logs/
sudo chmod -R 755 /home/aigc/data/ssh_control

echo "permission set completed!"
echo "directory permission information:"
ls -la /home/aigc/data/aioutput/worker-logs/
ls -la /home/aigc/data/aioutput/sounds/ 