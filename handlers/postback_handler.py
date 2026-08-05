# handlers/postback_handler.py
import os
import shutil
import threading
import datetime
from urllib.parse import parse_qsl
from linebot.v3.webhooks import PostbackEvent
from config import (
    SUBJECT_INFO, DATA_FOLDER, TEMP_FILE_FORMAT, ADMIN_USER_IDS,
    delete_subject_by_admin, generate_invite_key, remove_teacher_from_subject
)
from services import line_service, data_service, billing_service
from linebot.v3.messaging import (
    TemplateMessage, ConfirmTemplate, PostbackAction, MessageAction,
    QuickReply, QuickReplyItem
)
from utils import state_manager

def handle_postback(event: PostbackEvent):
    """處理所有來自按鈕點擊 (Postback) 的事件"""
    user_id = event.source.user_id
    reply_token = event.reply_token
    
    # 💡 將按鈕隱藏的資料 (例如 "action=upload_sub&msg_id=123&sub=MATH") 解析成字典
    data = dict(parse_qsl(event.postback.data))
    action = data.get('action')

    if not action:
        return

    # ==========================================
    # 處理路線 A. 處理上傳 Excel 後的科目選擇按鈕
    # ==========================================
    if action == 'upload_sub':
        msg_id = data.get('msg_id')
        sub_code = data.get('sub')
        
        # 尋找我們在 file_handler.py 存下來的暫存檔
        file_name = TEMP_FILE_FORMAT.format(msg_id=msg_id)
        temp_path = os.path.join(STAGING_FOLDER, file_name)
        if not os.path.exists(temp_path):
            line_service.reply_text(reply_token, '❌ 找不到暫存檔案，可能已經處理完畢或過期。')
            return
            
        sub_info = SUBJECT_INFO.get(sub_code)
        if not sub_info:
            line_service.reply_text(reply_token, '❌ 找不到該科目資訊。')
            return
            
        sub_name = sub_info['name']
        target_folder = sub_info['folder']
        
        if not os.path.exists(target_folder):
            os.makedirs(target_folder)
            
        now = datetime.datetime.now()
        new_file_name = f"{now.year}年{now.month:02d}月.xlsx"
        file_path = os.path.join(target_folder, new_file_name)
        is_overwrite = os.path.exists(file_path)
        
        # 將暫存檔移動至目標科目資料夾並重新命名
        shutil.move(temp_path, file_path)
        
        reply_msg = f'✅ 成功將名單歸檔至【{sub_name}】！\n已{"覆蓋舊檔並" if is_overwrite else ""}自動儲存為：\n【{new_file_name}】\n可以輸入「發送帳單」來進行作業了。'
        line_service.reply_text(reply_token, reply_msg)

    # ==========================================
    # 處理路線 B-1. 處理老師同意家長綁定
    # ==========================================
    elif action == 'approve_bind':
        parent_id = data.get('uid')
        sub_code = data.get('sub')
        student_id = data.get('sid')
        student_name = data.get('sname')
        
        sub_name = SUBJECT_INFO.get(sub_code, {}).get('name', '未知科目')
        
        # 透過我們剛寫好的 data_service 讀取綁定名單
        verified = data_service.load_verified_bindings()
        
        if parent_id not in verified:
            verified[parent_id] = {}
        if sub_code not in verified[parent_id]:
            verified[parent_id][sub_code] = []
            
        bind_record = f"{student_id}--{student_name}"
        
        if bind_record in verified[parent_id][sub_code]:
            line_service.reply_text(reply_token, f'⚠️ 學生【{student_name}】已經在【{sub_name}】綁定名單中了。')
            return
            
        # 加入紀錄並存檔
        verified[parent_id][sub_code].append(bind_record)
        data_service.save_verified_bindings(verified)
        
        # 回覆點擊按鈕的老師
        line_service.reply_text(reply_token, f'✅ 已同意家長綁定【{sub_name}】的學生：{student_name} ({student_id})')
        
        # 發送推播通知給家長
        line_service.push_text(
            parent_id, 
            f'🎉 審核通過！\n您已成功綁定【{sub_name}】的學生：{student_name} ({student_id})。\n未來若有帳單產生，系統將自動通知您。'
        )

        # 清除 pending 紀錄：
        pending_data = data_service.load_pending_bindings()
        if parent_id in pending_data and sub_code in pending_data[parent_id]:
            del pending_data[parent_id][sub_code]
            # 如果該家長沒有其他科目的申請了，就把家長整個 key 拔掉
            if not pending_data[parent_id]:
                del pending_data[parent_id]
            data_service.save_pending_bindings(pending_data)

    # ==========================================
    # 處理路線 B-2. 處理老師拒絕家長綁定
    # ==========================================
    elif action == 'reject_bind':
        parent_id = data.get('uid')
        sub_code = data.get('sub')
        student_name = data.get('sname')
        
        sub_name = SUBJECT_INFO.get(sub_code, {}).get('name', '未知科目')
        
        # 拒絕只需回覆老師，並通知家長失敗即可，不用動到 data.json
        line_service.reply_text(reply_token, f'❌ 已拒絕家長綁定【{sub_name}】的學生：{student_name}。')
        line_service.push_text(
            parent_id, 
            f'⚠️ 綁定失敗\n您申請綁定【{sub_name}】的學生 {student_name}，老師已拒絕。\n請確認學號與姓名是否正確，或直接與老師聯繫。'
        )

        # 清除 pending 紀錄：
        pending_data = data_service.load_pending_bindings()
        if parent_id in pending_data and sub_code in pending_data[parent_id]:
            del pending_data[parent_id][sub_code]
            # 如果該家長沒有其他科目的申請了，就把家長整個 key 拔掉
            if not pending_data[parent_id]:
                del pending_data[parent_id]
            data_service.save_pending_bindings(pending_data)

    # ==========================================
    # 處理路線 C：選擇科目後執行帳單發送
    # ==========================================
    elif action == 'exec_bill':
        mode = data.get('mode')       # 'batch' 或 'single'
        sub_code = data.get('sub')    # 科目代碼 或 'all'
        target_student_id = data.get('sid')
        if target_student_id == 'none':
            target_student_id = None
        lookback_months = int(data.get('lb', 6))

        subjects_to_send = list(SUBJECT_INFO.keys()) if sub_code == 'all' else [sub_code]
        sub_names_str = "、".join([SUBJECT_INFO[c]['name'] for c in subjects_to_send if c in SUBJECT_INFO])

        line_service.reply_text(
            reply_token,
            f'⏳ 收到！正在背景為您處理【{sub_names_str}】的帳單（回溯 {lookback_months} 個月），完成後會主動回報，請稍候...'
        )
        
        # 2. 定義一個背景執行的包裝函式，用來接收回傳值並主動推播給老師
        def run_billing_task():
            try:
                # 執行原本的商業邏輯並取得回傳結果
                result_msg = billing_service.send_bills_logic(
                    sub_code, target_student_id, lookback_months
                )
                
                # 如果有回傳內容，主動推送給執行這項操作的老師
                if result_msg:
                    line_service.push_text(user_id, result_msg)
                else:
                    line_service.push_text(user_id, f'✅ 【{sub_names_str}】帳單背景執行完畢，但沒有產生額外回報內容。')
                    
            except Exception as e:
                print(f"背景執行帳單發送失敗: {e}")
                line_service.push_text(user_id, f'❌ 【{sub_names_str}】帳單執行過程中發生錯誤：\n{e}')

        # 3. 透過 threading 執行包裝好的任務
        bg_thread = threading.Thread(target=run_billing_task)
        bg_thread.start()

        return

    # ==========================================
    # 處理路線 D：選擇要修改說明的科目
    # ==========================================
    elif action == 'select_edit_sub':
        sub_code = data.get('sub')
        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, "⚠️ 找不到該科目的設定。")
            return
            
        subject_name = SUBJECT_INFO[sub_code]['name']

        # 寫入 PENDING_PAYMENT_EDIT 狀態
        state_manager.set_state(
            user_id,
            'PENDING_PAYMENT_EDIT',
            {"sub_code": sub_code}
        )

        line_service.reply_text(
            reply_token,
            f'📝 您已選擇修改【{subject_name}】的繳費說明。\n\n請直接在聊天室輸入您想要設定的「新繳費說明內容」：'
        )
        return

    # ==========================================
    # 處理路線 E：管理老師透過按鈕選定學科後產生邀請金鑰
    # ==========================================
    elif action == 'gen_key':
        sub_code = data.get('sub')
        
        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, "⚠️ 找不到該科目的設定。")
            return
            
        # 確保您的 config 有引入 generate_invite_key
        key, err_msg = generate_invite_key(sub_code, user_id)
        if err_msg:
            line_service.reply_text(reply_token, err_msg)
        else:
            sub_name = SUBJECT_INFO[sub_code]['name']
            # 利用升級過、支援清單與自動轉換的 line_service 發送兩則訊息
            line_service.reply_message(
                reply_token,
                [
                    f'🔑 已成功為【{sub_name}】產生單次邀請金鑰：\n\n👉 `{key}`\n\n請將此金鑰提供給新老師，輸入 `加入老師 {key}` 即可加入！',
                    f'加入老師 {key}'
                ]
            )
        return

    # ==========================================
    # 處理路線 F-1：管理員點擊刪除後，跳出 Confirm 確認按鈕
    # ==========================================
    elif action == 'del_sub':
        if user_id not in ADMIN_USER_IDS:
            line_service.reply_text(reply_token, '⚠️ 只有系統管理員可以刪除學科。')
            return

        sub_code = data.get('sub')
        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, '⚠️ 找不到該學科。')
            return

        sub_name = SUBJECT_INFO[sub_code]['name']
        
        # 發送 Confirm 確認樣板
        line_service.reply_message(
            reply_token,
            [
                TemplateMessage(
                    alt_text=f"確認刪除學科：{sub_name}",
                    template=ConfirmTemplate(
                        text=f"⚠️ 您確定要刪除學科【{sub_name}】嗎？\n刪除後將無法復原！",
                        actions=[
                            PostbackAction(
                                label="確定刪除",
                                data=f"action=confirm_del_sub&sub={sub_code}",
                                display_text=f"確定刪除 {sub_name}"
                            ),
                            MessageAction(
                                label="取消",
                                text="取消刪除"
                            )
                        ]
                    )
                )
            ]
        )
        return

    # ==========================================
    # 處理路線 F-2：管理員按下「確定刪除」後執行刪除並發送通知
    # ==========================================
    elif action == 'confirm_del_sub':
        if user_id not in ADMIN_USER_IDS:
            line_service.reply_text(reply_token, '⚠️ 只有系統管理員可以刪除學科。')
            return

        sub_code = data.get('sub')
        
        # 執行刪除
        success, result_msg, admin_teacher_id = delete_subject_by_admin(sub_code, user_id, ADMIN_USER_IDS)
        
        if success:
            sub_name = result_msg
            
            # 1. 回覆管理員刪除成功
            line_service.reply_text(reply_token, f'🗑️ 已成功刪除學科：【{sub_name}】（代碼: {sub_code}）')

            # 2. 如果該學科有指定管理老師，且不是管理員本人，主動發送通知給該管理老師
            if admin_teacher_id and admin_teacher_id != user_id:
                try:
                    line_service.push_text(
                        admin_teacher_id,
                        f'⚠️ 【系統通知】\n您所管理的學科【{sub_name}】已被系統管理員刪除。如有疑問請與管理員聯繫。'
                    )
                except Exception as e:
                    print(f"發送刪除通知給管理老師失敗: {e}")
        else:
            line_service.reply_text(reply_token, result_msg)
            
        return

    # ==========================================
    # 處理路線 G-1：管理老師選定學科後，顯示該科的老師清單按鈕
    # ==========================================
    elif action == 'remove_teacher_sub':
        sub_code = data.get('sub')
        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, '⚠️ 找不到該學科。')
            return

        sub_name = SUBJECT_INFO[sub_code]['name']
        teachers = SUBJECT_INFO[sub_code].get('teachers', [])
        admin_t = SUBJECT_INFO[sub_code].get('admin_teacher')
        other_teachers = [t for t in teachers if t != admin_t]

        if not other_teachers:
            line_service.reply_text(reply_token, f'⚠️ 【{sub_name}】目前除了您以外，沒有其他授課老師可移除。')
            return

        items = []
        for t_id in other_teachers:
            try:
                # 取得老師的 LINE 暱稱
                t_name = line_service.get_user_name(t_id)
            except Exception:
                t_name = f"老師 ({t_id[-4:]})"
            
            label_text = f"移除 {t_name}"
            if len(label_text) > 20:
                label_text = label_text[:17] + "..."

            p_data = f"action=ask_remove_t&sub={sub_code}&target={t_id}"
            
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=label_text,
                        data=p_data,
                        display_text=f"移除 {t_name}"
                    )
                )
            )

        line_service.reply_text(
            reply_token,
            f"📋 請選擇您想要從【{sub_name}】移除的授課老師：",
            quick_reply_items=items
        )
        return

    # ==========================================
    # 處理路線 G-2：點擊移除老師後，彈出 Confirm 確認按鈕
    # ==========================================
    elif action == 'ask_remove_t':
        sub_code = data.get('sub')
        target_id = data.get('target')

        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, '⚠️ 找不到該學科。')
            return

        sub_name = SUBJECT_INFO[sub_code]['name']

        try:
            target_name = line_service.get_user_name(target_id)
        except Exception:
            target_name = f"老師 ({target_id[-4:]})"

        # 發送 Confirm 確認樣板
        line_service.reply_message(
            reply_token,
            [
                TemplateMessage(
                    alt_text=f"確認移除老師：{target_name}",
                    template=ConfirmTemplate(
                        text=f"⚠️ 您確定要從【{sub_name}】中移除授課老師【{target_name}】嗎？",
                        actions=[
                            PostbackAction(
                                label="確定移除",
                                data=f"action=confirm_remove_t&sub={sub_code}&target={target_id}",
                                display_text=f"確定移除 {target_name}"
                            ),
                            MessageAction(
                                label="取消",
                                text="取消移除"
                            )
                        ]
                    )
                )
            ]
        )
        return

    # ==========================================
    # 處理路線 G-3：確認執行移除該老師，並發送通知
    # ==========================================
    elif action == 'confirm_remove_t':
        sub_code = data.get('sub')
        target_id = data.get('target')

        # 假設您的 remove_teacher_from_subject 函式已定義
        success, result_msg = remove_teacher_from_subject(sub_code, target_id, user_id, ADMIN_USER_IDS)
        
        if success:
            sub_name = result_msg
            
            # 1. 回覆管理老師移除成功
            line_service.reply_text(reply_token, f'✅ 已成功從【{sub_name}】中移除該位授課老師。')

            # 2. 主動發送通知給被移除的老師
            try:
                line_service.push_text(
                    target_id,
                    f'⚠️ 【系統通知】\n您已被管理老師從學科【{sub_name}】的授課名單中移除。'
                )
            except Exception as e:
                print(f"發送移除通知給老師失敗: {e}")
        else:
            line_service.reply_text(reply_token, result_msg)
            
        return

    # ==========================================
    # H-1：老師透過按鈕選定學科後，提示輸入學號與名字
    # ==========================================
    elif action == 'bind_select_sub':
        sub_code = data.get('sub')
        if sub_code not in SUBJECT_INFO:
            line_service.reply_text(reply_token, '⚠️ 找不到該學科。')
            return

        sub_name = SUBJECT_INFO[sub_code]['name']

        # 狀態寫入 state_manager，聊天室才抓得到！
        state_manager.set_state(
            user_id, 
            'PENDING_STUDENT_BINDING', 
            {"sub_code": sub_code}
        )

        line_service.reply_text(
            reply_token,
            f'📌 目標學科：【{sub_name}】\n\n請直接在此聊天室輸入要給學生的【學號】與【名字】（中間用空格隔開）\n例如：`2601 王小明`\n\n（若想放棄，請輸入「取消」）'
        )
        return