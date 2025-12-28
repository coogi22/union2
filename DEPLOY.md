# Deploying ShopBot to Digital Ocean

## 1. Connect to your Droplet
```bash
ssh root@your_droplet_ip
```

## 2. Update system & install Python
```bash
apt update && apt upgrade -y
apt install python3 python3-pip python3-venv git -y
```

## 3. Upload your bot files
Option A - Using SCP from your local machine:
```bash
scp -r ShopBot-main root@your_droplet_ip:/root/ShopBot
```

Option B - Using Git (if you have a repo):
```bash
cd /root
git clone https://github.com/yourusername/ShopBot.git
```

## 4. Set up virtual environment
```bash
cd /root/ShopBot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 5. Create your .env file
```bash
cp .env.example .env
nano .env
# Fill in all your actual values, then save with Ctrl+X, Y, Enter
```

## 6. Test the bot manually first
```bash
source venv/bin/activate
python main.py
```
If it works, press Ctrl+C to stop it.

## 7. Set up systemd service (auto-start & restart)
```bash
cp shopbot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable shopbot
systemctl start shopbot
```

## 8. Useful commands
```bash
# Check status
systemctl status shopbot

# View logs
journalctl -u shopbot -f

# Restart bot
systemctl restart shopbot

# Stop bot
systemctl stop shopbot
```

## Updating the bot
```bash
cd /root/ShopBot
systemctl stop shopbot
# Upload new files or git pull
systemctl start shopbot
