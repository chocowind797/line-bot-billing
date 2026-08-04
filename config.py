import os
import json
from dotenv import load_dotenv

# ==========================================
# 1. 基礎路徑設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
DATA_FILE_PATH = os.path.join(BASE_DIR, 'bindings.json')
SUBJECTS_FILE = os.path.join(BASE_DIR, 'subjects.json') # 新增：科目設定檔路徑

# ==========================================
# 2. 宣告全域變數 (先建立空的容器)
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = ""
LINE_CHANNEL_SECRET = ""
ADMIN_USER_IDS = []
SUBJECT_INFO = {}
ALL_TEACHER_IDS = set()

# ==========================================
# 3. 建立動態讀取函式
# ==========================================
def reload_config():
    """重新讀取 .env 與 subjects.json 並更新所有設定變數"""
    
    # override=True 非常重要，它會強制用 .env 的新資料覆蓋掉系統舊的記憶
    load_dotenv(override=True)

    # 1. 更新 LINE 金鑰
    global LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
    LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '')

    # 2. 更新管理員名單 (原地更新)
    admin_ids_str = os.getenv('ADMIN_USER_IDS', '')
    ADMIN_USER_IDS.clear() # 清空舊資料
    if admin_ids_str:
        ADMIN_USER_IDS.extend([uid.strip() for uid in admin_ids_str.split(',')]) # 塞入新資料

    # 3. 更新科目對照表與老師名單 (從 subjects.json 動態讀取)
    SUBJECT_INFO.clear()
    ALL_TEACHER_IDS.clear()
    
    if os.path.exists(SUBJECTS_FILE):
        with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
            try:
                raw_subjects = json.load(f)
                for sub_code, sub_data in raw_subjects.items():
                    # 組合出程式需要的 SUBJECT_INFO 格式，並自動連結對應的資料夾
                    SUBJECT_INFO[sub_code] = {
                        "name": sub_data.get("name", "未知科目"),
                        "teachers": sub_data.get("teacher_ids", []),
                        "folder": os.path.join(DATA_FOLDER, sub_data.get("folder", str(sub_code))),
                        "payment_info": sub_data.get("payment_info", "") # 順便帶入自訂付款資訊
                    }
                    # 將該科目的所有老師 ID 統整加入全域集合中，方便權限驗證
                    ALL_TEACHER_IDS.update(sub_data.get("teacher_ids", []))
            except json.JSONDecodeError:
                print(f"⚠️ {SUBJECTS_FILE} 格式錯誤，請檢查 JSON 語法！")
    else:
        print(f"⚠️ 找不到 {SUBJECTS_FILE}，請確認檔案是否存在！")

    print("✅ 設定檔、.env 與科目資料已重新載入並更新！")

# ==========================================
# 4. 程式第一次啟動時，自動執行一次讀取
# ==========================================
reload_config()