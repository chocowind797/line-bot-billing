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
TEACHER_RICH_MENU_ID = os.getenv('TEACHER_RICH_MENU_ID', '')
PARENT_RICH_MENU_ID = os.getenv('PARENT_RICH_MENU_ID', '')

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


def set_user_rich_menu(line_bot_api, user_id):
  try:
    if user_id in TEACHER_USER_IDS:
      if TEACHER_RICH_MENU_ID:
        line_bot_api.link_rich_menu_id_to_user(
            user_id=user_id, rich_menu_id=TEACHER_RICH_MENU_ID
        )
    else:
      if PARENT_RICH_MENU_ID:
        line_bot_api.link_rich_menu_id_to_user(
            user_id=user_id, rich_menu_id=PARENT_RICH_MENU_ID
        )
  except Exception as e:
    print(f'設定 Rich Menu 發生錯誤: {e}')


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

    # 確保用戶套用正確的圖文選單
    set_user_rich_menu(line_bot_api, user_id)

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
      return

    # 預設回覆
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[
                TextMessage(
                    text=(
                        '歡迎使用補習班繳費通知系統。\n'
                        '請傳送「我是 [編號]--[姓名]」來直接綁定帳號。'
                    )
                )
            ],
        )
    )


def send_bills_logic(line_bot_api, verified_bindings):
  excel_file_path = get_current_month_excel_path()

  if not os.path.exists(excel_file_path):
    return (
        f'找不到對應月份的 Excel 檔案 ({excel_file_path})，'
        '請確認是否已放置於 data 資料夾中。'
    )

  try:
    xls = pd.ExcelFile(excel_file_path)
    current_month_str = f'{datetime.datetime.now().month}月'

    if current_month_str in xls.sheet_names:
      target_sheet = current_month_str
    else:
      target_sheet = xls.sheet_names[0]

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)

    excel_students = []
    for r_idx, row in df.iterrows():
      for c_idx, val in enumerate(row):
        if pd.notna(val) and '--' in str(val):
          student_name = str(val).strip()
          excel_students.append(
              {'name': student_name, 'row_idx': r_idx, 'col_idx': c_idx}
          )

    total_count = len(excel_students)
    sent_student_count = 0
    grand_total_amount = 0

    if total_count == 0:
      return f'在【{target_sheet}】中找不到任何學生資料。'

    student_data_map = {}
    for student_info in excel_students:
      student_full_name = student_info['name']
      r_idx = student_info['row_idx']
      c_idx = student_info['col_idx']

      hours = '略'
      salary = '略'
      subtotal = 0
      book_fee = None
      remark = None  # 新增：備註變數

      for check_r in range(max(0, r_idx - 3), r_idx):
        for check_c in range(c_idx, df.shape[1]):
          header_val = str(df.iloc[check_r, check_c]).strip()
          if '時數小計' in header_val:
            hours = df.iloc[r_idx, check_c]
          elif '薪資' in header_val:
            salary = df.iloc[r_idx, check_c]
          elif '書籍/教材' in header_val:
            book_fee = df.iloc[r_idx, check_c]
          elif '備註' in header_val:  # 新增：抓取備註欄位
            remark = df.iloc[r_idx, check_c]
          elif '單一學生小計' in header_val:
            raw_sub = df.iloc[r_idx, check_c]
            try:
              subtotal = float(raw_sub) if pd.notna(raw_sub) else 0
            except Exception:
              subtotal = 0

      student_data_map[student_full_name] = {
          'hours': hours if pd.notna(hours) else '略',
          'salary': salary if pd.notna(salary) else '略',
          'subtotal': subtotal,
          'book_fee': book_fee,
          'remark': remark,  # 新增：將數值存入字典
      }

    matched_excel_students = set()

    for user_id, bound_student_names in verified_bindings.items():
      parent_student_details = []
      parent_total_amount = 0

      for bound_name in bound_student_names:
        for student_full_name, data in student_data_map.items():
          if bound_name in student_full_name:
            parent_student_details.append({
                'name': student_full_name,
                'hours': data['hours'],
                'salary': data['salary'],
                'subtotal': data['subtotal'],
                'book_fee': data['book_fee'],
                'remark': data['remark'],  # 新增：傳遞給家長明細
            })
            parent_total_amount += data['subtotal']
            matched_excel_students.add(student_full_name)
            grand_total_amount += data['subtotal']

      if parent_student_details:
        message_content = (
            f'【{target_sheet} 補習班繳費通知】\n親愛的家長您好，以下是您的本期繳費明細：'
        )

        for s_info in parent_student_details:
          message_content += (
              f'\n--------------------\n'
              f'• 學生資訊：{s_info["name"]}\n'
              f'• 上課時數：{s_info["hours"]}\n'
              f'• 薪資/單價：{s_info["salary"]}'
          )

          b_fee = s_info.get("book_fee")
          if pd.notna(b_fee) and str(b_fee).strip() not in ['', '0', '0.0', 'None']:
              message_content += f'\n• 書籍/教材：{b_fee}'

          # 新增：判斷如果備註存在，且不是空字串或 NaN，才加入帳單顯示
          rmk = s_info.get("remark")
          if pd.notna(rmk) and str(rmk).strip() not in ['', 'None']:
              message_content += f'\n• 備註：{rmk}'

          message_content += f'\n• 單一學生小計：{s_info["subtotal"]:g} 元'

        if len(parent_student_details) > 1:
          message_content += (
              f'\n--------------------\n'
              f'💰 本期應繳總計金額：{parent_total_amount:g} 元'
          )

        message_content += f'\n--------------------\n請查收並於期限內完成繳費，謝謝！'

        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=user_id, messages=[TextMessage(text=message_content)]
            )
        )
        sent_student_count += len(parent_student_details)

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

scheduler = BackgroundScheduler()

@scheduler.scheduled_job('cron', day=1, hour=9, minute=0)
def scheduled_send_bills():
  verified_bindings = load_data()
  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
    send_bills_logic(line_bot_api, verified_bindings)

scheduler.start()

if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000, debug=True)