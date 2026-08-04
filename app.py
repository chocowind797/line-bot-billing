import datetime
import glob
import json
import logging
import threading
import time
import os
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
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FileMessageContent
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
    reload_config  # 新增這一行
)

app = Flask(__name__)

# 關閉 Werkzeug 每筆連線的 200 OK 刷屏日誌
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# v3 的 API 與 Handler 初始化方式
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


def get_current_month_excel_path():
  if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

  now = datetime.datetime.now()
  year_str = str(now.year)
  month_str = f'{now.month:02d}' 

  pattern = os.path.join(DATA_FOLDER, f'*{year_str}*{month_str}*.xlsx')
  matched_files = glob.glob(pattern)

  if matched_files:
    return max(matched_files, key=os.path.getmtime)

  all_excel_files = glob.glob(os.path.join(DATA_FOLDER, '*.xlsx'))
  if all_excel_files:
    return max(all_excel_files, key=os.path.getmtime)

  return os.path.join(DATA_FOLDER, f'薪資計算器{year_str}(NEW).xlsx')


def load_data():
  if os.path.exists(DATA_FILE_PATH):
    try:
      with open(DATA_FILE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        # 只讀取已驗證的綁定資料
        verified = data.get('verified', {})

        # 確保舊資料格式為 List
        for uid in list(verified.keys()):
          if isinstance(verified[uid], str):
            verified[uid] = [verified[uid]]

        return verified
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

        # 1. 安全機制：確認傳送檔案的人是不是老師
        if user_id not in TEACHER_USER_IDS:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='抱歉，只有老師權限可以上傳檔案。')]
                )
            )
            return

        # 2. 檢查檔案格式是否為 Excel (.xlsx)
        if not original_file_name.endswith('.xlsx'):
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='格式錯誤！請上傳 .xlsx 結尾的 Excel 檔案。')]
                )
            )
            return

        # 3. 確保 data 資料夾存在
        if not os.path.exists(DATA_FOLDER):
            os.makedirs(DATA_FOLDER)

        try:
            # 4. 透過 Blob API 下載檔案內容
            line_bot_blob_api = MessagingApiBlob(api_client)
            file_content = line_bot_blob_api.get_message_content(message_id)

            # 5. 動態產生新的檔案名稱
            now = datetime.datetime.now()
            new_file_name = f"{now.year}年{now.month:02d}月.xlsx"
            file_path = os.path.join(DATA_FOLDER, new_file_name)

            # 新增：檢查檔案是否已經存在，用來決定回覆的文字
            is_overwrite = os.path.exists(file_path)

            # 6. 寫入檔案 (若同名會自動覆蓋)
            with open(file_path, 'wb') as f:
                f.write(file_content)

            # 根據是否覆蓋，給予不同的提示訊息
            if is_overwrite:
                reply_msg = f'✅ 成功接收檔案！\n已「覆蓋」舊檔並更新為：【{new_file_name}】\n現在可以輸入「發送帳單」來進行作業了。'
            else:
                reply_msg = f'✅ 成功接收檔案！\n已自動重新命名並儲存為：【{new_file_name}】\n現在可以輸入「發送帳單」來進行作業了。'

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)]
                )
            )
        except Exception as e:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f'❌ 檔案下載失敗：{str(e)}')]
                )
            )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  user_id = event.source.user_id
  text = event.message.text.strip()

  verified_bindings = load_data()

  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)

    # ==========================
    # 1. 老師專屬指令處理
    # ==========================
    # ==========================
    # 1. 老師專屬指令處理
    # ==========================
    if user_id in TEACHER_USER_IDS:
      if text.startswith('發送帳單'):
        # 拆解指令與數字
        parts = text.split()
        lookback_months = 6  # 預設回溯 6 個月
        if len(parts) > 1:
            try:
                lookback_months = max(1, int(parts[1])) # 確保至少為 1
            except ValueError:
                pass

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=f'⏳ 系統已開始在背景處理並發送帳單（回溯 {lookback_months} 個月），完成後會主動通知您，請稍候...')]
            )
        )

        def background_send_task(teacher_id, bindings, lb_months):
            with ApiClient(configuration) as bg_api_client:
                bg_line_bot_api = MessagingApi(bg_api_client)
                result_msg = send_bills_logic(bg_line_bot_api, bindings, lookback_months=lb_months)
                bg_line_bot_api.push_message(
                    push_message_request=PushMessageRequest(
                        to=teacher_id, 
                        messages=[TextMessage(text=result_msg)]
                    )
                )

        thread = threading.Thread(target=background_send_task, args=(user_id, verified_bindings, lookback_months))
        thread.start()
        return
      
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
                
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=f'⏳ 系統已開始在背景處理單發帳單（編號 {target_id}，回溯 {lookback_months} 個月），請稍候...')]
            )
        )

        def background_single_task(teacher_id, bindings, t_id, lb_months):
            with ApiClient(configuration) as bg_api_client:
                bg_line_bot_api = MessagingApi(bg_api_client)
                result_msg = send_bills_logic(bg_line_bot_api, bindings, target_student_id=t_id, lookback_months=lb_months)
                bg_line_bot_api.push_message(
                    push_message_request=PushMessageRequest(
                        to=teacher_id, 
                        messages=[TextMessage(text=result_msg)]
                    )
                )

        thread = threading.Thread(target=background_single_task, args=(user_id, verified_bindings, target_id, lookback_months))
        thread.start()
        return

    # ==========================
    # 2. 家長綁定處理 (無須審核)
    # ==========================
    if text.startswith('我是'):
      content = text.replace('我是', '').strip()
      
      # 驗證格式是否包含 '--'
      if '--' not in content:
          line_bot_api.reply_message(
              ReplyMessageRequest(
                  reply_token=event.reply_token,
                  messages=[
                      TextMessage(
                          text='格式錯誤！請依照以下格式輸入：\n我是 編號--姓名\n例如：我是 A01--王小明'
                      )
                  ]
              )
          )
          return

      # 拆解編號與姓名並組合為識別碼
      parts = content.split('--', 1)
      student_id = parts[0].strip()
      student_name = parts[1].strip()
      bound_string = f"{student_id}--{student_name}"

      if user_id not in verified_bindings:
        verified_bindings[user_id] = []

      # 避免重複綁定，若未綁定則直接加入「已驗證(verified)」名單
      if bound_string not in verified_bindings[user_id]:
        verified_bindings[user_id].append(bound_string)
        save_data(verified_bindings)

      line_bot_api.reply_message(
          ReplyMessageRequest(
              reply_token=event.reply_token,
              messages=[
                  TextMessage(
                      text=(
                          f'綁定成功！🎉\n'
                          f'已將您的帳號綁定至：\n'
                          f'編號：【{student_id}】\n'
                          f'姓名：【{student_name}】\n'
                          f'未來將可接收該學生的繳費通知。'
                      )
                  )
              ],
          )
      )

      # 取得家長的 LINE 顯示名稱
      try:
        profile = line_bot_api.get_profile(user_id)
        parent_name = profile.display_name
      except Exception:
        parent_name = '家長'

      # 推播通知給所有老師
      for teacher_id in TEACHER_USER_IDS:
        try:
          line_bot_api.push_message(
              push_message_request=PushMessageRequest(
                  to=teacher_id,
                  messages=[
                      TextMessage(
                          text=(
                              f'【新增綁定通知】🔔\n'
                              f'家長「{parent_name}」已成功綁定學生：\n'
                              f'編號：【{student_id}】\n'
                              f'姓名：【{student_name}】'
                          )
                      )
                  ]
              )
          )
        except Exception as e:
          print(f"通知老師 {teacher_id} 失敗: {e}")
      return


def send_bills_logic(line_bot_api, verified_bindings, target_student_id=None, lookback_months=6):
  excel_file_path = get_current_month_excel_path()

  if not os.path.exists(excel_file_path):
    return (
        f'找不到對應月份的 Excel 檔案 ({excel_file_path})，'
        '請確認是否已放置於 data 資料夾中。'
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
    # 加入 with 區塊，確保 Excel 讀取完畢後立刻釋放檔案鎖定
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
    # ==========================================
    # 程式執行到這裡，離開了 with 區塊，Excel 檔案已經完全釋放並關閉！
    # ==========================================

    if not student_unpaid_map:
      return f'掃描了 {len(processed_sheets)} 個月份的工作表，目前所有學生皆已完成繳費或無欠款。'

    matched_excel_students = set()
    grand_total_amount = 0

    # ==========================================
    # 3. 比對並發送帳單 (含過濾與單發功能)
    # ==========================================
    for user_id, bound_student_records in verified_bindings.items():
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
        message_content = (
            f'親愛的家長您好\n\n跟您報一下'
        )

        for record in parent_student_details:
          message_content += (
              f'\n--------------------\n'
              f'{record["name"]}，{record["month_str"]}的物理學費：\n\n'
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
                            f'可使用LINE PAY轉帳或是匯款至\n'
                            f'玉山銀行代碼(808)帳號0299--979--299866\n'
                            f'如果是匯款的話\n'
                            f'匯款完後再請您通知我一下\n'
                            f'謝謝您唷，感恩感恩'
                            )

        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=user_id, messages=[TextMessage(text=message_content)]
            )
        )
        time.sleep(0.1) # 增加微小延遲，避免 API 阻擋

    # 如果是單獨發送，回傳單獨發送的結果
    if target_student_id:
        if target_student_id not in matched_excel_students:
            return f'⚠️ 無法發送：找不到編號【{target_student_id}】的欠費資料，或是該學生尚未綁定家長。'
        return f'✅ 已成功單獨發送編號【{target_student_id}】的帳單給家長！\n（已掃描過去 {lookback_months} 個月紀錄）'

    # 全部發送的統計結果
    total_unpaid_students = len(student_unpaid_map)
    unsent_count = total_unpaid_students - len(matched_excel_students)

    return (
        f'【帳單發送統計結果（基準月：{target_sheet}）】\n'
        f'• 成功發送學生筆數：{len(matched_excel_students)} 筆\n'
        f'• 欠費未發送筆數：{unsent_count} 筆（尚未綁定家長）\n'
        f'• 總計欠費學生筆數：{total_unpaid_students} 筆\n'
        f'• 本期已發送總金額：{grand_total_amount:g} 元\n'
        f'（已掃描過去 {lookback_months} 個月紀錄）'
    )
  except Exception as e:
    return f'發送帳單時發生錯誤: {str(e)}'

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