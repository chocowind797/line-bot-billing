import os
import datetime
from linebot.v3.webhooks import MessageEvent
from linebot.v3.messaging import QuickReplyItem, PostbackAction
from config import SUBJECT_INFO, ADMIN_USER_IDS, STAGING_FOLDER, TEMP_FILE_FORMAT, DATA_FOLDER, MAX_FILE_SIZE
from services import line_service

def handle_file_message(event: MessageEvent):
    """處理使用者上傳檔案的事件"""
    user_id = event.source.user_id
    message_id = event.message.id
    original_file_name = event.message.file_name
    reply_token = event.reply_token

    # 1. 檢查檔案格式是否為 Excel (.xlsx)
    if not original_file_name.endswith('.xlsx'):
        line_service.reply_text(reply_token, '格式錯誤！請上傳 .xlsx 結尾的 Excel 檔案。')
        return

    # 1. 攔截超過 20MB 的檔案
    # 使用 getattr 是為了避免某些舊版 SDK 取不到 file_size 屬性而報錯
    file_size = getattr(event.message, 'file_size', 0) 
    
    if file_size > MAX_FILE_SIZE:
        # 直接阻擋，連下載都不下載，節省伺服器資源
        line_service.reply_text(
            event.reply_token, 
            f"⚠️ 您上傳的檔案過大 ({round(file_size/1024/1024, 2)} MB)！\n系統限制最大為 20MB，請調整後重新上傳。"
        )
        return

    # 2. 判斷該使用者可以上傳的科目清單
    available_subjects = []
    if user_id in ADMIN_USER_IDS:
        # 管理員：可以直接看到並上傳至所有科目
        available_subjects = list(SUBJECT_INFO.keys())
    else:
        # 一般老師：只能看到自己負責的科目
        for sub_code, sub_info in SUBJECT_INFO.items():
            if user_id in sub_info.get('teachers', []):
                available_subjects.append(sub_code)

    if not available_subjects:
        line_service.reply_text(reply_token, '⚠️ 您目前沒有權限上傳任何科目的檔案。')
        return

    try:
        # 3. 透過 line_service 下載檔案
        file_content = line_service.get_file_content(message_id)

        if not os.path.exists(DATA_FOLDER):
            os.makedirs(DATA_FOLDER)

        # ==========================================
        # 情境 A：只有一個科目，直接儲存
        # ==========================================
        if len(available_subjects) == 1:
            sub_code = available_subjects[0]
            target_folder = SUBJECT_INFO[sub_code]['folder']
            subject_name = SUBJECT_INFO[sub_code]['name']

            if not os.path.exists(target_folder):
                os.makedirs(target_folder)

            now = datetime.datetime.now()
            new_file_name = f"{now.year}年{now.month:02d}月.xlsx"
            file_path = os.path.join(target_folder, new_file_name)
            is_overwrite = os.path.exists(file_path)

            with open(file_path, 'wb') as f:
                f.write(file_content)

            reply_msg = f'✅ 成功接收【{subject_name}】名單！\n已{"覆蓋舊檔並" if is_overwrite else ""}自動儲存為：\n【{new_file_name}】\n可以輸入「發送帳單」來進行作業了。'
            line_service.reply_text(reply_token, reply_msg)

        # ==========================================
        # 情境 B：有多個科目，先暫存並跳出按鈕讓老師選
        # ==========================================
        else:
            # 確保 staging 資料夾存在
            if not os.path.exists(STAGING_FOLDER):
                os.makedirs(STAGING_FOLDER)

            # 透過 config 的格式來產生暫存檔路徑
            file_name = TEMP_FILE_FORMAT.format(msg_id=message_id)
            temp_path = os.path.join(STAGING_FOLDER, file_name)

            with open(temp_path, 'wb') as f:
                f.write(file_content)

            # 建立科目選擇按鈕
            items = []
            for sub_code in available_subjects:
                sub_name = SUBJECT_INFO[sub_code]['name']
                postback_data = f"action=upload_sub&msg_id={message_id}&sub={sub_code}"
                
                # 防呆：確保按鈕文字不會超過 LINE 規定的 20 字元
                label_text = sub_name if len(sub_name) <= 20 else sub_name[:17] + "..."
                
                items.append(
                    QuickReplyItem(
                        action=PostbackAction(
                            label=label_text, 
                            data=postback_data, 
                            display_text=f"上傳至：{sub_name}"
                        )
                    )
                )

            line_service.reply_text(reply_token, "請選擇這份名單是屬於哪個科目的？", quick_reply_items=items)

    except Exception as e:
        line_service.reply_text(reply_token, f'❌ 檔案處理失敗：{str(e)}')