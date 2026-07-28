import os
import requests
from dotenv import load_dotenv

load_dotenv()

access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
url = "https://api.line.me/v2/bot/richmenu"

headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json",
}

data = {
    "size": {"width": 2500, "height": 843},
    "selected": True,
    "name": "Teacher Rich Menu",
    "chatBarText": "老師功能選單",
    "areas": [
        {
            "bounds": {"x": 0, "y": 0, "width": 833, "height": 843},
            "action": {"type": "message", "label": "查看待審核", "text": "查看待審核"},
        },
        {
            "bounds": {"x": 833, "y": 0, "width": 834, "height": 843},
            "action": {
                "type": "uri",
                "label": "無功能",
                "uri": "https://example.com",  # 點擊後不會有任何動作或跳轉
            },
        },
        {
            "bounds": {"x": 1667, "y": 0, "width": 833, "height": 843},
            "action": {"type": "message", "label": "發送帳單", "text": "發送帳單"},
        },
    ],
}

response = requests.post(url, headers=headers, json=data)
print("狀態碼:", response.status_code)
print("回應內容:", response.text)