# handlers/postback_handler.py
import os
import shutil
import datetime
from urllib.parse import parse_qsl
from linebot.v3.webhooks import PostbackEvent
from config import SUBJECT_INFO, DATA_FOLDER, TEMP_FILE_FORMAT
from services import line_service, data_service

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
    # 1. 處理上傳 Excel 後的科目選擇按鈕
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
    # 2. 處理老師同意家長綁定
    # ==========================================
    elif action == 'approve_bind':
        parent_id = data.get('parent_id')
        sub_code = data.get('sub')
        student_id = data.get('student_id')
        student_name = data.get('student_name')
        
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

    # ==========================================
    # 3. 處理老師拒絕家長綁定
    # ==========================================
    elif action == 'reject_bind':
        parent_id = data.get('parent_id')
        sub_code = data.get('sub')
        student_name = data.get('student_name')
        
        sub_name = SUBJECT_INFO.get(sub_code, {}).get('name', '未知科目')
        
        # 拒絕只需回覆老師，並通知家長失敗即可，不用動到 data.json
        line_service.reply_text(reply_token, f'❌ 已拒絕家長綁定【{sub_name}】的學生：{student_name}。')
        line_service.push_text(
            parent_id, 
            f'⚠️ 綁定失敗\n您申請綁定【{sub_name}】的學生 {student_name}，老師已拒絕。\n請確認學號與姓名是否正確，或直接與老師聯繫。'
        )

    # ==========================================
    # 4. 刪除老師與刪除學科的確認 (預留擴充)
    # ==========================================
    elif action == 'confirm_del_teacher':
        # 這裡未來可以放入您的「刪除老師」確認邏輯
        pass
        
    elif action == 'confirm_del_subject':
        # 這裡未來可以放入您的「刪除學科並清理資料夾」確認邏輯
        pass