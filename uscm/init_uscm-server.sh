echo "set us host directory permission..."

# 确保目录存在
sudo mkdir -p /home/aigc/data/aioutput/uscm-server-logs

# 设置目录所有者为 aigc 用户
sudo chown -R aigc:aigc /home/aigc/data/aioutput/uscm-server-logs

# 设置目录权限，确保可以写入
sudo chmod -R 755 /home/aigc/data/aioutput/uscm-server-logs

echo "permission set completed!"
echo "directory permission information:"
ls -la /home/aigc/data/aioutput/uscm-server-logs