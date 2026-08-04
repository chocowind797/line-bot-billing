import os
from dotenv import load_dotenv

# ==========================================
# 1. 基礎路徑設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
DATA_FILE_PATH = os.path.join(BASE_DIR, 'bindings.json')

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
    """重新讀取 .env 並更新所有設定變數"""
    
    # override=True 非常重要，它會強制用 .env 的新資料覆蓋掉系統舊的記憶
    load_dotenv(override=True)

    # 1. 更新 LINE 金鑰 (通常不會在運作中改，但還是跟著更新)
    global LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
    LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '')

    # 2. 更新管理員名單 (原地更新)
    admin_ids_str = os.getenv('ADMIN_USER_IDS', '')
    ADMIN_USER_IDS.clear() # 清空舊資料
    if admin_ids_str:
        ADMIN_USER_IDS.extend([uid.strip() for uid in admin_ids_str.split(',')]) # 塞入新資料

    # 3. 取得各科老師名單
    sub1_ids_str = os.getenv('SUBJECT_1_TEACHER_IDS', '')
    sub1_teachers = [uid.strip() for uid in sub1_ids_str.split(',')] if sub1_ids_str else []

    # 4. 更新科目對照表 (原地更新)
    SUBJECT_INFO.clear()
    SUBJECT_INFO.update({
        "1": {
            "name": "物理",
            "teachers": sub1_teachers,
            "folder": os.path.join(DATA_FOLDER, "1")
        }
        # 未來擴充時，解除下方的註解，呼叫 reload_config() 就會瞬間生效！
        # "2": {
        #     "name": "數學",
        #     "teachers": [uid.strip() for uid in os.getenv('SUBJECT_2_TEACHER_IDS', '').split(',')],
        #     "folder": os.path.join(DATA_FOLDER, "2")
        # }
    })

    # 5. 更新「所有老師」的集合 (原地更新)
    ALL_TEACHER_IDS.clear()
    for sub_id, info in SUBJECT_INFO.items():
        ALL_TEACHER_IDS.update(info["teachers"])

    print("✅ 設定檔與 .env 已重新載入並更新！")

# ==========================================
# 4. 程式第一次啟動時，自動執行一次讀取
# ==========================================
reload_config()