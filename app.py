import logging
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FileMessageContent, PostbackEvent
from config import LINE_CHANNEL_SECRET

# 匯入我們寫好的所有 Handlers 模組
from handlers.message_handler import handle_text_message
from handlers.postback_handler import handle_postback
from handlers.file_handler import handle_file_message

app = Flask(__name__)

# 設定 Flask 記錄檔層級，避免終端機被過多的 GET 請求洗版
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=["GET"])
def health_check():
    """用來檢查機器人是否正常存活的健康檢查點"""
    return "Line Bot is alive!", 200

@app.route('/callback', methods=['POST'])
def callback():
    """接收 LINE 傳過來的 Webhook 事件入口"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 🌟 1. 當收到「文字訊息」時，交給 message_handler 處理
@handler.add(MessageEvent, message=TextMessageContent)
def on_text_message(event):
    handle_text_message(event)

# 🌟 2. 當收到「按鈕點擊 (Postback)」時，交給 postback_handler 處理
@handler.add(PostbackEvent)
def on_postback(event):
    handle_postback(event)

# 🌟 3. 當收到「檔案上傳 (例如 Excel)」時，交給 file_handler 處理
@handler.add(MessageEvent, message=FileMessageContent)
def on_file_message(event):
    handle_file_message(event)

if __name__ == '__main__':
    # 啟動伺服器 (預設 Port 5000)
    app.run(host='0.0.0.0', port=5000, debug=True)