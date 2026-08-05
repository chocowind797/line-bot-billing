import os
import json

STATE_FILE = 'data/temp_states.json'

def _load_states():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_states(states):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(states, f, ensure_ascii=False, indent=4)

def get_state(user_id):
    """取得使用者的當前狀態與資料"""
    states = _load_states()
    return states.get(str(user_id))

def set_state(user_id, state_type, data=None):
    """設定使用者狀態"""
    states = _load_states()
    states[str(user_id)] = {
        'type': state_type,
        'data': data or {}
    }
    _save_states(states)

def clear_state(user_id):
    """清除使用者狀態"""
    states = _load_states()
    if str(user_id) in states:
        del states[str(user_id)]
        _save_states(states)