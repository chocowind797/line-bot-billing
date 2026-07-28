import os
import requests
from dotenv import load_dotenv

load_dotenv()

access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")


def unlink_rich_menu_from_user(user_id):
  url = f"https://api.line.me/v2/bot/user/{user_id}/richmenu"
  headers = {"Authorization": f"Bearer {access_token}"}

  # 該 API 使用 DELETE 方法
  response = requests.delete(url, headers=headers)

  if response.status_code == 200:
    print(f"成功解除使用者 {user_id} 的圖文選單綁定")
    return True
  else:
    print(f"解除綁定失敗，狀態碼: {response.status_code}, 回應: {response.text}")
    return False


if __name__ == "__main__":
  # 測試範例（請填入實際的 LINE User ID）
  target_user_id = "填入對應的LINE_USER_ID"
  unlink_rich_menu_from_user(target_user_id)