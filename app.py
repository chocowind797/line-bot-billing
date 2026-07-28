import datetime
import json
import logging  # <--- 已加回 logging 模組
import os
from urllib.parse import quote, unquote
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    FlexContainer,
    FlexMessage,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent
import pandas as pd

# 載入本機的 .env 檔案
load_dotenv()

app = Flask(__name__)

# 【已加回】關閉 Werkzeug 每筆連線的 200 OK 刷屏日誌，讓終端機保持乾淨
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

# 資料儲存檔案路徑（此檔案已被 .gitignore 隔離，不會上傳到 GitHub）
DATA_FILE_PATH = 'bindings.json'

# 設定資料夾路徑與檔名規則
DATA_FOLDER = 'data'

def get_current_month_excel_path():
  # 確保 data 資料夾存在
  if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

  # 取得當前年份與月份（例如：2026年7月，會對應檔名包含 2026-07 或 20267 的檔案）
  now = datetime.datetime.now()
  year_str = str(now.year)
  month_str = f'{now.month:02d}'  # 確保是兩位數，例如 07

  # 搜尋 data 資料夾下符合當前年月關鍵字的檔案
  pattern = os.path.join(DATA_FOLDER, f'*{year_str}*{month_str}*.xlsx')
  matched_files = glob.glob(pattern)

  if matched_files:
    # 如果找到符合的檔案，回傳最新的那一個
    return max(matched_files, key=os.path.getmtime)

  # 如果沒有特定月份的檔案，退而求其次找 data 資料夾下的任何 .xlsx 檔案
  all_excel_files = glob.glob(os.path.join(DATA_FOLDER, '*.xlsx'))
  if all_excel_files:
    return max(all_excel_files, key=os.path.getmtime)

  # 如果真的找不到，回傳一個預設路徑
  return os.path.join(DATA_FOLDER, f'薪資計算器{year_str}(NEW).xlsx')

# 讀取資料的輔助函式（自動相容舊格式字串與新格式 List）
def load_data():
  if os.path.exists(DATA_FILE_PATH):
    try:
      with open(DATA_FILE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        pending = data.get('pending', {})
        verified = data.get('verified', {})

        for uid in list(pending.keys()):
          if isinstance(pending[uid], str):
            pending[uid] = [pending[uid]]

        for uid in list(verified.keys()):
          if isinstance(verified[uid], str):
            verified[uid] = [verified[uid]]

        return pending, verified
    except Exception:
      pass
  return {}, {}


# 儲存資料的輔助函式
def save_data(pending, verified):
  data = {'pending': pending, 'verified': verified}
  with open(DATA_FILE_PATH, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=4)


def encode_postback_data(**kwargs):
  return '&'.join(
      f'{key}={quote(str(value), safe="")}' for key, value in kwargs.items()
  )


def parse_postback_data(data):
  params = {}
  for item in data.split('&'):
    if '=' in item:
      key, value = item.split('=', 1)
      params[key] = unquote(value)
  return params


# 動態為使用者切換/綁定對應的 Rich Menu
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


def build_review_flex_message(parent_name, student_name, user_id):
  rename_prompt = f'改名 {student_name} '
  bubble_content = {
      'type': 'bubble',
      'body': {
          'type': 'box',
          'layout': 'vertical',
          'contents': [
              {
                  'type': 'text',
                  'text': '【新綁定審核通知】',
                  'weight': 'bold',
                  'size': 'lg',
                  'color': '#1DB446',
              },
              {
                  'type': 'text',
                  'text': f'家長名稱：{parent_name}',
                  'size': 'md',
                  'weight': 'bold',
                  'margin': 'md',
                  'wrap': True,
              },
              {
                  'type': 'text',
                  'text': f'申請綁定學生：{student_name}',
                  'size': 'md',
                  'margin': 'sm',
                  'wrap': True,
              },
          ],
      },
      'footer': {
          'type': 'box',
          'layout': 'vertical',
          'spacing': 'sm',
          'contents': [
              {
                  'type': 'box',
                  'layout': 'horizontal',
                  'spacing': 'sm',
                  'contents': [
                      {
                          'type': 'button',
                          'style': 'primary',
                          'color': '#28a745',
                          'action': {
                              'type': 'postback',
                              'label': '同意',
                              'data': encode_postback_data(
                                  action='approve',
                                  student=student_name,
                                  uid=user_id,
                              ),
                          },
                      },
                      {
                          'type': 'button',
                          'style': 'primary',
                          'color': '#dc3545',
                          'action': {
                              'type': 'postback',
                              'label': '拒絕',
                              'data': encode_postback_data(
                                  action='reject',
                                  student=student_name,
                                  uid=user_id,
                              ),
                          },
                      },
                  ],
              },
              {
                  'type': 'button',
                  'style': 'primary',
                  'color': '#ffc107',
                  'action': {
                      'type': 'postback',
                      'label': '改名',
                      'data': encode_postback_data(
                          action='rename',
                          student=student_name,
                          uid=user_id,
                      ),
                      'displayText': rename_prompt,
                      'inputOption': 'openKeyboard',
                      'fillInText': rename_prompt,
                  },
              },
          ],
      },
  }

  return FlexMessage(
      alt_text=f'【新綁定通知】{parent_name} 申請綁定學生：{student_name}',
      contents=FlexContainer.from_dict(bubble_content),
  )


@app.route("/", methods=["GET"])
def health_check():
  return "Line Bot is alive!", 200

@app.route('/callback', methods=['POST'])
def callback():
  signature = request.headers['X-Line-Signature']
  body = request.get_data(as_text=True)
  app.logger.info(f"收到 callback 請求，body: {body}")
  try:
    handler.handle(body, signature)
  except InvalidSignatureError:
    abort(400)
  return 'OK'


# 處理文字訊息事件
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
  user_id = event.source.user_id
  text = event.message.text.strip()

  pending_bindings, verified_bindings = load_data()

  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)

    # 每次用戶傳送訊息時，自動確保其套用正確的身分圖文選單[cite: 1]
    set_user_rich_menu(line_bot_api, user_id)

    # 取得家長的 LINE 顯示名稱（如果取得失敗則預設為「家長」）
    try:
      profile = line_bot_api.get_profile(user_id)
      parent_name = profile.display_name
    except Exception:
      parent_name = '家長'

    # 1. 老師專屬指令處理
    if user_id in TEACHER_USER_IDS:
      if text.startswith('審核 '):
        student_name = text.replace('審核 ', '').strip()
        target_user_id = None

        for uid, s_list in pending_bindings.items():
          if student_name in s_list:
            target_user_id = uid
            break

        if target_user_id:
          pending_bindings[target_user_id].remove(student_name)
          if not pending_bindings[target_user_id]:
            del pending_bindings[target_user_id]

          if target_user_id not in verified_bindings:
            verified_bindings[target_user_id] = []
          if student_name not in verified_bindings[target_user_id]:
            verified_bindings[target_user_id].append(student_name)

          save_data(pending_bindings, verified_bindings)

          line_bot_api.reply_message(
              ReplyMessageRequest(
                  reply_token=event.reply_token,
                  messages=[
                      TextMessage(
                          text=(
                              f'已成功將學生 【{student_name}】'
                              ' 與該家長完成綁定審核！'
                          )
                      )
                  ],
              )
          )
          line_bot_api.push_message(
              push_message_request=PushMessageRequest(
                  to=target_user_id,
                  messages=[
                      TextMessage(
                          text=(
                              f'您的帳號已通過老師審核！\n目前已成功綁定學生：{student_name}'
                              '，之後將可接收繳費通知。'
                          )
                      )
                  ],
              )
          )
        else:
          line_bot_api.reply_message(
              ReplyMessageRequest(
                  reply_token=event.reply_token,
                  messages=[
                      TextMessage(
                          text=f'找不到學生 【{student_name}】 的待審核申請紀錄。'
                      )
                  ],
              )
          )
        return

      elif text == '查看待審核':
        if not pending_bindings:
          msg = '目前沒有等待審核的綁定請求。'
        else:
          msg = '【待審核清單】\n'
          for uid, s_list in pending_bindings.items():
            try:
              p_profile = line_bot_api.get_profile(uid)
              p_display = p_profile.display_name
            except Exception:
              p_display = '未知家長'

            for sname in s_list:
              msg += f'- 學生: {sname} (家長: {p_display})\n'
          msg += '\n請回覆「審核 [學生姓名]」來通過綁定。'
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=msg)],
            )
        )
        return

      elif text == '發送帳單':
        result_msg = send_bills_logic(line_bot_api, verified_bindings)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=result_msg)],
            )
        )
        return

      elif text.startswith('改名 '):
        rest = text.replace('改名 ', '', 1).strip()
        renamed = False
        old_name = ''
        new_name = ''
        target_user_id = None

        for uid, s_list in pending_bindings.items():
          for sname in list(s_list):
            if rest.startswith(sname):
              new_name = rest[len(sname) :].strip()
              if not new_name:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text=(
                                    f'請使用格式「改名 {sname} [新姓名]」'
                                    '，並補上正確的學生姓名。'
                                )
                            )
                        ],
                    )
                )
                return
              if new_name in s_list:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text=(
                                    f'學生姓名 【{new_name}】'
                                    ' 已存在於此家長的待審核清單中。'
                                )
                            )
                        ],
                    )
                )
                return

              s_list[s_list.index(sname)] = new_name
              old_name = sname
              target_user_id = uid
              renamed = True
              break
          if renamed:
            break

        if renamed:
          save_data(pending_bindings, verified_bindings)
          try:
            p_profile = line_bot_api.get_profile(target_user_id)
            p_display = p_profile.display_name
          except Exception:
            p_display = '家長'

          line_bot_api.reply_message(
              ReplyMessageRequest(
                  reply_token=event.reply_token,
                  messages=[
                      TextMessage(
                          text=(
                              f'已將學生姓名由 【{old_name}】'
                              f' 修改為 【{new_name}】。'
                          )
                      ),
                      build_review_flex_message(
                          p_display, new_name, target_user_id
                      ),
                  ],
              )
          )
          line_bot_api.push_message(
              push_message_request=PushMessageRequest(
                  to=target_user_id,
                  messages=[
                      TextMessage(
                          text=(
                              f'老師已將您的綁定申請學生姓名'
                              f'由 【{old_name}】 修正為 【{new_name}】。'
                              '請等候老師審核確認。'
                          )
                      )
                  ],
              )
          )
        else:
          line_bot_api.reply_message(
              ReplyMessageRequest(
                  reply_token=event.reply_token,
                  messages=[
                      TextMessage(
                          text='找不到符合的待審核學生紀錄，請確認姓名是否正確。'
                      )
                  ],
              )
          )
        return

    # 2. 一般家長用戶訊息處理
    if text.startswith('我是'):
      student_name = text.replace('我是', '').strip()

      if user_id not in pending_bindings:
        pending_bindings[user_id] = []

      if student_name not in pending_bindings[user_id]:
        pending_bindings[user_id].append(student_name)

      save_data(pending_bindings, verified_bindings)

      line_bot_api.reply_message(
          ReplyMessageRequest(
              reply_token=event.reply_token,
              messages=[
                  TextMessage(
                      text=(
                          f'已收到您的綁定申請，學生姓名：【{student_name}】。\n請等候老師後台審核確認！'
                      )
                  )
              ],
          )
      )

      for teacher in TEACHER_USER_IDS:
        flex_message = build_review_flex_message(
            parent_name, student_name, user_id
        )

        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=teacher, messages=[flex_message]
            )
        )
      return

    elif text.startswith('解綁 '):
      student_name = text.replace('解綁 ', '').strip()
      unbound_success = False

      if user_id in verified_bindings:
        for s in list(verified_bindings[user_id]):
          if student_name in s:
            verified_bindings[user_id].remove(s)
            unbound_success = True
            student_name = s
            break

        if not verified_bindings[user_id]:
          del verified_bindings[user_id]

        save_data(pending_bindings, verified_bindings)

      if unbound_success:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=(
                            f'已成功為您解除綁定學生：【{student_name}】。\n未來將不再接收該學生的繳費通知。'
                        )
                    )
                ],
            )
        )

        for teacher in TEACHER_USER_IDS:
            line_bot_api.push_message(
                push_message_request=PushMessageRequest(
                    to=teacher,
                    messages=[
                        TextMessage(
                            text=(
                                f'【解除綁定通知】\n家長 【{parent_name}】'
                                f' 已自行解除綁定學生：【{student_name}】'
                            )
                        )
                    ],
                )
            )
      else:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=(
                            f'找不到您已綁定學生【{student_name}】的紀錄，請確認輸入是否正確。'
                        )
                    )
                ],
            )
        )
      return

    elif text == '我的綁定':
      my_students = verified_bindings.get(user_id, [])
      if not my_students:
        msg = (
            '您目前尚未成功綁定任何學生。\n若要綁定，請傳送「我是 [學生姓名]」。'
        )
      else:
        msg = '【您目前已綁定的學生】\n'
        for s in my_students:
          msg += f'- {s}\n'
        msg += '\n若要解除綁定，請回傳「解綁 [學生姓名]」。'

      line_bot_api.reply_message(
          ReplyMessageRequest(
              reply_token=event.reply_token,
              messages=[TextMessage(text=msg)],
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
                        '歡迎使用補習班繳費通知系統。\n請傳送「我是 [學生姓名]」來申請綁定帳號。'
                    )
                )
            ],
        )
    )


# 處理按鈕點擊後的 Postback 事件
@handler.add(PostbackEvent)
def handle_postback(event):
  user_id = event.source.user_id

  if user_id in TEACHER_USER_IDS:
    return

  data = event.postback.data
  params = parse_postback_data(data)
  action = params.get('action')
  student_name = params.get('student')
  target_user_id = params.get('uid')

  pending_bindings, verified_bindings = load_data()

  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)

    if action == 'approve':
      if (
          target_user_id in pending_bindings
          and student_name in pending_bindings[target_user_id]
      ):
        pending_bindings[target_user_id].remove(student_name)
        if not pending_bindings[target_user_id]:
          del pending_bindings[target_user_id]

        if target_user_id not in verified_bindings:
          verified_bindings[target_user_id] = []
        if student_name not in verified_bindings[target_user_id]:
          verified_bindings[target_user_id].append(student_name)

        save_data(pending_bindings, verified_bindings)

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=f'已成功【同意】學生 【{student_name}】 的綁定申請！'
                    )
                ],
            )
        )
        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=target_user_id,
                messages=[
                    TextMessage(
                        text=(
                            f'您的帳號已通過老師審核！\n目前已成功綁定學生：{student_name}'
                            '，之後將可接收繳費通知。'
                        )
                    )
                ],
            )
        )
      else:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=(
                            f'找不到學生 【{student_name}】'
                            ' 的待審核紀錄（可能已經審核過了）。'
                        )
                    )
                ],
            )
        )

    elif action == 'reject':
      if (
          target_user_id in pending_bindings
          and student_name in pending_bindings[target_user_id]
      ):
        pending_bindings[target_user_id].remove(student_name)
        if not pending_bindings[target_user_id]:
          del pending_bindings[target_user_id]

        save_data(pending_bindings, verified_bindings)

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=f'已【拒絕】學生 【{student_name}】 的綁定申請。'
                    )
                ],
            )
        )
        line_bot_api.push_message(
            push_message_request=PushMessageRequest(
                to=target_user_id,
                messages=[
                    TextMessage(
                        text=(
                            f'很抱歉，您申請綁定的學生 【{student_name}】'
                            ' 未通過老師審核。如有疑問請與老師聯繫。'
                        )
                    )
                ],
            )
        )
      else:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=f'找不到學生 【{student_name}】 的待審核紀錄。'
                    )
                ],
            )
        )

    elif action == 'rename':
      if (
          target_user_id in pending_bindings
          and student_name in pending_bindings[target_user_id]
      ):
        rename_prompt = f'改名 {student_name} '
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=(
                            f'請將學生 【{student_name}】 修改為正確姓名。\n'
                            f'格式：{rename_prompt}[新姓名]'
                        )
                    )
                ],
            )
        )
      else:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text=(
                            f'找不到學生 【{student_name}】'
                            ' 的待審核紀錄（可能已審核或已修改）。'
                        )
                    )
                ],
            )
        )


# 核心發送帳單邏輯（支援多學生合併發送、單一小計計算以及總金額統計）
def send_bills_logic(line_bot_api, verified_bindings):
  # 動態取得 data 資料夾下當月的 Excel 檔案路徑
  excel_file_path = get_current_month_excel_path()

  if not os.path.exists(excel_file_path):
    return (
        f'找不到對應月份的 Excel 檔案 ({excel_file_path})，'
        '請確認是否已放置於 data 資料夾中。'
    )

  try:
    xls = pd.ExcelFile(EXCEL_FILE_PATH)
    current_month_str = f'{datetime.datetime.now().month}月'

    if current_month_str in xls.sheet_names:
      target_sheet = current_month_str
    else:
      target_sheet = xls.sheet_names[0]

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)

    # 1. 自動從 Excel 中找出所有學生姓名列（包含 "--" 的列）
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
    grand_total_amount = 0  # 統計全班/全部發送的總金額

    if total_count == 0:
      return f'在【{target_sheet}】中找不到任何學生資料。'

    # 建立一個「學生全名 -> 該學生的詳細資料」的對照字典
    student_data_map = {}
    for student_info in excel_students:
      student_full_name = student_info['name']
      r_idx = student_info['row_idx']
      c_idx = student_info['col_idx']

      hours = '略'
      salary = '略'
      subtotal = 0

      for check_r in range(max(0, r_idx - 3), r_idx):
        for check_c in range(c_idx, df.shape[1]):
          header_val = str(df.iloc[check_r, check_c]).strip()
          if '時數小計' in header_val:
            hours = df.iloc[r_idx, check_c]
          elif '薪資' in header_val:
            salary = df.iloc[r_idx, check_c]
          elif '單一學生小計' in header_val:
            raw_sub = df.iloc[r_idx, check_c]
            # 嘗試將小計轉換為數字以便加總，若轉換失敗則為 0
            try:
              subtotal = float(raw_sub) if pd.notna(raw_sub) else 0
            except Exception:
              subtotal = 0

      student_data_map[student_full_name] = {
          'hours': hours if pd.notna(hours) else '略',
          'salary': salary if pd.notna(salary) else '略',
          'subtotal': subtotal,
      }

    matched_excel_students = set()

    # 2. 針對每一位已驗證的家長，檢查他們綁定的學生清單
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
            })
            parent_total_amount += data['subtotal']
            matched_excel_students.add(student_full_name)
            grand_total_amount += data['subtotal']

      # 如果這位家長名下有對應到學生，將所有學生的明細合併為一則訊息發出
      if parent_student_details:
        message_content = (
            f'【{target_sheet} 補習班繳費通知】\n親愛的家長您好，以下是您的本期繳費明細：'
        )

        for s_info in parent_student_details:
          message_content += (
              f'\n--------------------\n'
              f'• 學生姓名：{s_info["name"]}\n'
              f'• 上課時數：{s_info["hours"]}\n'
              f'• 薪資/單價：{s_info["salary"]}\n'
              f'• 單一學生小計：{s_info["subtotal"]:g} 元'
          )

        # 如果家長有多個學生，顯示各別小計的總計金額
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


# 背景排程
scheduler = BackgroundScheduler()


@scheduler.scheduled_job('cron', day=1, hour=9, minute=0)
def scheduled_send_bills():
  _, verified_bindings = load_data()
  with ApiClient(configuration) as api_client:
    line_bot_api = MessagingApi(api_client)
    send_bills_logic(line_bot_api, verified_bindings)


scheduler.start()

if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000, debug=True)