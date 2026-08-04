import datetime
import glob
import json
import logging
import threading
import time
import os
from urllib.parse import parse_qsl  # 用來解析按鈕傳回來的隱藏資料
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
    TemplateMessage,   
    ButtonsTemplate,   
    PostbackAction,
    QuickReply,
    QuickReplyItem 
)
from linebot.v3.webhooks import (
    MessageEvent, 
    TextMessageContent, 
    FileMessageContent,
    PostbackEvent,
)
import pandas as pd
# 從 config.py 統一匯入所有環境變數與設定
from config import (
    LINE_CHANNEL_ACCESS_TOKEN,
    LINE_CHANNEL_SECRET,
    ADMIN_USER_IDS,
    SUBJECT_INFO,
    ALL_TEACHER_IDS,
    DATA_FOLDER,
    DATA_FILE_PATH,
    reload_config,
    update_subject_payment_info
)

app = Flask(__name__)

# 關閉 Werkzeug 每筆連線的 200 OK 刷屏日誌
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# v3 的 API 與 Handler 初始化方式
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 用來記錄正在修改說名的老師狀態 {teacher_id: sub_code}
PENDING_PAYMENT_EDIT = {}

def get_current_month_excel_path(folder_path):
  # 【初始化檢查】確保該科目的專屬資料夾存在，如果不存在就自動建立一個
  if not os.path.exists(folder_path):
    os.makedirs(folder_path)

  now = datetime.datetime.now()
  year_str = str(now.year)
  month_str = f'{now.month:02d}' 

  # ====================================================
  # 讀取方式 1：精準尋找包含「當前年月」的 Excel 檔案
  # 情境：老師上傳的檔案名稱很標準，包含目前的年份與月份。
  # 邏輯：利用 glob 模糊搜尋檔名中同時包含「今年(例如2024)」與「當月(例如05)」的 .xlsx 檔。
  # 例如會找到：'2024年05月.xlsx' 或 '物理薪資_2024_05_final.xlsx'
  # ====================================================
  pattern = os.path.join(folder_path, f'*{year_str}*{month_str}*.xlsx')
  matched_files = glob.glob(pattern)

  if matched_files:
    # 如果同時找到多個符合年月條件的檔案，則回傳「最後修改時間 (getmtime)」最新的那一個
    return max(matched_files, key=os.path.getmtime)

  # ====================================================
  # 讀取方式 2：退而求其次，尋找資料夾內「最新修改」的任何 Excel 檔
  # 情境：老師上傳的檔案名稱忘記打上年月，或是還沒上傳本月的新檔案。
  # 邏輯：掃描該資料夾下所有的 .xlsx 檔案，不論檔名是什麼，直接抓取「最後被修改過」的那一個檔案當作目標。
  # ====================================================
  all_excel_files = glob.glob(os.path.join(folder_path, '*.xlsx'))
  if all_excel_files:
    return max(all_excel_files, key=os.path.getmtime)

  # ====================================================
  # 讀取方式 3：完全找不到檔案時的「預設防呆檔名」
  # 情境：這是一個全新的科目，資料夾裡面空空如也，完全沒有任何 Excel 檔案。
  # 邏輯：為了避免程式回傳 None 導致後續讀取時崩潰，這裡會拼湊一個「預設的虛擬路徑」回傳。
  # 後續的 send_bills_logic 拿到這個路徑後，用 os.path.exists 檢查發現檔案不存在，就能優雅地回覆錯誤訊息給老師。
  # ====================================================
  return os.path.join(folder_path, f'薪資計算器{year_str}(NEW).xlsx')


def load_data():
  if os.path.exists(DATA_FILE_PATH):
    try:
      with open(DATA_FILE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        # 直接回傳，不需要再做 List 的轉型檢查
        return data.get('verified', {})
    except Exception:
      pass
  return {}


def save_data(verified):
  data = {'verified': verified}
  with open(DATA_FILE_PATH, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=4)


@app.route("/", methods=["GET"])
def health_check():
  return "Line Bot is alive!", 200


@app.route('/callback', methods=['POST'])
def callback():
  signature = request.headers['X-Line-Signature']
  body = request.get_data(as_text=True)
  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return 'OK'

@handler.add(MessageEvent, message=FileMessageContent)
def handle_file_message(event):
    user_id = event.source.user_id
    message_id = event.message.id
    original_file_name = event.message.file_name

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # 1. 檢查檔案格式是否為 Excel (.xlsx)
        if not original_file_name.endswith('.xlsx'):
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='格式錯誤！請上傳 .xlsx 結尾的 Excel 檔案。')]
                )
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
                if user_id in sub_info['teachers']:
                    available_subjects.append(sub_code)

        if not available_subjects:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='⚠️ 您目前沒有權限上傳任何科目的檔案。')]
                )
            )
            return

        try:
            # 3. 先把檔案下載下來
            line_bot_blob_api = MessagingApiBlob(api_client)
            file_content = line_bot_blob_api.get_message_content(message_id)

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
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)]))

            # ==========================================
            # 情境 B：有多個科目，先暫存並跳出按鈕讓老師選
            # ==========================================
            else:
                # 暫存在最外層的 data 資料夾，並用 message_id 命名避免衝突
                temp_path = os.path.join(DATA_FOLDER, f"temp_{message_id}.xlsx")
                with open(temp_path, 'wb') as f:
                    f.write(file_content)

                # 建立科目選擇按鈕
                items = []
                for sub_code in available_subjects:
                    sub_name = SUBJECT_INFO[sub_code]['name']
                    # 將資訊藏在按鈕回傳的 data 裡
                    postback_data = f"action=upload_sub&msg_id={message_id}&sub={sub_code}"
                    
                    items.append(
                        QuickReplyItem(
                            action=PostbackAction(
                                label=sub_name, 
                                data=postback_data, 
                                display_text=f"上傳至：{sub_name}"
                            )
                        )
                    )

                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text="請選擇這份名單是屬於哪個科目的？",
                                quick_reply=QuickReply(items=items)
                            )
                        ]
                    )
                )

        except Exception as e:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f'❌ 檔案處理失敗：{str(e)}')]
                )
            )

@handler.add(PostbackEvent)
def handle_postback(event):
    # 點擊按鈕的老師 ID
    teacher_id = event.source.user_id 
    
    # 解析按鈕帶過來的隱藏資料
    postback_data = dict(parse_qsl(event.postback.data))
    action = postback_data.get('action')

    if not action:
        return

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # ==========================================
        # 處理路線 A：檔案上傳的科目選擇
        # ==========================================
        if action == 'upload_sub':
            msg_id = postback_data.get('msg_id')
            sub_code = postback_data.get('sub')
            
            temp_path = os.path.join(DATA_FOLDER, f"temp_{msg_id}.xlsx")
            
            # 檢查暫存檔是否還在
            if not os.path.exists(temp_path):
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text="⚠️ 檔案已遺失或操作過期，請重新上傳檔案。")]))
                return
                
            if sub_code not in SUBJECT_INFO:
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text="⚠️ 找不到該科目的設定檔。")]))
                return
                
            target_folder = SUBJECT_INFO[sub_code]['folder']
            subject_name = SUBJECT_INFO[sub_code]['name']
            
            if not os.path.exists(target_folder):
                os.makedirs(target_folder)
                
            now = datetime.datetime.now()
            new_file_name = f"{now.year}年{now.month:02d}月.xlsx"
            final_path = os.path.join(target_folder, new_file_name)
            
            is_overwrite = os.path.exists(final_path)
            
            try:
                # 把暫存檔移動並重新命名到目標資料夾中
                os.replace(temp_path, final_path)
                
                reply_msg = f'✅ 成功接收【{subject_name}】名單！\n已{"覆蓋舊檔並" if is_overwrite else ""}自動儲存為：【{new_file_name}】\n可以輸入「發送帳單」來進行作業了。'
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)]))
            except Exception as e:
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=f"❌ 存檔失敗：{str(e)}")]))
            
            return # 處理完畢，提早結束！

        # ==========================================
        # 處理路線 B：家長綁定審核 (同意 / 拒絕)
        # ==========================================
        if action in ['approve', 'reject']:
            parent_uid = postback_data.get('uid')
            bound_string = postback_data.get('data')

            if not parent_uid or not bound_string:
                return

            # 安全防護：確保按按鈕的人真的是系統裡的老師
            if teacher_id not in ALL_TEACHER_IDS:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text='您沒有審核權限。')]
                    )
                )
                return

            if action == 'approve':
                # 1. 拆解出「科目代碼」與「學生學號--姓名」
                sub_code, student_info = bound_string.split('-', 1)

                # 2. 將資料正式寫入 JSON (採用巢狀字典結構)
                verified_bindings = load_data()
                
                # (1) 確保該家長擁有專屬的字典
                if parent_uid not in verified_bindings:
                    verified_bindings[parent_uid] = {}
                
                # (2) 確保該家長底下的該科目擁有陣列
                if sub_code not in verified_bindings[parent_uid]:
                    verified_bindings[parent_uid][sub_code] = []
                
                # (3) 檢查不重複後，寫入乾淨的「學號--姓名」
                if student_info not in verified_bindings[parent_uid][sub_code]:
                    verified_bindings[parent_uid][sub_code].append(student_info)
                    save_data(verified_bindings)

                # 2. 回覆老師 (為了讓老師看懂，還是顯示完整的 bound_string)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f'✅ 已同意綁定：\n{bound_string}')]
                    )
                )

                # 3. 通知家長
                try:
                    # 嘗試反向解析出科目名稱，讓家長看得懂
                    sub_name = SUBJECT_INFO.get(sub_code, {}).get('name', '該')
                    
                    line_bot_api.push_message(
                        push_message_request=PushMessageRequest(
                            to=parent_uid,
                            messages=[TextMessage(text=f'🎉 您的綁定申請已通過！\n成功綁定【{sub_name}】課程（{student_info}），未來將會在此收到帳單。')]
                        )
                    )
                except Exception as e:
                    print(f"通知家長失敗: {e}")
                
                # 4. 通知同學科的其他老師是誰審核的
                try:
                    # 取得按下同意按鈕的老師 LINE 名稱
                    teacher_profile = line_bot_api.get_profile(teacher_id)
                    teacher_name = teacher_profile.display_name
                except Exception:
                    teacher_name = "某位老師"

                # 抓取該科目的所有老師名單
                sub_teachers = SUBJECT_INFO.get(sub_code, {}).get('teachers', [])
                
                for t_id in sub_teachers:
                    # 排除掉自己，只通知「其他」老師
                    if t_id != teacher_id:
                        try:
                            line_bot_api.push_message(
                                push_message_request=PushMessageRequest(
                                    to=t_id,
                                    messages=[
                                        TextMessage(
                                            text=f'ℹ️ 審核動態更新\n老師「{teacher_name}」已核准了【{sub_name}】學生（{student_info}）的綁定申請！'
                                        )
                                    ]
                                )
                            )
                        except Exception as e:
                            print(f"通知其他老師 {t_id} 失敗: {e}")

            elif action == 'reject':
                # 1. 回覆老師
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f'❌ 已拒絕綁定：\n{bound_string}')]
                    )
                )

                # 2. 通知家長
                try:
                    line_bot_api.push_message(
                        push_message_request=PushMessageRequest(
                            to=parent_uid,
                            messages=[TextMessage(text=f'⚠️ 您的綁定申請已被老師拒絕。\n資料：{bound_string}\n如有疑問請聯繫您的指導老師。')]
                        )
                    )
                except Exception as e:
                    pass

        # ==========================================
        # 處理路線 C：選擇科目後執行帳單發送
        # ==========================================
        if action == 'exec_bill':
            mode = postback_data.get('mode')       # 'batch' 或 'single'
            sub_code = postback_data.get('sub')    # 科目代碼 或 'all'

            target_student_id = postback_data.get('sid')
            if target_student_id == 'none':
                target_student_id = None
            lookback_months = int(postback_data.get('lb', 6))

            verified_bindings = load_data()

            # 決定要發送的科目清單
            subjects_to_send = []
            if sub_code == 'all':
                subjects_to_send = list(SUBJECT_INFO.keys())
            else:
                subjects_to_send = [sub_code]

            sub_names_str = "、".join([SUBJECT_INFO[c]['name'] for c in subjects_to_send if c in SUBJECT_INFO])

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f'⏳ 收到！正在背景為您處理【{sub_names_str}】的帳單（回溯 {lookback_months} 個月），完成後會主動回報，請稍候...')]
                )
            )

            def background_exec_bill_task(teacher_id, bindings, subjects, s_id, lb_months, batch_mode):
                with ApiClient(configuration) as bg_api_client:
                    bg_line_bot_api = MessagingApi(bg_api_client)
                    for scode in subjects:
                        # 判斷是單發還是批次發送
                        if batch_mode == 'single':
                            result_msg = send_bills_logic(bg_line_bot_api, bindings, subject_code=scode, target_student_id=s_id, lookback_months=lb_months)
                        else:
                            result_msg = send_bills_logic(bg_line_bot_api, bindings, subject_code=scode, lookback_months=lb_months)
                        
                        bg_line_bot_api.push_message(
                            push_message_request=PushMessageRequest(to=teacher_id, messages=[TextMessage(text=result_msg)])
                        )

            thread = threading.Thread(target=background_exec_bill_task, args=(teacher_id, verified_bindings, subjects_to_send, target_student_id, lookback_months, mode))
            thread.start()
            return

        # ==========================================
        # 處理路線 D：選擇要修改說明的科目
        # ==========================================
        if action == 'select_edit_sub':
            sub_code = postback_data.get('sub')
            
            if sub_code not in SUBJECT_INFO:
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text="⚠️ 找不到該科目的設定。")]))
                return
                
            # 記錄狀態
            PENDING_PAYMENT_EDIT[teacher_id] = sub_code
            subject_name = SUBJECT_INFO[sub_code]['name']
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=f'📝 您已選擇修改【{subject_name}】的繳費說明。\n\n請直接在聊天室輸入您想要設定的「新繳費說明內容」：'
                        )
                    ]
                )
            )
            return

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  user_id = event.source.user_id
  text = event.message.text.strip()

  verified_bindings = load_data()

  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)

    # ==========================
    # 0. 檢查是否正在進行「修改說明」的第二階段輸入
    # ==========================
    if user_id in PENDING_PAYMENT_EDIT:
        sub_code = PENDING_PAYMENT_EDIT.pop(user_id) # 取出後清除狀態，避免卡住
        subject_name = SUBJECT_INFO.get(sub_code, {}).get('name', '該科目')
        
        # 執行寫入
        success = update_subject_payment_info(sub_code, text)
        if success:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=(
                                f'✅ 成功更新【{subject_name}】的繳費說明！\n\n'
                                f'【目前最新的說明內容】\n{text}'
                            )
                        )
                    ]
                )
            )
        else:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='❌ 寫入設定檔失敗，請稍後再試。')]
                )
            )
        return

    # ==========================
    # 1. 老師專屬指令處理
    # ==========================
    if user_id in ALL_TEACHER_IDS or user_id in ADMIN_USER_IDS:
      
      # --------------------------
      # 批次發送帳單區塊
      # --------------------------
      if text.startswith('發送帳單'):
        parts = text.split()
        lookback_months = 6  # 預設回溯 6 個月
        if len(parts) > 1:
            try: lookback_months = max(1, int(parts[1]))
            except ValueError: pass

        # 自動找出該使用者負責的科目
        target_subjects = []
        if user_id in ADMIN_USER_IDS:
            target_subjects = list(SUBJECT_INFO.keys())
        else:
            for sub_code, sub_info in SUBJECT_INFO.items():
                if user_id in sub_info['teachers']:
                    target_subjects.append(sub_code)

        if not target_subjects:
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text='⚠️ 您目前沒有被分配到任何科目，無法發送帳單。')]))
            return

        # 如果只有一個科目，直接背景發送
        if len(target_subjects) == 1:
            sub_code = target_subjects[0]
            sub_name = SUBJECT_INFO[sub_code]['name']
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f'⏳ 系統已開始為您處理【{sub_name}】的帳單（回溯 {lookback_months} 個月），請稍候...')]
                )
            )

            def background_single_subject_task(teacher_id, bindings, s_code, lb_months):
                with ApiClient(configuration) as bg_api_client:
                    bg_line_bot_api = MessagingApi(bg_api_client)
                    result_msg = send_bills_logic(bg_line_bot_api, bindings, subject_code=s_code, lookback_months=lb_months)
                    bg_line_bot_api.push_message(push_message_request=PushMessageRequest(to=teacher_id, messages=[TextMessage(text=result_msg)]))

            thread = threading.Thread(target=background_single_subject_task, args=(user_id, verified_bindings, sub_code, lookback_months))
            thread.start()
            return

        # 如果有多個科目，跳出選擇按鈕 (Quick Reply)
        items = []
        for sub_code in target_subjects:
            sub_name = SUBJECT_INFO[sub_code]['name']
            postback_data = f"action=exec_bill&mode=batch&sub={sub_code}&sid=none&lb={lookback_months}"
            
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=sub_name, 
                        data=postback_data, 
                        display_text=f"發送【{sub_name}】帳單"
                    )
                )
            )

        # 管理員額外提供全部發送選項
        if user_id in ADMIN_USER_IDS:
            all_postback_data = f"action=exec_bill&mode=batch&sub=all&sid=none&lb={lookback_months}"
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label="🌐 全部科目", 
                        data=all_postback_data, 
                        display_text="發送所有科目帳單"
                    )
                )
            )

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text="📋 請選擇您要執行【批次發送帳單】的科目：",
                        quick_reply=QuickReply(items=items)
                    )
                ]
            )
        )
        return
      
      # --------------------------
      # 單發帳單區塊 (已獨立呼叫專屬函式)
      # --------------------------
      elif text.startswith('單發帳單'):
        parts = text.split()
        if len(parts) < 2:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='格式錯誤！請輸入例如：單發帳單 2601 或 單發帳單 2601 3')]
                )
            )
            return
            
        target_id = parts[1].strip()
        lookback_months = 6  # 預設回溯 6 個月
        if len(parts) > 2:
            try:
                lookback_months = max(1, int(parts[2]))
            except ValueError:
                pass

        # 自動找出該使用者負責的科目權限
        target_subjects = []
        if user_id in ADMIN_USER_IDS:
            target_subjects = list(SUBJECT_INFO.keys())
        else:
            for sub_code, sub_info in SUBJECT_INFO.items():
                if user_id in sub_info['teachers']:
                    target_subjects.append(sub_code)

        if not target_subjects:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='⚠️ 您目前沒有被分配到任何科目。')]
                )
            )
            return

        # 如果只有一個科目，直接在背景執行單發帳單
        if len(target_subjects) == 1:
            sub_code = target_subjects[0]
            sub_name = SUBJECT_INFO[sub_code]['name']
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f'⏳ 系統已開始為您處理【{sub_name}】的單發帳單（學號：{target_id}，回溯 {lookback_months} 個月），請稍候...')]
                )
            )

            def background_single_subject_task(teacher_id, bindings, s_code, t_id, lb_months):
                with ApiClient(configuration) as bg_api_client:
                    bg_line_bot_api = MessagingApi(bg_api_client)
                    # 呼叫共用的發送邏輯，並帶入 target_student_id
                    result_msg = send_bills_logic(bg_line_bot_api, bindings, subject_code=s_code, target_student_id=t_id, lookback_months=lb_months)
                    bg_line_bot_api.push_message(
                        push_message_request=PushMessageRequest(to=teacher_id, messages=[TextMessage(text=result_msg)])
                    )

            thread = threading.Thread(target=background_single_subject_task, args=(user_id, verified_bindings, sub_code, target_id, lookback_months))
            thread.start()
            return

        # 如果有多個科目，跳出選擇按鈕 (Quick Reply) 讓老師挑選要從哪一科發送
        items = []
        for sub_code in target_subjects:
            sub_name = SUBJECT_INFO[sub_code]['name']
            # 模式設定為 single，並把學號與回溯月數包進 data 裡
            postback_data = f"action=exec_bill&mode=single&sub={sub_code}&sid={target_id}&lb={lookback_months}"
            
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=sub_name, 
                        data=postback_data, 
                        display_text=f"單發【{sub_name}】帳單"
                    )
                )
            )

        # 管理員額外提供全部發送的選項（可從所有科目中尋找該學號並發送）
        if user_id in ADMIN_USER_IDS:
            all_postback_data = f"action=exec_bill&mode=single&sub=all&sid={target_id}&lb={lookback_months}"
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label="🌐 全部科目", 
                        data=all_postback_data, 
                        display_text="從所有科目尋找並發送此學生帳單"
                    )
                )
            )

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=f"📋 請選擇要從哪個科目發送學號【{target_id}】的帳單：",
                        quick_reply=QuickReply(items=items)
                    )
                ]
            )
        )
        return

      # --------------------------
      # 修改科目付款說明區塊 (引導式互動)
      # --------------------------
      elif text == '修改說明' or text.startswith('修改說明'):
        # 1. 自動找出該使用者負責的科目
        target_subjects = []
        if user_id in ADMIN_USER_IDS:
            target_subjects = list(SUBJECT_INFO.keys())
        else:
            for sub_code, sub_info in SUBJECT_INFO.items():
                if user_id in sub_info['teachers']:
                    target_subjects.append(sub_code)

        if not target_subjects:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='⚠️ 您目前沒有被分配到任何科目。')]
                )
            )
            return

        # 2. 如果只有一個科目，直接進入該科目的修改狀態
        if len(target_subjects) == 1:
            sub_code = target_subjects[0]
            sub_name = SUBJECT_INFO[sub_code]['name']
            
            # 記錄狀態：這位老師接下來傳的訊息就是要給這個科目的新內容
            PENDING_PAYMENT_EDIT[user_id] = sub_code
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=f'📝 您目前正準備修改【{sub_name}】的繳費說明。\n\n請直接在聊天室輸入您想要設定的「新繳費說明內容」：'
                        )
                    ]
                )
            )
            return

        # 3. 如果有多個科目，跳出選擇按鈕 (Quick Reply)
        items = []
        for sub_code in target_subjects:
            sub_name = SUBJECT_INFO[sub_code]['name']
            # 將動作設為 select_edit_sub
            postback_data = f"action=select_edit_sub&sub={sub_code}"
            
            items.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=sub_name, 
                        data=postback_data, 
                        display_text=f"修改【{sub_name}】說明"
                    )
                )
            )

        # 管理員額外提供全部修改的選項（或可依需求調整，此處以單科為主）
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text="📋 請選擇您想要修改哪個科目的繳費說明：",
                        quick_reply=QuickReply(items=items)
                    )
                ]
            )
        )
        return

    # ==========================
    # 2. 家長綁定處理 (解析科目編號、稱謂清理並精準推播)
    # ==========================
    # 檢查開頭是否為「我是」，且包含 '-' 和 '--'
    if text.startswith('我是') and '-' in text and '--' in text:
      try:
        # 1. 移除開頭的「我是」
        content = text.replace('我是', '').strip()

        # 2. 切割字串解析資訊
        # 先用第一個 '-' 切開，左邊是科目代碼，右邊是 學號--姓名
        subject_part, rest_part = content.split('-', 1)
        student_id, raw_student_name = rest_part.split('--', 1)

        subject_code = subject_part.strip()
        student_id = student_id.strip()
        student_name = raw_student_name.strip()

        # 3. 自動過濾掉結尾的稱謂 (讓名字保持乾淨，例如「小明的爸爸」變「小明」)
        for suffix in ['的爸爸', '的媽媽', '爸爸', '媽媽', '的家長', '家長', '阿公', '阿嬤']:
            if student_name.endswith(suffix):
                # 如果結尾符合，就把它切掉
                student_name = student_name[:-len(suffix)].strip()
                break

        # 組合完整的綁定字串存入 JSON (例如: 1-2601--小明)
        bound_string = f"{subject_code}-{student_id}--{student_name}"

        # 4. 檢查這個科目編號是否存在於我們的 config 系統中
        if subject_code not in SUBJECT_INFO:
          line_bot_api.reply_message(
              ReplyMessageRequest(
                  reply_token=event.reply_token,
                  messages=[TextMessage(text=f'❌ 找不到科目代碼【{subject_code}】。請確認您輸入的格式為「我是科目代碼-學號--姓名」。\n例如：我是1-2601--王小明')]
              )
          )
          return

        # 5. 抓取該科目的名稱與負責老師
        subject_data = SUBJECT_INFO[subject_code]
        subject_name = subject_data['name']
        target_teachers = subject_data['teachers']

        # 6. 回覆家長等待訊息
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=(
                            f'⏳ 已收到綁定請求！\n'
                            f'申請綁定【{subject_name}】課程：\n'
                            f'學號：【{student_id}】\n'
                            f'姓名：【{student_name}】\n'
                            f'已通知該科老師，請等候老師點擊確認。'
                        )
                    )
                ],
            )
        )

        # 7. 【精準推播】組合審核按鈕，傳給該科目的老師
        try:
          profile = line_bot_api.get_profile(user_id)
          parent_name = profile.display_name
        except Exception:
          parent_name = '家長'

        # 將資訊壓縮在按鈕的 data 裡 (LINE 限制 data 長度為 300 字元內)
        approve_data = f"action=approve&uid={user_id}&data={bound_string}"
        reject_data = f"action=reject&uid={user_id}&data={bound_string}"

        buttons_template = ButtonsTemplate(
            text=f"🔔 綁定審核\n家長「{parent_name}」申請綁定：\n【{subject_name}】{student_id} {student_name}",
            actions=[
                PostbackAction(label="✅ 同意綁定", data=approve_data),
                PostbackAction(label="❌ 拒絕", data=reject_data)
            ]
        )
        
        template_message = TemplateMessage(
            alt_text="收到新的綁定審核請求",
            template=buttons_template
        )

        for teacher_id in target_teachers:
          try:
            line_bot_api.push_message(
                push_message_request=PushMessageRequest(
                    to=teacher_id,
                    messages=[template_message]
                )
            )
          except Exception as e:
            print(f"通知老師 {teacher_id} 失敗: {e}")
            
        return # 處理完畢，結束這一回合
        
      except ValueError:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text='⚠️ 格式錯誤！請確認輸入格式為：我是科目編號-學號--姓名\n例如：我是1-2601--王小明')]
            )
        )
        return


def send_bills_logic(line_bot_api, verified_bindings, subject_code, target_student_id=None, lookback_months=6):
  # 1. 取得該科目的相關資訊與資料夾路徑
  sub_info = SUBJECT_INFO.get(subject_code)
  if not sub_info:
      return f'❌ 系統錯誤：找不到科目代碼 {subject_code} 的設定。'
  
  folder_path = sub_info['folder']
  subject_name = sub_info['name']
  
  # 🎯 讀取該科目專屬的付款資訊（如果沒填則給予預設文字）
  custom_payment_info = sub_info.get(
    'payment_info', 
    '可使用轉帳匯款，完成後再請您通知我一下，謝謝您！'
  )
  
  excel_file_path = get_current_month_excel_path(folder_path)

  if not os.path.exists(excel_file_path):
    return (
        f'找不到【{subject_name}】對應月份的 Excel 檔案，'
        '請確認是否已放置於該科目的專屬資料夾中。'
    )

  try:
    now = datetime.datetime.now()
    
    if now.month == 1:
        target_year = now.year - 1
        target_month = 12
    else:
        target_year = now.year
        target_month = now.month - 1
        
    student_unpaid_map = {}
    target_sheet = f"{str(target_year)[-2:]}年{target_month}月"
    processed_sheets = []

    # ==========================================
    # 讀取 Excel with 區塊，確保 Excel 讀取完畢後立刻釋放檔案鎖定
    # ==========================================
    with pd.ExcelFile(excel_file_path) as xls:
        # 動態依照輸入的月份進行回溯，如果 lookback_months 是 3，就會是 range(2, -1, -1)
        for i in range(lookback_months - 1, -1, -1):
            calc_month = target_month - i
            calc_year = target_year
            while calc_month <= 0:
                calc_month += 12
                calc_year -= 1
                
            short_year = str(calc_year)[-2:]
            sheet_name = f'{short_year}年{calc_month}月'
            
            actual_sheet = None
            if sheet_name in xls.sheet_names:
                actual_sheet = sheet_name
            elif i == 0:
                actual_sheet = xls.sheet_names[0]
                target_sheet = actual_sheet
                
            if not actual_sheet:
                continue
                
            processed_sheets.append(actual_sheet)
            df = pd.read_excel(xls, sheet_name=actual_sheet, header=None)

            if df.empty:
                continue

            header_row = df.iloc[0]
            col_idx_map = {}
            
            for c_idx, val in enumerate(header_row):
                val_str = str(val).strip()
                if '名字' in val_str or '姓名' in val_str: col_idx_map['name'] = c_idx
                elif '學號' in val_str: col_idx_map['id'] = c_idx
                elif '已繳' in val_str: col_idx_map['paid'] = c_idx
                elif '備註' in val_str: col_idx_map['remark'] = c_idx
                elif '時數小計' in val_str: col_idx_map['hours'] = c_idx
                elif '薪資' in val_str: col_idx_map['salary'] = c_idx
                elif '書籍/教材' in val_str: col_idx_map['book_fee'] = c_idx
                elif '單一學生小計' in val_str: col_idx_map['subtotal'] = c_idx

            if 'id' not in col_idx_map:
                continue
                
            if 'name' not in col_idx_map:
                col_idx_map['name'] = max(0, col_idx_map['id'] - 1)

            for r_idx in range(1, df.shape[0]):
                raw_id = df.iloc[r_idx, col_idx_map['id']]
                if pd.isna(raw_id):
                    continue
                    
                s_id = str(raw_id).strip()
                if s_id.endswith('.0'):
                    s_id = s_id[:-2]
                    
                if not s_id or s_id == '學號' or s_id == 'None':
                    continue

                # ==========================================
                # 【新增優化】：如果在讀取階段就發現學號不符，直接跳過，不浪費資源處理！
                # ==========================================
                if target_student_id and s_id != target_student_id:
                    continue
                # ==========================================

                raw_name = df.iloc[r_idx, col_idx_map['name']]
                s_name = str(raw_name).strip() if pd.notna(raw_name) else "未知姓名"

                is_paid = False
                if 'paid' in col_idx_map:
                    paid_val = df.iloc[r_idx, col_idx_map['paid']]
                    if pd.notna(paid_val):
                        p_str = str(paid_val).strip()
                        if p_str in ['1', '1.0', '已繳', 'V', 'v']:
                            is_paid = True
                
                if is_paid:
                    continue

                subtotal = 0
                if 'subtotal' in col_idx_map:
                    raw_sub = df.iloc[r_idx, col_idx_map['subtotal']]
                    try: subtotal = float(raw_sub) if pd.notna(raw_sub) else 0
                    except: subtotal = 0
                    
                if subtotal > 0:
                    hours = df.iloc[r_idx, col_idx_map['hours']] if 'hours' in col_idx_map else '略'
                    salary = df.iloc[r_idx, col_idx_map['salary']] if 'salary' in col_idx_map else '略'
                    book_fee = df.iloc[r_idx, col_idx_map['book_fee']] if 'book_fee' in col_idx_map else None
                    remark = df.iloc[r_idx, col_idx_map['remark']] if 'remark' in col_idx_map else None

                    if s_id not in student_unpaid_map:
                        student_unpaid_map[s_id] = {'name': s_name, 'records': []}
                        
                    student_unpaid_map[s_id]['records'].append({
                        'month_str': f"{calc_month}月份",
                        'hours': hours if pd.notna(hours) else '略',
                        'salary': salary if pd.notna(salary) else '略',
                        'subtotal': subtotal,
                        'book_fee': book_fee,
                        'remark': remark
                    })

    if not student_unpaid_map:
      return f'【{subject_name}】掃描了 {len(processed_sheets)} 個月份，目前所有學生皆已完成繳費或無欠款。'

    matched_excel_students = set()
    grand_total_amount = 0

    # ==========================================
    # 2. 讀取新的巢狀字典並比對並發送帳單
    # ==========================================
    for user_id, user_subjects in verified_bindings.items():
      # 只拿出該家長在這個科目的陣列 (如果沒綁定這科，回傳空陣列)
      bound_student_records = user_subjects.get(subject_code, [])
      if not bound_student_records:
          continue

      parent_student_details = []
      parent_total_amount = 0

      for bound_record in bound_student_records:
        if '--' in bound_record:
            bound_id = bound_record.split('--')[0].strip()
        else:
            bound_id = bound_record.strip()
            
        # 如果是「單發帳單」，跳過不符的學號
        if target_student_id and bound_id != target_student_id:
            continue
        
        if bound_id in student_unpaid_map:
          student_data = student_unpaid_map[bound_id]
          already_counted = bound_id in matched_excel_students

          for record in student_data['records']:
              record['name'] = student_data['name']
              parent_student_details.append(record)
              parent_total_amount += record['subtotal']
              
              if not already_counted:
                  grand_total_amount += record['subtotal']
              
          matched_excel_students.add(bound_id)

      if parent_student_details:
        message_content = f'親愛的家長您好\n\n跟您報一下'

        for record in parent_student_details:
          message_content += (
              f'\n--------------------\n'
              f'{record["name"]}，{record["month_str"]}的【{subject_name}】學費：\n\n'
              f'• 上課時數：{record["hours"]}\n'
              f'• 薪資/單價：{record["salary"]}'
          )

          b_fee = record.get("book_fee")
          if pd.notna(b_fee) and str(b_fee).strip() not in ['', '0', '0.0', 'None']:
              message_content += f'\n• 書籍/教材：{b_fee}'

          message_content += f'\n• 單一學生小計：{record["subtotal"]:g} 元'

          rmk = record.get("remark")
          if pd.notna(rmk) and str(rmk).strip() not in ['', 'None']:
              message_content += f'\n• 備註：\n{rmk}'

        message_content += (
            f'\n--------------------\n'
            f'💰 本期待繳總計金額：{parent_total_amount:g} 元'
        )

        message_content += (f'\n--------------------\n'
                            f'{custom_payment_info}')

        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=user_id, messages=[TextMessage(text=message_content)]
            )
        )
        time.sleep(0.1) # 增加微小延遲，避免 API 阻擋

    # 如果是單獨發送，回傳單獨發送的結果
    if target_student_id:
        if target_student_id not in matched_excel_students:
            return f'⚠️ 無法發送：找不到編號【{target_student_id}】的【{subject_name}】欠費資料，或是該學生尚未綁定家長。'
        return f'✅ 已成功單獨發送編號【{target_student_id}】的【{subject_name}】帳單！\n（已掃描過去 {lookback_months} 個月紀錄）'

    # 全部發送的統計結果
    total_unpaid_students = len(student_unpaid_map)
    unsent_count = total_unpaid_students - len(matched_excel_students)

    return (
        f'【{subject_name} 帳單發送統計（基準月：{target_sheet}）】\n'
        f'• 成功發送學生筆數：{len(matched_excel_students)} 筆\n'
        f'• 欠費未發送筆數：{unsent_count} 筆（尚未綁定家長）\n'
        f'• 總計欠費學生筆數：{total_unpaid_students} 筆\n'
        f'• 本期已發送總金額：{grand_total_amount:g} 元\n'
        f'（已掃描過去 {lookback_months} 個月紀錄）'
    )
  except Exception as e:
    return f'發送【{subject_name}】帳單時發生錯誤: {str(e)}'

# scheduler = BackgroundScheduler()

# @scheduler.scheduled_job('cron', day=1, hour=9, minute=0)
# def scheduled_send_bills():
#   verified_bindings = load_data()
#   with ApiClient(configuration) as api_client:
#     line_bot_api = MessagingApi(api_client)
#     send_bills_logic(line_bot_api, verified_bindings)

# scheduler.start()

if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000, debug=True)