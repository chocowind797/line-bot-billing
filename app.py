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
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
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
        result_msg = send_bills_logic(line_bot_api, verified_bindings)
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


def send_bills_logic(line_bot_api, verified_bindings):
  excel_file_path = get_current_month_excel_path()

  if not os.path.exists(excel_file_path):
    return (
        f'找不到對應月份的 Excel 檔案 ({excel_file_path})，'
        '請確認是否已放置於 data 資料夾中。'
    )

  try:
    xls = pd.ExcelFile(excel_file_path)
    
    # 取得當前時間
    now = datetime.datetime.now()
    # 取得西元年後兩碼 (例如 2026 -> "26")
    short_year = str(now.year)[-2:]
    # 取得當前月份 (例如 7)
    current_month = now.month
    
    # 組合工作表名稱，例如 "26年7月"
    current_month_str = f'{short_year}年{current_month}月'

    # 檢查該名稱的工作表是否存在
    if current_month_str in xls.sheet_names:
        target_sheet = current_month_str
    else:
    # 如果找不到當月名稱，預設抓取第一個工作表
        target_sheet = xls.sheet_names[0]

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)

    # 1. 尋找「學號」所在的欄位索引 (預設為 1 即 B 欄，名稱預設為 A 欄)
    id_col_idx = 1
    name_col_idx = 0
    for r_idx, row in df.iterrows():
      for c_idx, val in enumerate(row):
        if str(val).strip() == '學號':
          id_col_idx = c_idx
          name_col_idx = max(0, c_idx - 1)
          break

    # 2. 收集所有學生資料 (以學號為判斷基準)
    excel_students = []
    for r_idx in range(df.shape[0]):
      raw_id = df.iloc[r_idx, id_col_idx]
      raw_name = df.iloc[r_idx, name_col_idx]
      
      if pd.notna(raw_id):
        s_id = str(raw_id).strip()
        # 處理 Pandas 讀取數字時可能產生的 .0 (例如 2601.0 -> 2601)
        if s_id.endswith('.0'):
            s_id = s_id[:-2]
            
        # 確認該儲存格是有效的學號，而非標題或空值
        if s_id and s_id != '學號' and s_id != 'None':
            s_name = str(raw_name).strip() if pd.notna(raw_name) else "未知姓名"
            excel_students.append({
                'id': s_id,
                'name': s_name,
                'row_idx': r_idx,
                'col_idx': name_col_idx
            })

    total_count = len(excel_students)
    sent_student_count = 0
    grand_total_amount = 0

    if total_count == 0:
      return f'在【{target_sheet}】中找不到任何含有學號的學生資料。'

    # 3. 讀取學生薪資與小計
    student_data_map = {}
    for student_info in excel_students:
      s_id = student_info['id']
      s_name = student_info['name']
      r_idx = student_info['row_idx']
      c_idx = student_info['col_idx']

      hours = '略'
      salary = '略'
      subtotal = 0
      book_fee = None
      remark = None

      # 往上找 3 列以內來對應標題列
      for check_r in range(max(0, r_idx - 3), r_idx):
        for check_c in range(c_idx, df.shape[1]):
          header_val = str(df.iloc[check_r, check_c]).strip()
          if '時數小計' in header_val:
            hours = df.iloc[r_idx, check_c]
          elif '薪資' in header_val:
            salary = df.iloc[r_idx, check_c]
          elif '書籍/教材' in header_val:
            book_fee = df.iloc[r_idx, check_c]
          elif '備註' in header_val:
            remark = df.iloc[r_idx, check_c]
          elif '單一學生小計' in header_val:
            raw_sub = df.iloc[r_idx, check_c]
            try:
              subtotal = float(raw_sub) if pd.notna(raw_sub) else 0
            except Exception:
              subtotal = 0

      # 將資料存入字典，使用「學號(s_id)」當作 Key
      student_data_map[s_id] = {
          'id': s_id,
          'name': s_name,
          'hours': hours if pd.notna(hours) else '略',
          'salary': salary if pd.notna(salary) else '略',
          'subtotal': subtotal,
          'book_fee': book_fee,
          'remark': remark,
      }

    matched_excel_students = set()

    # 4. 比對並發送帳單 (比對學號)
    for user_id, bound_student_records in verified_bindings.items():
      parent_student_details = []
      parent_total_amount = 0

      for bound_record in bound_student_records:
        # bound_record 格式為 "編號--姓名" (例如 "2601--威澄")，我們將編號切分出來
        if '--' in bound_record:
            bound_id = bound_record.split('--')[0].strip()
        else:
            bound_id = bound_record.strip()
        
        # 使用編號尋找該學生資料
        if bound_id in student_data_map:
          data = student_data_map[bound_id]
          parent_student_details.append(data)
          parent_total_amount += data['subtotal']
          matched_excel_students.add(bound_id)  # 記錄已發送的學號
          grand_total_amount += data['subtotal']

      if parent_student_details:
        message_content = (
            f'親愛的家長您好\n\n跟您報一下'
        )

        for s_info in parent_student_details:
          message_content += (
              f'\n--------------------\n'
              f'{s_info["name"]}，{current_month}月份的物理學費：\n\n'
              f'• 上課時數：{s_info["hours"]}\n'
              f'• 薪資/單價：{s_info["salary"]}'
          )

          b_fee = s_info.get("book_fee")
          if pd.notna(b_fee) and str(b_fee).strip() not in ['', '0', '0.0', 'None']:
              message_content += f'\n• 書籍/教材：{b_fee}'

          message_content += f'\n• 單一學生小計：{s_info["subtotal"]:g} 元'

          rmk = s_info.get("remark")
          if pd.notna(rmk) and str(rmk).strip() not in ['', 'None']:
              message_content += f'\n• 備註：\n{rmk}'

        if len(parent_student_details) > 1:
          message_content += (
              f'\n--------------------\n'
              f'💰 本期應繳總計金額：{parent_total_amount:g} 元'
          )

        message_content += (f'\n--------------------\n'
                            f'可使用LINE PAY轉帳或是匯款至\n'
                            f'玉山銀行代碼(808)帳號0299--979--299866\n'
                            f'如果是匯款的話\n'
                            f'匯款完後在請您通知我一下'
                            f'謝謝您唷，感恩感恩'
                            )

        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=user_id, messages=[TextMessage(text=message_content)]
            )
        )

    unsent_count = total_count - len(matched_excel_students)

    return (
        f'【帳單發送統計結果（{target_sheet}）】\n'
        f'• 成功發送學生筆數：{len(matched_excel_students)} 筆\n'
        f'• 未發送筆數：{unsent_count} 筆（尚未綁定家長）\n'
        f'• 總計學生筆數：{total_count} 筆\n'
        f'• 本期已發送總金額：{grand_total_amount:g} 元'
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