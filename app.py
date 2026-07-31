import datetime
import glob
import json
import logging
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

# 載入本機的 .env 檔案
load_dotenv()

app = Flask(__name__)

# 關閉 Werkzeug 每筆連線的 200 OK 刷屏日誌
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# 從環境變數讀取 LINE 金鑰、老師 ID 以及圖文選單 ID
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', '')
teacher_ids_raw = os.getenv('TEACHER_USER_IDS', '')
TEACHER_USER_IDS = [uid.strip() for uid in teacher_ids_raw.split(',') if uid.strip()]

# v3 的 API 與 Handler 初始化方式
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 資料儲存檔案路徑
DATA_FILE_PATH = 'bindings.json'
DATA_FOLDER = 'data'


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
    if user_id in TEACHER_USER_IDS:
      if text == '發送帳單':
        # 全部發送
        result_msg = send_bills_logic(line_bot_api, verified_bindings)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=result_msg)],
            )
        )
        return
      
      elif text.startswith('單發帳單'):
        # 單獨發送，例如輸入 "單發帳單 2601"
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='格式錯誤！請輸入例如：單發帳單 2601')]
                )
            )
            return
            
        target_id = parts[1].strip()
        # 呼叫發送邏輯，並傳入指定的學號
        result_msg = send_bills_logic(line_bot_api, verified_bindings, target_student_id=target_id)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=result_msg)],
            )
        )
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


def send_bills_logic(line_bot_api, verified_bindings, target_student_id=None):
  excel_file_path = get_current_month_excel_path()

  if not os.path.exists(excel_file_path):
    return (
        f'找不到對應月份的 Excel 檔案 ({excel_file_path})，'
        '請確認是否已放置於 data 資料夾中。'
    )

  try:
    xls = pd.ExcelFile(excel_file_path)
    now = datetime.datetime.now()

    target_year = now.year
    target_month = now.month
    
    # 計算「上一個月」的年份與月份 (當作基準月)
    # if now.month == 1:
    #     target_year = now.year - 1
    #     target_month = 12
    # else:
    #     target_year = now.year
    #     target_month = now.month - 1
        
    # 準備用來存放所有需繳費紀錄的字典：{ s_id: { 'name': 姓名, 'records': [各月明細] } }
    student_unpaid_map = {}
    
    # 統計用變數
    target_sheet = f"{str(target_year)[-2:]}年{target_month}月" # 基準月名稱
    processed_sheets = []

    # 往前推算 6 個月 (包含基準月，由舊到新排序)
    for i in range(5, -1, -1):
        calc_month = target_month - i
        calc_year = target_year
        while calc_month <= 0:
            calc_month += 12
            calc_year -= 1
            
        short_year = str(calc_year)[-2:]
        sheet_name = f'{short_year}年{calc_month}月'
        
        # 確認該月份工作表是否存在
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

        # 1. 尋找「學號」及「已繳」所在的欄位索引
        id_col_idx = 1
        name_col_idx = 0
        paid_col_idx = -1
        
        for r_idx, row in df.iterrows():
          for c_idx, val in enumerate(row):
            val_str = str(val).strip()
            if val_str == '學號':
              id_col_idx = c_idx
              name_col_idx = max(0, c_idx - 1)
            elif val_str == '已繳':
              paid_col_idx = c_idx

        # 2. 收集此工作表中的學生資料與繳費狀態
        for r_idx in range(df.shape[0]):
          raw_id = df.iloc[r_idx, id_col_idx]
          if pd.notna(raw_id):
            s_id = str(raw_id).strip()
            if s_id.endswith('.0'):
                s_id = s_id[:-2]
                
            if s_id and s_id != '學號' and s_id != 'None':
                # ==========================================
                # 【新增】：如果有指定學號，且目前學號不符，就跳過
                # ==========================================
                if target_student_id and s_id != target_student_id:
                    continue
                raw_name = df.iloc[r_idx, name_col_idx]
                s_name = str(raw_name).strip() if pd.notna(raw_name) else "未知姓名"
                
                # 判斷是否已繳
                # 【修改重點】如果沒有「已繳」欄位 (paid_col_idx == -1)，預設為 True (已繳)
                is_paid = True if paid_col_idx == -1 else False
                
                if paid_col_idx != -1:
                    paid_val = df.iloc[r_idx, paid_col_idx]
                    if pd.notna(paid_val):
                        p_str = str(paid_val).strip()
                        if p_str in ['1', '1.0', '已繳', 'V', 'v']:
                            is_paid = True
                
                # 若已繳納，則跳過不記錄
                if is_paid:
                    continue
                    
                # 3. 讀取未繳費的薪資與小計
                hours, salary, subtotal, book_fee, remark = '略', '略', 0, None, None
                for check_r in range(max(0, r_idx - 3), r_idx):
                    for check_c in range(name_col_idx, df.shape[1]):
                        header_val = str(df.iloc[check_r, check_c]).strip()
                        if '時數小計' in header_val: hours = df.iloc[r_idx, check_c]
                        elif '薪資' in header_val: salary = df.iloc[r_idx, check_c]
                        elif '書籍/教材' in header_val: book_fee = df.iloc[r_idx, check_c]
                        elif '備註' in header_val: remark = df.iloc[r_idx, check_c]
                        elif '單一學生小計' in header_val:
                            raw_sub = df.iloc[r_idx, check_c]
                            try: subtotal = float(raw_sub) if pd.notna(raw_sub) else 0
                            except: subtotal = 0

                if subtotal > 0:
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
      return f'掃描了 {len(processed_sheets)} 個月份的工作表，目前所有學生皆已完成繳費或無欠款。'

    matched_excel_students = set()
    grand_total_amount = 0

    # 4. 比對並發送帳單
    for user_id, bound_student_records in verified_bindings.items():
      parent_student_details = []
      parent_total_amount = 0

      for bound_record in bound_student_records:
        if '--' in bound_record:
            bound_id = bound_record.split('--')[0].strip()
        else:
            bound_id = bound_record.strip()

        # ==========================================
        # 【新增】：如果有指定學號，且目前學號不符，就跳過
        # ==========================================
        if target_student_id and bound_id != target_student_id:
            continue
        
        if bound_id in student_unpaid_map:
          student_data = student_unpaid_map[bound_id]
          
          # 【修改重點】檢查這個學生的學號是否已經被處理過（例如父母都綁定同一個學生）
          already_counted = bound_id in matched_excel_students

          for record in student_data['records']:
              record['name'] = student_data['name']
              parent_student_details.append(record)
              parent_total_amount += record['subtotal']
              
              # 只有在第一次遇到這個學生時，才把小計加入老師的「總發送金額」統計中
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

    total_unpaid_students = len(student_unpaid_map)
    unsent_count = total_unpaid_students - len(matched_excel_students)

    return (
        f'【帳單發送統計結果（基準月：{target_sheet}）】\n'
        f'• 成功發送學生筆數：{len(matched_excel_students)} 筆\n'
        f'• 欠費未發送筆數：{unsent_count} 筆（尚未綁定家長）\n'
        f'• 總計欠費學生筆數：{total_unpaid_students} 筆\n'
        f'• 本期已發送總金額：{grand_total_amount:g} 元\n'
        f'（已掃描過去 6 個月紀錄）'
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