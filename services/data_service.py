import os
import json
from config import DATA_FILE_PATH, PENDING_FILE

def load_verified_bindings():
    """讀取家長學生綁定資料"""
    if os.path.exists(DATA_FILE_PATH):
        try:
            with open(DATA_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('verified', {})
        except Exception:
            pass
    return {}

def save_verified_bindings(verified):
    """儲存家長學生綁定資料"""
    # 確保資料夾存在，避免第一次寫入時報錯
    os.makedirs(os.path.dirname(DATA_FILE_PATH), exist_ok=True)
    
    data = {'verified': verified}
    with open(DATA_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_pending_bindings():
    """讀取所有等待審核中的綁定申請"""
    if not os.path.exists(PENDING_FILE):
        return {}
    try:
        with open(PENDING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_pending_bindings(data):
    """儲存等待審核中的綁定申請"""
    if not os.path.exists(STAGING_FOLDER):
        os.makedirs(STAGING_FOLDER)
    with open(PENDING_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)