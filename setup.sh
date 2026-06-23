#!/bin/bash
set -e

echo "=== Auto Trader — VM Setup ==="

# System updates
sudo apt-get update -y && sudo apt-get upgrade -y
sudo apt-get install -y python3 python3-pip python3-venv git

# Clone repo
cd ~
git clone https://github.com/ranveersaini7687/Trading.git auto-trader
cd auto-trader

# Python virtualenv + dependencies
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Create .env for Twilio secrets (fill in your values)
cat > .env <<'EOF'
TWILIO_SID=your_twilio_sid_here
TWILIO_TOKEN=your_twilio_token_here
WHATSAPP_TO=+91xxxxxxxxxx
EOF

echo ""
echo ">>> Edit .env with your Twilio credentials:"
echo "    nano ~/auto-trader/.env"
echo ""

# Install and start systemd service
sudo cp auto-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable auto-trader
sudo systemctl start auto-trader

echo ""
echo "=== Done! ==="
echo "Check status : sudo systemctl status auto-trader"
echo "Live logs    : sudo journalctl -u auto-trader -f"
