import os
import json
import hashlib
from dotenv import load_dotenv
import secrets
import datetime

# ==========================================
# 基礎路徑設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'data')
DATA_FILE_PATH = os.path.join(BASE_DIR, 'bindings.json')
SUBJECTS_FILE = os.path.join(BASE_DIR, 'subjects.json') # 新增：科目設定檔路徑

# ==========================================
# 宣告全域變數 (先建立空的容器)
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = ""
LINE_CHANNEL_SECRET = ""
ADMIN_USER_IDS = []
SUBJECT_INFO = {}
ALL_TEACHER_IDS = set()

# ==========================================
# 建立動態讀取函式
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
                        "admin_teacher": sub_data.get("admin_teacher", ""),
                        "teachers": sub_data.get("teachers", []),
                        "folder": os.path.join(DATA_FOLDER, sub_data.get("folder", str(sub_code))),
                        "payment_info": sub_data.get("payment_info", "") # 順便帶入自訂付款資訊
                    }
                    # 將該科目的所有老師 ID 統整加入全域集合中，方便權限驗證
                    ALL_TEACHER_IDS.update(sub_data.get("teachers", []))
            except json.JSONDecodeError:
                print(f"⚠️ {SUBJECTS_FILE} 格式錯誤，請檢查 JSON 語法！")
    else:
        print(f"⚠️ 找不到 {SUBJECTS_FILE}，請確認檔案是否存在！")

    print("✅ 設定檔、.env 與科目資料已重新載入並更新！")

# ==========================================
# 程式第一次啟動時，自動執行一次讀取
# ==========================================
reload_config()

# ==========================================
# 老師可以在聊天室內修改payment_info
# ==========================================
def update_subject_payment_info(sub_code, new_payment_info, admin_user_ids):
    """更新指定科目的 payment_info 並寫回 subjects.json"""
    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
            
            if sub_code in raw_subjects:
                sub_data = raw_subjects[sub_code]
                # 權限檢查：必須是系統管理員，或是該科目的指定管理老師
                if user_id not in admin_user_ids and sub_data.get("admin_teacher") != user_id:
                    return False, "⚠️ 您不是該科目的管理老師，無權修改繳費說明。"
                
                sub_data["payment_info"] = new_payment_info
                with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
                reload_config()
                return True
        except Exception as e:
            print(f"更新 subjects.json 失敗: {e}")
    return False

# ==========================================
# 管理老師可以生成邀請金鑰
# ==========================================
def generate_invite_key(sub_code, user_id):
    """管理老師產生單次使用的金鑰"""
    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
            
            if sub_code in raw_subjects:
                # 驗證操作者是否為該科目的管理老師（或系統管理員）
                if raw_subjects[sub_code].get("admin_teacher") != user_id and user_id not in ADMIN_USER_IDS:
                    return None, "⚠️ 您不是該科目的管理老師，無權生成邀請金鑰。"
                
                # 產生一組隨機 6 位數或亂碼金鑰
                invite_key = secrets.token_hex(3).upper() # 例如：A3F9B2
                
                if "invite_keys" not in raw_subjects[sub_code]:
                    raw_subjects[sub_code]["invite_keys"] = {}
                
                # 儲存金鑰（可設定簡單的狀態）
                raw_subjects[sub_code]["invite_keys"][invite_key] = {
                    "created_at": str(datetime.datetime.now())
                }
                
                with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
                
                reload_config()
                return invite_key, None
        except Exception as e:
            print(f"產生金鑰失敗: {e}")
    return None, "❌ 系統錯誤，無法產生金鑰。"

# ==========================================
# 新老師可以在聊天室中輸入金鑰來加入學科
# ==========================================
def redeem_invite_key(user_id, invite_key):
    """新老師輸入金鑰來加入科目"""
    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
            
            target_sub_code = None
            for sub_code, sub_data in raw_subjects.items():
                keys = sub_data.get("invite_keys", {})
                if invite_key in keys:
                    target_sub_code = sub_code
                    break
            
            if not target_sub_code:
                return False, "⚠️ 無效的金鑰或已被使用。"
            
            sub_data = raw_subjects[target_sub_code]
            if "teachers" not in sub_data:
                sub_data["teachers"] = []
            
            # 檢查是否已經是該科目的老師
            if user_id in sub_data["teachers"] or user_id == sub_data.get("admin_teacher"):
                return False, "⚠️ 您已經是該科目的老師了，不需要重複加入。"
            
            # 將使用者加入一般老師名單
            sub_data["teachers"].append(user_id)
            
            # 💡 單次使用：用完即焚，從金鑰清單中刪除
            del sub_data["invite_keys"][invite_key]
            
            with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
            
            reload_config()
            return True, sub_data["name"]
            
        except Exception as e:
            print(f"兌換金鑰失敗: {e}")
    return False, "❌ 系統錯誤，無法驗證金鑰。"

# ==========================================
# 管理員可以在聊天室中建立新增學科的金鑰
# ==========================================
def generate_subject_creation_key(admin_user_id, admin_user_ids):
    """系統管理員產生一組用來新增學科的金鑰"""
    if admin_user_id not in admin_user_ids:
        return None, "⚠️ 只有系統管理員可以產生新學科金鑰。"
    
    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
        except Exception:
            raw_subjects = {}
    else:
        raw_subjects = {}

    # 確保 subjects.json 內有存放開通信鑰的容器（我們可以用一個特殊的 key 或是獨立的管理結構，例如在頂層或用獨立變數，此處我們在 subjects.json 中加入一個 "_subject_creation_keys" 欄位來管理）
    if "_subject_creation_keys" not in raw_subjects:
        raw_subjects["_subject_creation_keys"] = {}

    # 產生一組隨機金鑰
    sub_key = secrets.token_hex(3).upper() # 例如：C4F2A1
    raw_subjects["_subject_creation_keys"][sub_key] = {
        "created_at": str(datetime.datetime.now())
    }

    try:
        with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
        return sub_key, None
    except Exception as e:
        return None, f"❌ 儲存金鑰失敗: {e}"

# ==========================================
# 新老師可以在聊天室中輸入金鑰來新增學科並成為管理老師
# ==========================================
def create_new_subject_by_key(user_id, subject_key, subject_name):
    """驗證金鑰、自動計算簡短唯一編號、建立學科並指派管理老師"""
    if not os.path.exists(SUBJECTS_FILE):
        return False, "⚠️ 找不到 subjects.json 檔案。"

    try:
        with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
            raw_subjects = json.load(f)
    except Exception:
        return False, "⚠️ 讀取 subjects.json 失敗。"

    creation_keys = raw_subjects.get("_subject_creation_keys", {})
    if subject_key not in creation_keys:
        return False, "⚠️ 無效的新學科金鑰或已被使用。"

    # 1. 根據金鑰與學科名稱，計算出一個簡短且唯一的科目編號（例如取 MD5 前 4 碼轉為數字或保留英文數字短碼）
    # 為了確保它是唯一的且短，我們檢查是否已存在，若重複則加上微調
    base_str = subject_key + subject_name
    hash_obj = hashlib.md5(base_str.encode('utf-8'))
    # 取前 3 碼作為簡短代碼（例如 'a2f' 或轉數字，這裡我們直接用英數字短碼，確保家長好輸入）
    sub_code = hash_obj.hexdigest()[:3].lower()

    # 確保編號在現有科目中絕對唯一
    while sub_code in raw_subjects or sub_code == "_subject_creation_keys":
        # 如果不小心重複，就多取一碼
        sub_code = hashlib.md5((base_str + sub_code).encode('utf-8')).hexdigest()[:4].lower()

    # 2. 建立新學科結構
    raw_subjects[sub_code] = {
        "name": subject_name,
        "folder_name": sub_code, # 對應資料夾名稱
        "admin_teacher": user_id, # 建立者成為管理老師
        "teachers": [user_id],    # 同時加入一般老師名單
        "invite_keys": {},
        "payment_info": "匯款完成後再請您通知我一下，謝謝！"
    }

    # 3. 銷毀用過的新學科金鑰（單次使用）
    del raw_subjects["_subject_creation_keys"][subject_key]

    try:
        with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
        
        # 重新載入設定
        reload_config()
        return True, sub_code
    except Exception as e:
        return False, f"❌ 寫入新學科失敗: {e}"
