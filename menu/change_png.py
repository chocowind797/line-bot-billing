import os
import requests
from dotenv import load_dotenv

load_dotenv()

access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

# 請填入你的 Rich Menu ID 以及你的圖片檔名（例如 my_menu.png）
rich_menu_id = "richmenu-df529b5cbed3f11d3b1ac4e950619a9c"
image_path = "teacher_menu.png"

url = f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content"
headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "image/png"}

if os.path.exists(image_path):
  with open(image_path, "rb") as image_file:
    response = requests.post(url, headers=headers, data=image_file)
    print("狀態碼:", response.status_code)
    print("回應內容:", response.text)
else:
  print(f"找不到圖片檔案: {image_path}")