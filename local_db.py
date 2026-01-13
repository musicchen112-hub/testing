# modules/local_db.py

import pandas as pd
import streamlit as st
from difflib import SequenceMatcher
from .parsers import clean_title

def load_csv_data(uploaded_file):
    """
    讀取上傳的 CSV 檔案，嘗試不同編碼以防亂碼。
    """
    if uploaded_file is None:
        return None
    
    try:
        # 嘗試預設 utf-8
        df = pd.read_csv(uploaded_file)
        return df
    except UnicodeDecodeError:
        try:
            # 台灣常見的 big5 編碼 (針對政府資料)
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, encoding='big5')
            return df
        except Exception as e:
            st.error(f"讀取 CSV 失敗: {e}")
            return None

def search_local_database(df, title_column, query_title, threshold=0.8):
    """
    在 DataFrame 的指定欄位中搜尋相似標題。
    """
    if df is None or not title_column or not query_title:
        return None, None

    # 清洗查詢標題
    clean_query = clean_title(query_title)
    
    # 遍歷 CSV 中的每一列 (如果資料量>10萬筆，這裡可能需要優化，但一般論文清單夠快)
    best_score = 0
    best_match_row = None

    # 為了效能，我們先做簡單篩選 (長度差異太大就不比對)
    # 這裡示範逐行比對 (最準確)
    for index, row in df.iterrows():
        db_title = str(row[title_column])
        clean_db_title = clean_title(db_title)
        
        # 快速過濾：如果標題完全包含
        if clean_query in clean_db_title or clean_db_title in clean_query:
            score = 1.0
        else:
            # 模糊比對
            score = SequenceMatcher(None, clean_query, clean_db_title).ratio()
        
        if score > best_score:
            best_score = score
            best_match_row = row

    # 判斷是否超過門檻
    if best_score >= threshold:
        # 回傳找到的那一行資料 (Series)
        return best_match_row, best_score
    
    return None, 0