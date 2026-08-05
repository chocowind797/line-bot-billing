from functools import wraps
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest, TextMessage, QuickReply
)
from config import LINE_CHANNEL_ACCESS_TOKEN

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

def auto_format_messages(func):
    """裝飾器：自動將傳入的訊息（字串、字典或清單）轉為 LINE SDK 的 TextMessage 物件"""
    @wraps(func)
    def wrapper(to_or_token, messages, *args, **kwargs):
        # 確保 messages 是一個 list
        if not isinstance(messages, list):
            messages = [messages]
            
        formatted = []
        for msg in messages:
            if isinstance(msg, str):
                formatted.append(TextMessage(text=msg))
            elif isinstance(msg, dict) and "text" in msg:
                formatted.append(TextMessage(text=msg["text"]))
            else:
                formatted.append(msg)
                
        # 呼叫原本的函式，並帶入格式化後的 messages
        return func(to_or_token, formatted, *args, **kwargs)
    return wrapper

@auto_format_messages
def reply_message(reply_token, messages):
    """傳送多則/自訂格式回覆訊息"""
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=messages))

@auto_format_messages
def push_message(to, messages):
    """傳送多則/自訂格式推播訊息"""
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.push_message(PushMessageRequest(to=to, messages=messages))

def reply_text(reply_token, text, quick_reply_items=None):
    """回覆單純文字 (可選配下方快捷按鈕 Quick Reply)"""
    msg = TextMessage(text=text)
    if quick_reply_items:
        msg.quick_reply = QuickReply(items=quick_reply_items)
    reply_message(reply_token, [msg])

def push_text(to_id, text):
    """主動推播單純文字給特定使用者"""
    msg = TextMessage(text=text)
    push_message(to_id, [msg])

def get_file_content(message_id):
    """下載使用者上傳的檔案 (例如 Excel 檔)"""
    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        return blob_api.get_message_content(message_id)

def get_user_name(user_id):
    """透過 LINE API 取得使用者的 LINE 暱稱"""
    try:
        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            profile = api.get_profile(user_id)
            return profile.display_name
    except Exception:
        return f"使用者 ({user_id[-4:]})"