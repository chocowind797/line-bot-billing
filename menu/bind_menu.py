import os
import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
teacher_ids_raw = os.getenv('TEACHER_USER_IDS', '')
TEACHER_RICH_MENU_ID = os.getenv('TEACHER_RICH_MENU_ID', '')
TEACHER_USER_IDS = [uid.strip() for uid in teacher_ids_raw.split(',') if uid.strip()]

# 動態從 .env 讀取 ID 並綁定 Rich Menu 給使用者
def set_user_rich_menu(user_id):
  if not ACCESS_TOKEN:
    return

  for teacher in user_id:
    url = f'https://api.line.me/v2/bot/user/{teacher}/richmenu/{TEACHER_RICH_MENU_ID}'
    headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}

    try:
      response = requests.post(url, headers=headers)
      if response.status_code != 200:
        print(f'綁定 Rich Menu 失敗: {response.status_code}, 回應: {response.text}')
    except Exception as e:
      print(f'設定 Rich Menu 發生錯誤: {e}')

set_user_rich_menu(TEACHER_USER_IDS)