import datetime
import glob
import os
import time
import pandas as pd
from config import SUBJECT_INFO
from services import line_service

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

# 💡 注意這裡：拿掉了原本的 line_bot_api 參數
def send_bills_logic(verified_bindings, subject_code, target_student_id=None, lookback_months=6):
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
                    # 【單發帳單優化】：如果在讀取階段就發現學號不符，直接跳過，不浪費資源處理！
                    # ==========================================
                    if target_student_id and s_id != target_student_id:
                        continue

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
        # 讀取新的巢狀字典並比對並發送帳單
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

                line_service.push_text(user_id, message_content)
                time.sleep(0.1) 

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