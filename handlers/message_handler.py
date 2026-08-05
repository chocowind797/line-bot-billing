import threading
from linebot.v3.messaging import QuickReplyItem, MessageAction, PostbackAction, TemplateMessage, ButtonsTemplate
from config import (
    SUBJECT_INFO, ADMIN_USER_IDS, ALL_TEACHER_IDS,
    generate_subject_creation_key, create_new_subject_by_key,
    update_subject_payment_info, generate_invite_key, redeem_invite_key,
    delete_subject_by_admin
)
from services import line_service, billing_service, data_service
from utils import state_manager

# ==========================================
# 1. 狀態處理邏輯 (二階段輸入)
# ==========================================
def process_pending_states(event, user_id, text, state):
    reply_token = event.reply_token
    
    # 支援隨時取消
    if text in ['取消', '取消操作', '取消建立', '取消修改']:
        state_manager.clear_state(user_id)
        line_service.reply_text(reply_token, '❌ 已取消操作。')
        return True

    stype = state['type']
    data = state['data']

    if stype == 'PENDING_STUDENT_BINDING':
        parts = text.split()
        if len(parts) < 2:
            line_service.reply_text(reply_token, '⚠️ 格式錯誤！請同時輸入「學號」與「名字」，中間用空格隔開。\n例如：`2601 王小明`\n請重新輸入：（或輸入「取消」）')
            return True
            
        sub_code = data['sub_code']
        sub_name = SUBJECT_INFO[sub_code]['name']
        student_no, student_name = parts[0].strip(), parts[1].strip()

        msg = (f'📱 【{sub_name}】家長綁定通知\n\n請家長複製下方整段文字，並傳送給官方帳號來完成綁定：\n'
               f'----------------------------------\n我是{sub_code}-{student_no}--{student_name}\n----------------------------------\n'
               f'（送出後即完成學生身分對應！）')
               
        line_service.reply_message(reply_token, [
            {"type": "text", "text": msg},
            {"type": "text", "text": f"我是{sub_code}-{student_no}--{student_name}"}
        ])
        state_manager.clear_state(user_id)
        return True

    elif stype == 'PENDING_PAYMENT_EDIT':
        sub_code = data['sub_code']
        subject_name = SUBJECT_INFO.get(sub_code, {}).get('name', '該科目')
        success, err_msg = update_subject_payment_info(sub_code, text, user_id)
        
        if success:
            line_service.reply_text(reply_token, f'✅ 成功更新【{subject_name}】的繳費說明！\n\n【目前最新的說明內容】\n{text}')
        else:
            line_service.reply_text(reply_token, '❌ 寫入設定檔失敗，請稍後再試。')
        state_manager.clear_state(user_id)
        return True

    elif stype == 'PENDING_SUBJECT_CREATION':
        success, result = create_new_subject_by_key(user_id, data['key'], text)
        if success:
            sub_date = SUBJECT_INFO.get(result, {}).get('created_date', '未知日期')
            line_service.reply_text(reply_token, f'🎉 恭喜您！成功建立新學科【{text}】！\n\n📌 專屬學科編號：`{result}`\n📅 開通日期：{sub_date}\n👨‍💼 您已被設為此學科的【管理老師】。')
            
            creator_name = line_service.get_user_name(user_id)
            for admin_id in ADMIN_USER_IDS:
                if admin_id != user_id:
                    line_service.push_text(admin_id, f'📢 【系統通知】有新學科建立囉！\n📚 學科名稱：{text}\n🆔 編號：`{result}`\n👨‍🏫 管理老師：{creator_name}')
        else:
            line_service.reply_text(reply_token, result)
        state_manager.clear_state(user_id)
        return True

    return False

# ==========================================
# 2. 特殊格式處理 (家長綁定)
# ==========================================
def handle_parent_binding(event, user_id, text):
    reply_token = event.reply_token
    content = text.replace('我是', '').strip()
    
    try:
        subject_part, rest_part = content.split('-', 1)
        student_id, raw_student_name = rest_part.split('--', 1)
        
        sub_code = subject_part.strip()
        s_id = student_id.strip()
        s_name = raw_student_name.strip()

        for suffix in ['的爸爸', '的媽媽', '爸爸', '媽媽', '的家長', '家長', '阿公', '阿嬤']:
            if s_name.endswith(suffix):
                s_name = s_name[:-len(suffix)].strip()
                break

        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, f'❌ 找不到科目代碼【{sub_code}】。請確認格式為「我是[科目代碼]-[學號]--[姓名]」。')
            return True

        sub_name = SUBJECT_INFO[sub_code]['name']
        line_service.reply_text(reply_token, f'⏳ 已收到綁定請求！\n申請綁定【{sub_name}】課程：\n學號：【{s_id}】\n姓名：【{s_name}】\n已通知該科老師，請等候確認。')

        # 💡 使用新版的安全參數格式
        approve_data = f"action=approve_bind&uid={user_id}&sub={sub_code}&sid={s_id}&sname={s_name}"
        reject_data = f"action=reject_bind&uid={user_id}&sub={sub_code}&sid={s_id}&sname={s_name}"
        parent_name = line_service.get_user_name(user_id)

        tmpl = TemplateMessage(
            alt_text="收到新的綁定審核請求",
            template=ButtonsTemplate(
                text=f"🔔 綁定審核\n家長「{parent_name}」申請綁定：\n【{sub_name}】{s_id} {s_name}",
                actions=[
                    PostbackAction(label="✅ 同意", data=approve_data),
                    PostbackAction(label="❌ 拒絕", data=reject_data)
                ]
            )
        )

        for teacher_id in SUBJECT_INFO[sub_code]['teachers']:
            try:
                # 必須使用 push_message，才能主動把按鈕發送給該科目的老師
                line_service.push_message(teacher_id, [tmpl])
            except Exception as e:
                print(f"通知老師 {teacher_id} 失敗: {e}")
            
    except ValueError:
        line_service.reply_text(reply_token, '⚠️ 格式錯誤！請確認輸入格式為：我是[科目編號]-[學號]--[姓名]')
    return True

# ==========================================
# 3. 指令路由對應函式 (Router Functions)
# ==========================================
def cmd_help(event, user_id, text, parts):
    # 1. 判斷使用者的身分權限
    is_admin = user_id in ADMIN_USER_IDS
    
    # 檢查是否為任何學科的管理老師
    is_manager = False
    for sub_code, sub_info in SUBJECT_INFO.items():
        if sub_info.get('admin_teacher') == user_id:
            is_manager = True
            break

    # 2. 基礎功能（所有老師皆有）
    help_text = (
        '📚 【老師功能指南】\n\n'
        '1️⃣ 學生與綁定管理\n'
        '• 輸入 `產生綁定`：選擇科目後輸入「學號 名字」，快速產生家長綁定通知範本。\n'
    )
    
    quick_reply_items = [
        QuickReplyItem(action=MessageAction(label="產生學生綁定", text="產生綁定"))
    ]

    # 3. 若為「管理老師」或「管理員」，加入管理老師功能
    if is_manager or is_admin:
        help_text += (
            '\n2️⃣ 管理老師專屬功能\n'
            '• 輸入 `發送帳單`：批次發送半年內各學生的學費帳單。\n'
            '• 輸入 `發送帳單 [月份]`：批次發送指定時間內各學生的學費帳單。\n'
            '• 輸入 `單發帳單 [學號]`：針對特定學生補發或單獨發送帳單。\n'
            '• 輸入 `修改說明`：自訂該學科的轉帳/繳費說明文字。\n'
            '• 輸入 `新增老師`：產生單次使用的老師邀請金鑰。\n'
            '• 輸入 `移除老師`：安全移除授課老師（附帶確認與通知）。\n'
        )
        quick_reply_items.extend([
            QuickReplyItem(action=MessageAction(label="發送帳單", text="發送帳單")),
            QuickReplyItem(action=MessageAction(label="新增老師", text="新增老師")),
            QuickReplyItem(action=MessageAction(label="移除老師", text="移除老師")),
            QuickReplyItem(action=MessageAction(label="修改說明", text="修改說明")),
        ])

    # 4. 若為「系統管理員」，再加入管理員功能
    if is_admin:
        help_text += (
            '\n3️⃣ 系統管理員專屬功能\n'
            '• 輸入 `新增學科`：產生新學科開通信鑰。\n'
            '• 輸入 `刪除學科`：刪除學科並自動清理對應資料與發送通知。\n'
        )
        quick_reply_items.extend([
            QuickReplyItem(action=MessageAction(label="新增學科", text="新增學科")),
            QuickReplyItem(action=MessageAction(label="刪除學科", text="刪除學科")),
        ])

    # 5. 使用 line_service 統一發送 (程式碼變得超級乾淨！)
    line_service.reply_text(event.reply_token, help_text, quick_reply_items)

def cmd_generate_binding(event, user_id, text, parts):
    allowed_subjects = [c for c, i in SUBJECT_INFO.items() if not c.startswith('_') and (user_id in ADMIN_USER_IDS or user_id in i.get('teachers', []))]
    if not allowed_subjects:
        return line_service.reply_text(event.reply_token, '⚠️ 您目前沒有參與任何學科，無法產生綁定訊息。')

    if len(allowed_subjects) == 1:
        sub_code = allowed_subjects[0]
        state_manager.set_state(user_id, 'PENDING_STUDENT_BINDING', {"sub_code": sub_code})
        line_service.reply_text(event.reply_token, f'📌 目標學科：【{SUBJECT_INFO[sub_code]["name"]}】\n請輸入【學號】與【名字】（空格隔開）：')
    else:
        items = [QuickReplyItem(action=PostbackAction(label=SUBJECT_INFO[c]['name'], data=f"action=bind_select_sub&sub={c}")) for c in allowed_subjects]
        line_service.reply_text(event.reply_token, "📋 請選擇您要為哪一個學科產生學生綁定訊息：", quick_reply_items=items)

def cmd_send_bills(event, user_id, text, parts):
    lookback = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 6
    target_subjects = [c for c, i in SUBJECT_INFO.items() if not c.startswith('_') and (user_id in ADMIN_USER_IDS or i.get('admin_teacher') == user_id)]
    
    if not target_subjects:
        return line_service.reply_text(event.reply_token, '⚠️ 只有該學科的管理老師或系統管理員才可以發送帳單。')

    if len(target_subjects) == 1:
        sub_code = target_subjects[0]
        line_service.reply_text(event.reply_token, f'⏳ 系統已開始處理【{SUBJECT_INFO[sub_code]["name"]}】的帳單...')
        
        def bg_task():
            bindings = data_service.load_verified_bindings()
            res = billing_service.send_bills_logic(bindings, sub_code, lookback_months=lookback)
            line_service.push_text(user_id, res)
        threading.Thread(target=bg_task).start()
    else:
        items = [QuickReplyItem(action=PostbackAction(label=SUBJECT_INFO[c]['name'], data=f"action=exec_bill&mode=batch&sub={c}&sid=none&lb={lookback}")) for c in target_subjects]
        if user_id in ADMIN_USER_IDS:
            items.append(QuickReplyItem(action=PostbackAction(label="🌐 全部科目", data=f"action=exec_bill&mode=batch&sub=all&sid=none&lb={lookback}")))
        line_service.reply_text(event.reply_token, "📋 請選擇要執行批次發送的科目：", quick_reply_items=items)

def cmd_activate_subject(event, user_id, text, parts):
    if len(parts) < 2:
        return line_service.reply_text(event.reply_token, '格式錯誤！請輸入：開通學科 金鑰')
    state_manager.set_state(user_id, 'PENDING_SUBJECT_CREATION', {'key': parts[1].strip().upper()})
    line_service.reply_text(event.reply_token, '✅ 金鑰驗證成功！請輸入您想要建立的【學科名稱】：')

def cmd_join_teacher(event, user_id, text, parts):
    if len(parts) < 2:
        return line_service.reply_text(event.reply_token, '格式錯誤！請輸入：加入老師 金鑰')
    success, msg, admin_id = redeem_invite_key(user_id, parts[1].strip().upper())
    line_service.reply_text(event.reply_token, f'🎉 恭喜您！成功加入【{msg}】' if success else msg)
    if success and admin_id and admin_id != user_id:
        line_service.push_text(admin_id, f'🔔 【系統通知】新老師【{line_service.get_user_name(user_id)}】加入了您的學科！')

def cmd_add_subject(event, user_id, text, parts):
    if user_id not in ADMIN_USER_IDS:
        return line_service.reply_text(event.reply_token, '⚠️ 只有系統管理員可以使用此功能。')
    key, err_msg = generate_subject_creation_key(user_id, ADMIN_USER_IDS)
    if err_msg:
        line_service.reply_text(event.reply_token, err_msg)
    else:
        line_service.reply_message(event.reply_token, [
            {"type": "text", "text": '🔑 已成功產生【新學科開通信鑰】：\n請將下方金鑰提供給新學科負責人，對方輸入 `開通學科 金鑰` 即可開始建立！'},
            {"type": "text", "text": f'開通學科 {key}'}
        ])

def cmd_delete_subject(event, user_id, text, parts):
    if user_id not in ADMIN_USER_IDS:
        return line_service.reply_text(event.reply_token, '⚠️ 只有系統管理員可以刪除學科。')

    # 若直接輸入「刪除學科 123」
    if len(parts) > 1:
        sub_code = parts[1].strip()
        success, result = delete_subject_by_admin(sub_code, user_id, ADMIN_USER_IDS)
        line_service.reply_text(event.reply_token, f'🗑️ 已成功刪除學科：【{result}】（代碼: {sub_code}）' if success else result)
        return

    # 若只輸入「刪除學科」，跳出按鈕選擇
    if not SUBJECT_INFO:
        return line_service.reply_text(event.reply_token, '⚠️ 目前系統中沒有任何學科可供刪除。')

    items = [QuickReplyItem(action=PostbackAction(label=f"刪除 {i['name']}", data=f"action=del_sub&sub={c}", display_text=f"刪除學科 {i['name']}")) for c, i in SUBJECT_INFO.items()]
    line_service.reply_text(event.reply_token, "⚠️ 【警告】請選擇您想要刪除的學科：\n（刪除後將移除該學科的所有設定）", quick_reply_items=items)

def cmd_send_single_bill(event, user_id, text, parts):
    if len(parts) < 2:
        return line_service.reply_text(event.reply_token, '格式錯誤！請輸入例如：單發帳單 2601 或 單發帳單 2601 3')
    target_id = parts[1].strip()
    lookback = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 6

    target_subjects = [c for c, i in SUBJECT_INFO.items() if not c.startswith('_') and (user_id in ADMIN_USER_IDS or i.get('admin_teacher') == user_id)]
    if not target_subjects:
        return line_service.reply_text(event.reply_token, '⚠️ 只有該學科的管理老師或系統管理員才可以發送帳單。')

    if len(target_subjects) == 1:
        sub_code = target_subjects[0]
        line_service.reply_text(event.reply_token, f'⏳ 系統已開始為您處理【{SUBJECT_INFO[sub_code]["name"]}】的單發帳單...')
        def bg_task():
            bindings = data_service.load_verified_bindings()
            res = billing_service.send_bills_logic(bindings, sub_code, target_id, lookback)
            line_service.push_text(user_id, res)
        threading.Thread(target=bg_task).start()
    else:
        items = [QuickReplyItem(action=PostbackAction(label=SUBJECT_INFO[c]['name'], data=f"action=exec_bill&mode=single&sub={c}&sid={target_id}&lb={lookback}", display_text=f"單發【{SUBJECT_INFO[c]['name']}】帳單")) for c in target_subjects]
        if user_id in ADMIN_USER_IDS:
            items.append(QuickReplyItem(action=PostbackAction(label="🌐 全部科目", data=f"action=exec_bill&mode=single&sub=all&sid={target_id}&lb={lookback}", display_text="從所有科目尋找並發送此學生帳單")))
        line_service.reply_text(event.reply_token, f"📋 請選擇要從哪個科目發送學號【{target_id}】的帳單：", quick_reply_items=items)

def cmd_edit_payment_info(event, user_id, text, parts):
    managed_subjects = [c for c, i in SUBJECT_INFO.items() if user_id in ADMIN_USER_IDS or i.get('admin_teacher') == user_id]
    if not managed_subjects:
        return line_service.reply_text(event.reply_token, '⚠️ 您目前沒有被指定為任何學科的管理老師，無法修改繳費說明。')

    if len(managed_subjects) == 1:
        sub_code = managed_subjects[0]
        state_manager.set_state(user_id, 'PENDING_PAYMENT_EDIT', {'sub_code': sub_code})
        line_service.reply_text(event.reply_token, f'📝 您目前正準備修改【{SUBJECT_INFO[sub_code]["name"]}】的繳費說明。\n\n請直接在聊天室輸入新內容：')
    else:
        items = [QuickReplyItem(action=PostbackAction(label=SUBJECT_INFO[c]['name'], data=f"action=select_edit_sub&sub={c}", display_text=f"修改【{SUBJECT_INFO[c]['name']}】說明")) for c in managed_subjects]
        line_service.reply_text(event.reply_token, "📋 請選擇您想要修改哪個學科的繳費說明：", quick_reply_items=items)

def cmd_add_teacher(event, user_id, text, parts):
    managed_subjects = [c for c, i in SUBJECT_INFO.items() if i.get('admin_teacher') == user_id or user_id in ADMIN_USER_IDS]
    if not managed_subjects:
        return line_service.reply_text(event.reply_token, '⚠️ 您目前沒有被指定為任何學科的管理老師，無法產生邀請金鑰。')

    if len(managed_subjects) == 1:
        sub_code = managed_subjects[0]
        key, err_msg = generate_invite_key(sub_code, user_id)
        if err_msg:
            line_service.reply_text(event.reply_token, err_msg)
        else:
            line_service.reply_message(event.reply_token, [
                {"type": "text", "text": f'🔑 已成功為【{SUBJECT_INFO[sub_code]["name"]}】產生單次邀請金鑰：\n\n👉 `{key}`\n\n請將此金鑰提供給新老師，輸入 `加入老師 {key}` 即可加入！'},
                {"type": "text", "text": f'加入老師 {key}'}
            ])
    else:
        items = [QuickReplyItem(action=PostbackAction(label=SUBJECT_INFO[c]['name'], data=f"action=gen_key&sub={c}", display_text=f"為【{SUBJECT_INFO[c]['name']}】產生邀請金鑰")) for c in managed_subjects]
        line_service.reply_text(event.reply_token, "📋 請選擇您要為哪一個學科產生新老師邀請金鑰：", quick_reply_items=items)

def cmd_remove_teacher(event, user_id, text, parts):
    managed_subjects = [c for c, i in SUBJECT_INFO.items() if not c.startswith('_') and (user_id in ADMIN_USER_IDS or i.get('admin_teacher') == user_id)]
    if not managed_subjects:
        return line_service.reply_text(event.reply_token, '⚠️ 您目前沒有被指定為任何學科的管理老師。')

    if len(managed_subjects) == 1:
        sub_code = managed_subjects[0]
        sub_name = SUBJECT_INFO[sub_code]['name']
        other_teachers = [t for t in SUBJECT_INFO[sub_code].get('teachers', []) if t != SUBJECT_INFO[sub_code].get('admin_teacher')]
        
        if not other_teachers:
            return line_service.reply_text(event.reply_token, f'⚠️ 【{sub_name}】目前除了您以外，沒有其他授課老師可移除。')

        items = [QuickReplyItem(action=PostbackAction(label=f"移除 {line_service.get_user_name(t_id)[:15]}", data=f"action=ask_remove_t&sub={sub_code}&target={t_id}", display_text=f"移除老師")) for t_id in other_teachers]
        line_service.reply_text(event.reply_token, f"📋 請選擇您想要從【{sub_name}】移除的授課老師：", quick_reply_items=items)
    else:
        items = [QuickReplyItem(action=PostbackAction(label=SUBJECT_INFO[c]['name'], data=f"action=remove_teacher_sub&sub={c}", display_text=f"管理【{SUBJECT_INFO[c]['name']}】老師")) for c in managed_subjects]
        line_service.reply_text(event.reply_token, "📋 請選擇您要管理哪一個學科的老師名單：", quick_reply_items=items)

# ==========================================
# 4. Router 註冊表 (將文字對應到函式)
# ==========================================
COMMAND_ROUTER = {
    # 將所有觸發幫助的關鍵字都指向 cmd_help
    '幫助': cmd_help,
    'help': cmd_help,
    '功能': cmd_help,
    '指令說明': cmd_help,
    '?': cmd_help,
    '？': cmd_help,
    
    # 一般老師功能
    '產生綁定': cmd_generate_binding,
    '加入老師': cmd_join_teacher,

    # 管理老師功能
    '發送帳單': cmd_send_bills,
    '開通學科': cmd_activate_subject,
    '單發帳單': cmd_send_single_bill,
    '修改說明': cmd_edit_payment_info,
    '新增老師': cmd_add_teacher,
    '移除老師': cmd_remove_teacher,
    
    # 管理員功能
    '新增學科': cmd_add_subject,
    '刪除學科': cmd_delete_subject,
}

# ==========================================
# 🎯 主進入點
# ==========================================
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    parts = text.split()

    # 1. 攔截二階段輸入狀態
    state = state_manager.get_state(user_id)
    if state and process_pending_states(event, user_id, text, state):
        return

    # 2. 攔截家長專屬綁定格式
    if text.startswith('我是') and '-' in text and '--' in text:
        if handle_parent_binding(event, user_id, text):
            return

    # 3. 進入指令路由 (Router)
    cmd = parts[0] # 取第一個詞作為指令
    action_func = COMMAND_ROUTER.get(cmd)
    
    if action_func:
        action_func(event, user_id, text, parts)