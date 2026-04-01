import urllib.request
import json

# Paste your actual values here
TOKEN   = "8700551412:AAFSo871PmoSaH5Ff1Lr_38eqeH3WwIFeTs"
CHAT_ID = "8528691104"

url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = json.dumps({"chat_id": CHAT_ID, "text": "Bot is working!"}).encode()
req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

try:
    resp = urllib.request.urlopen(req, timeout=10)
    print("SUCCESS:", resp.read().decode())
except Exception as e:
    print("ERROR:", e)
