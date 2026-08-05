import os
import json
import hashlib
from dotenv import load_dotenv
import secrets
import datetime
import shutil

# ==========================================
# 基礎路徑設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, 'records')
CONFIG_FOLDER = os.path.join(BASE_DIR, 'config')
STAGING_FOLDER = os.path.join(BASE_DIR, 'staging')
DATA_FILE_PATH = os.path.join(CONFIG_FOLDER, 'bindings.json') # 家長綁定檔路徑
SUBJECTS_FILE = os.path.join(CONFIG_FOLDER, 'subjects.json') # 科目設定檔路徑
KEYS_FILE = os.path.join(CONFIG_FOLDER, 'keys.json') # 金鑰設定檔路徑
PENDING_FILE = os.path.join(STAGING_FOLDER, 'pending_bindings.json') # 金鑰設定檔路徑
STATE_FILE = os.path.join(STAGING_FOLDER, 'temp_states.json')

# --------------------------
# 暫存檔命名格式設定
# --------------------------
TEMP_FILE_FORMAT = "temp_{msg_id}.xlsx"

# 確保資料夾存在
os.makedirs(DATA_FOLDER, exist_ok=True)
os.makedirs(CONFIG_FOLDER, exist_ok=True)
os.makedirs(STAGING_FOLDER, exist_ok=True)

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
                        "payment_info": sub_data.get("payment_info", ""), # 順便帶入自訂付款資訊
                        "created_date": sub_data.get("created_date", "未知日期")
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
def update_subject_payment_info(user_id, sub_code, new_payment_info):
    """更新指定科目的 payment_info 並寫回 subjects.json"""
    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
            
            if sub_code in raw_subjects:
                sub_data = raw_subjects[sub_code]
                # 權限檢查：必須是系統管理員，或是該科目的指定管理老師
                if user_id not in ADMIN_USER_IDS and sub_data.get("admin_teacher") != user_id:
                    return False, "⚠️ 您不是該科目的管理老師，無權修改繳費說明。"
                
                sub_data["payment_info"] = new_payment_info
                with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
                reload_config()
                return True, None
        except Exception as e:
            print(f"更新 subjects.json 失敗: {e}")
    return False, "subjects.json不存在"

# ==========================================
# 管理老師可以生成邀請金鑰
# ==========================================
def generate_invite_key(sub_code, user_id):
    """管理老師產生單次使用的老師邀請金鑰（存放在 keys.json）"""
    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
            
            if sub_code in raw_subjects:
                # 驗證操作者是否為該科目的管理老師（或系統管理員）
                if raw_subjects[sub_code].get("admin_teacher") != user_id and user_id not in ADMIN_USER_IDS:
                    return None, "⚠️ 您不是該科目的管理老師，無權生成邀請金鑰。"
                
                invite_key = secrets.token_hex(3).upper()
                
                keys_data = {}
                if os.path.exists(KEYS_FILE):
                    try:
                        with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                            keys_data = json.load(f)
                    except Exception:
                        keys_data = {}
                
                if "teacher_invite_keys" not in keys_data:
                    keys_data["teacher_invite_keys"] = {}
                
                # 記錄這組金鑰對應到哪一個學科
                keys_data["teacher_invite_keys"][invite_key] = {
                    "sub_code": sub_code,
                    "created_at": str(datetime.datetime.now())
                }
                
                with open(KEYS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(keys_data, f, ensure_ascii=False, indent=4)
                
                return invite_key, None
        except Exception as e:
            print(f"產生金鑰失敗: {e}")
    return None, "❌ 系統錯誤，無法產生金鑰。"

# ==========================================
# 管理老師可以刪除老師
# ==========================================
def remove_teacher_from_subject(sub_code, target_teacher_id, admin_user_id, admin_user_ids):
    """管理老師將某位老師從指定學科的 teachers 名單中移除"""
    if not os.path.exists(SUBJECTS_FILE):
        return False, "⚠️ 找不到 subjects.json 檔案。"

    try:
        with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
            raw_subjects = json.load(f)
        
        if sub_code not in raw_subjects:
            return False, f"⚠️ 找不到代碼為【{sub_code}】的學科。"

        sub_data = raw_subjects[sub_code]
        sub_name = sub_data.get("name", sub_code)

        # 權限檢查：必須是系統管理員，或是該科目的指定管理老師
        if admin_user_id not in admin_user_ids and sub_data.get("admin_teacher") != admin_user_id:
            return False, "⚠️ 您不是該科目的管理老師，無權移除老師。"

        teachers_list = sub_data.get("teachers", [])
        
        if target_teacher_id not in teachers_list:
            return False, "⚠️ 該使用者本來就不在此學科的老師名單中。"
            
        # 不允許管理老師把自己從 teachers 裡面直接拔掉（需透過刪除學科或轉移管理權）
        if target_teacher_id == sub_data.get("admin_teacher"):
            return False, "⚠️ 不能移除學科的管理老師本人。"

        # 從名單中移除
        teachers_list.remove(target_teacher_id)
        sub_data["teachers"] = teachers_list

        # 寫回檔案
        with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_subjects, f, ensure_ascii=False, indent=4)

        # 重新載入設定
        reload_config()
        return True, sub_name
    except Exception as e:
        return False, f"❌ 移除老師失敗: {e}"

# ==========================================
# 新老師可以在聊天室中輸入金鑰來加入學科
# ==========================================
def redeem_invite_key(user_id, invite_key):
    """新老師輸入金鑰來加入科目（成功後回傳：是否成功, 科目名稱或錯誤訊息, 管理老師ID）"""
    if not os.path.exists(KEYS_FILE) or not os.path.exists(SUBJECTS_FILE):
        return False, "⚠️ 找不到設定檔或金鑰檔。", None

    try:
        with open(KEYS_FILE, 'r', encoding='utf-8') as f:
            keys_data = json.load(f)
        
        teacher_keys = keys_data.get("teacher_invite_keys", {})
        if invite_key not in teacher_keys:
            return False, "⚠️ 無效的金鑰或已被使用。", None
        
        target_sub_code = teacher_keys[invite_key].get("sub_code")

        with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
            raw_subjects = json.load(f)
        
        if target_sub_code not in raw_subjects:
            return False, "⚠️ 該金鑰對應的學科已不存在。", None
        
        sub_data = raw_subjects[target_sub_code]
        if "teachers" not in sub_data:
            sub_data["teachers"] = []
        
        if user_id in sub_data["teachers"] or user_id == sub_data.get("admin_teacher"):
            return False, "⚠️ 您已經是該科目的老師了，不需要重複加入。", None
        
        # 將新老師加入名單
        sub_data["teachers"].append(user_id)
        admin_teacher_id = sub_data.get("admin_teacher") # 取得管理老師 ID
        
        # 單次使用：從 keys.json 中刪除該金鑰
        del keys_data["teacher_invite_keys"][invite_key]
        
        # 寫回 keys.json
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, ensure_ascii=False, indent=4)
            
        # 寫回 subjects.json
        with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
        
        reload_config()
        return True, sub_data["name"], admin_teacher_id
        
    except Exception as e:
        print(f"兌換金鑰失敗: {e}")
    return False, "❌ 系統錯誤，無法驗證金鑰。", None

# ==========================================
# 管理員可以在聊天室中建立新增學科的金鑰
# ==========================================
def generate_subject_creation_key(admin_user_id, admin_user_ids):
    """系統管理員產生一組用來新增學科的金鑰（存放在 keys.json）"""
    if admin_user_id not in admin_user_ids:
        return None, "⚠️ 只有系統管理員可以產生新學科金鑰。"
    
    keys_data = {}
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                keys_data = json.load(f)
        except Exception:
            keys_data = {}

    if "_subject_creation_keys" not in keys_data:
        keys_data["_subject_creation_keys"] = {}

    sub_key = secrets.token_hex(3).upper() # 例如：C4F2A1
    keys_data["_subject_creation_keys"][sub_key] = {
        "created_at": str(datetime.datetime.now())
    }

    try:
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, ensure_ascii=False, indent=4)
        return sub_key, None
    except Exception as e:
        return None, f"❌ 儲存金鑰失敗: {e}"

# ==========================================
# 新老師可以在聊天室中輸入金鑰來新增學科並成為管理老師
# ==========================================
def create_new_subject_by_key(user_id, subject_key, subject_name):
    """驗證開通信鑰、建立學科（從 keys.json 驗證並銷毀金鑰）"""
    if not os.path.exists(KEYS_FILE) or not os.path.exists(SUBJECTS_FILE):
        return False, "⚠️ 找不到設定檔或金鑰檔。"

    try:
        with open(KEYS_FILE, 'r', encoding='utf-8') as f:
            keys_data = json.load(f)
        
        creation_keys = keys_data.get("_subject_creation_keys", {})
        if subject_key not in creation_keys:
            return False, "⚠️ 無效的新學科金鑰或已被使用。"

        with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
            raw_subjects = json.load(f)
    except Exception:
        return False, "⚠️ 讀取設定失敗。"

    # 計算唯一代碼
    base_str = subject_key + subject_name
    hash_obj = hashlib.md5(base_str.encode('utf-8'))
    sub_code = hash_obj.hexdigest()[:3].lower()

    while sub_code in raw_subjects:
        sub_code = hashlib.md5((base_str + sub_code).encode('utf-8')).hexdigest()[:4].lower()

    # 取得當前的年月日（例如：2026-06-07）
    creation_date = datetime.date.today().strftime('%Y-%m-%d')

    # 建立新學科
    raw_subjects[sub_code] = {
        "name": subject_name,
        "folder_name": sub_code,
        "admin_teacher": user_id,
        "teachers": [user_id],
        "payment_info": "匯款完成後再請您通知我一下，謝謝！",
        "created_date": creation_date
    }

    # 銷毀用過的新學科金鑰
    del keys_data["_subject_creation_keys"][subject_key]

    try:
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, ensure_ascii=False, indent=4)
            
        with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_subjects, f, ensure_ascii=False, indent=4)
        
        reload_config()
        return True, sub_code
    except Exception as e:
        return False, f"❌ 寫入新學科失敗: {e}"

# ==========================================
# 管理員可以刪除學科
# ==========================================
def delete_subject_by_admin(sub_code, admin_user_id, admin_user_ids):
    """系統管理員刪除指定學科，同時刪除對應的 data 資料夾與設定"""
    if admin_user_id not in admin_user_ids:
        return False, "⚠️ 只有系統管理員可以刪除學科。", None

    if os.path.exists(SUBJECTS_FILE):
        try:
            with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
                raw_subjects = json.load(f)
            
            if sub_code not in raw_subjects:
                return False, f"⚠️ 找不到代碼為【{sub_code}】的學科。", None

            sub_data = raw_subjects[sub_code]
            sub_name = sub_data.get("name", sub_code)
            admin_teacher_id = sub_data.get("admin_teacher")
            
            # 💡 取得該學科對應的資料夾名稱（通常 folder_name 或 sub_code 就是資料夾名稱）
            folder_name = sub_data.get("folder_name", sub_code)
            target_data_dir = os.path.join(DATA_FOLDER, folder_name)

            # 1. 從字典中刪除該學科設定
            del raw_subjects[sub_code]

            with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(raw_subjects, f, ensure_ascii=False, indent=4)

            # 2. 💡 同步刪除 data 裡對應的實體資料夾與所有內部檔案
            if os.path.exists(target_data_dir):
                try:
                    shutil.rmtree(target_data_dir)
                    print(f"已成功刪除實體資料夾: {target_data_dir}")
                except Exception as e:
                    print(f"刪除實體資料夾失敗: {e}")

            # 重新載入設定
            reload_config()
            return True, sub_name, admin_teacher_id
        except Exception as e:
            return False, f"❌ 刪除學科失敗: {e}", None
    return False, "⚠️ 找不到 subjects.json 檔案。", None