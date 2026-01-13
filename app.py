# app.py (Streamlit Cloud 兼容版)

import streamlit as st
import pandas as pd
import time
import os
import re
import ast 
import subprocess
import difflib
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== [雲端環境專用：自動初始化 AnyStyle] ==========
def initialize_cloud_environment():
    """在 Streamlit Cloud 上自動安裝與設定 AnyStyle 執行環境"""
    try:
        # 檢查 anystyle 是否已存在
        subprocess.run(["anystyle", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        with st.spinner("☁️ 正在初始化雲端環境 (安裝 AnyStyle)... 這可能需要 1-2 分鐘"):
            # 1. 安裝 anystyle-cli 到用戶目錄
            os.system("gem install anystyle-cli --user-install")
            
            # 2. 獲取 Ruby Gem 的 bin 路徑並加入環境變數
            gem_path = subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
            if gem_path not in os.environ["PATH"]:
                os.environ["PATH"] = gem_path + os.pathsep + os.environ["PATH"]

# 執行環境檢查
initialize_cloud_environment()

# ========== 導入模組 (請確保 modules 資料夾與此檔同級) ==========
from modules.parsers import parse_references_with_anystyle
from modules.local_db import load_csv_data, search_local_database
from modules.api_clients import (
    get_scopus_key,
    get_serpapi_key,
    search_crossref_by_doi,
    search_crossref_by_text,
    search_scopus_by_title,
    search_scholar_by_title,
    search_scholar_by_ref_text,
    search_s2_by_title,
    search_openalex_by_title,
    check_url_availability
)

# ========== 頁面設定與樣式 ==========
st.set_page_config(page_title="引文查核報表工具", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: bold; text-align: center; color: #4F46E5; margin-bottom: 5px; }
    .sub-header { text-align: center; color: #6B7280; margin-bottom: 2rem; }
    .status-badge { padding: 4px 10px; border-radius: 12px; font-size: 0.85em; font-weight: bold; }
    .ref-box { background-color: #F9FAFB; padding: 12px; border-radius: 8px; font-family: 'Courier New', monospace; font-size: 0.9em; border: 1px solid #E5E7EB; margin-top: 5px; }
    .report-card { background-color: #FFFFFF; padding: 20px; border-radius: 10px; border: 1px solid #E5E7EB; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
</style>
""", unsafe_allow_html=True)

if "results" not in st.session_state: st.session_state.results = []

# ========== [核心工具函數] (維持同學邏輯) ==========
def format_name_field(data):
    if not data: return None
    try:
        if isinstance(data, str):
            if not (data.startswith('[') or data.startswith('{')): return data
            try: data = ast.literal_eval(data)
            except: return data
        names_list = []
        data_list = data if isinstance(data, list) else [data]
        for item in data_list:
            if isinstance(item, dict):
                parts = [item.get('family', ''), item.get('given', '')]
                names_list.append(", ".join([p for p in parts if p]))
            else: names_list.append(str(item))
        return "; ".join(names_list)
    except: return str(data)

def refine_parsed_data(parsed_item):
    item = parsed_item.copy()
    raw_text = item.get('text', '').strip()
    for key in ['doi', 'url', 'title', 'date']:
        if item.get(key) and isinstance(item[key], str):
            item[key] = item[key].strip(' ,.;)]}>')
    title = item.get('title', '')

    if title and (title.startswith('&') or title.lower().startswith('and ')):
        fix_match = re.search(r'^&(?:amp;)?\s*[^0-9]+?\(?\d{4}\)?[\.\s]+(.*)', title)
        if fix_match:
            cleaned_title = fix_match.group(1).strip()
            if len(cleaned_title) > 5:
                title = cleaned_title
                item['title'] = title

    if title:
        title = re.sub(r'^\s*\d{4}[\.\s]+', '', title)
        title = re.sub(r'(?i)\.?\s*arXiv.*$', '', title)
        title = re.sub(r'(?i)\.?\s*Available.*$', '', title)
        item['title'] = title

    if not title or len(title) < 5:
        abbr_match = re.search(r'^([A-Z0-9\-\.\s]{2,12}:\s*.+?)(?=\s*[,\[]|\s*Available|\s*\(|\bhttps?://|\.|$)', raw_text)
        if abbr_match:
            item['title'] = abbr_match.group(1).strip()
        else:
            for backup_key in ['publisher', 'container-title', 'journal']:
                val = item.get(backup_key)
                if val and len(str(val)) > 15:
                    item['title'] = str(val).strip()
                    break

    if item.get('authors'): item['authors'] = format_name_field(item['authors'])
    return item

def check_single_task(idx, raw_ref, local_df, target_col, scopus_key, serpapi_key):
    ref = refine_parsed_data(raw_ref)
    title, text = ref.get('title', ''), ref.get('text', '')
    doi, parsed_url = ref.get('doi'), ref.get('url')
    first_author = ref['authors'].split(';')[0].split(',')[0].strip() if ref.get('authors') else ""
    year = str(ref.get('date', ''))[:4]
    
    res = {"id": idx, "title": title, "text": text, "parsed": ref, "sources": {}, "found_at_step": None, "suggestion": None}

    # 1. Local DB (維持原樣)
    if bool(re.search(r'[\u4e00-\u9fff]', title)) and local_df is not None and title:
        match_row, _ = search_local_database(local_df, target_col, title, threshold=0.85)
        if match_row is not None:
            res.update({"sources": {"Local DB": "匹配成功"}, "found_at_step": "0. Local Database"})
            return res

    # 2. Crossref 搜尋 (加入標題驗證)
    # 使用標題作為查詢，避免全文過長導致亂抓
    url_cr, cr_title = search_crossref_by_text(title, first_author)
    if url_cr and is_title_match(cr_title, title):
        res.update({"sources": {"Crossref": url_cr}, "found_at_step": "1. Crossref"})
        return res

    # 3. Google Scholar 搜尋 (使用 api_clients 內建的階層搜尋與比對)
    if serpapi_key:
        try:
            # 直接傳入 title 和 raw_text，讓 api_clients 內部去跑「三關搜尋」
            url_gs, gs_title = search_scholar_by_title(
                title=title, 
                api_key=serpapi_key, 
                author=first_author, 
                raw_text=text
            )
            
            if url_gs:
                # 只要 API 回傳了 URL，代表它在內部已經通過了新版的 _is_match 檢查
                res.update({
                    "sources": {"Google Scholar": url_gs}, 
                    "found_at_step": "5. Google Scholar"
                })
                # 如果有抓到更完整的標題，就更新它
                if gs_title: res["title"] = gs_title 
                return res
            
            # 如果連 search_scholar_by_title 都回傳 None，
            # 我們才嘗試最後的「全文模糊建議」
            else:
                url_fallback, _ = search_scholar_by_ref_text(text, serpapi_key, target_title=title)
                if url_fallback:
                    res["suggestion"] = url_fallback
                    
        except Exception as e:
            # 這裡可以暫時加上 st.write(f"Debug: {e}") 來看看有沒有報錯
            pass

    # 4. 檢查原文是否自帶網址 (ID 8, 9 的情況)
    if not res["found_at_step"]:
        found_urls = re.findall(r'https?://[^\s)\]]+', text)
        for u in found_urls:
            if "google" not in u and check_url_availability(u): # 排除搜尋引擊連結
                res.update({"sources": {"Direct Link": u}, "found_at_step": "6. Website Check"})
                return res

    return res

# ========== 側邊欄與介面 (維持同學 UI) ==========
with st.sidebar:
    st.header("⚙️ 系統設定")
    DEFAULT_CSV_PATH = "112ndltd.csv" # 確保 GitHub 倉庫有此檔案
    local_df, target_col = None, None
    if os.path.exists(DEFAULT_CSV_PATH):
        local_df = load_csv_data(DEFAULT_CSV_PATH)
        if local_df is not None:
            st.success(f"✅ 已載入本地庫: {len(local_df)} 筆")
            target_col = "論文名稱" if "論文名稱" in local_df.columns else local_df.columns[0]
    
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    st.divider()
    st.caption("API 狀態確認:")
    st.write(f"Scopus: {'✅' if scopus_key else '❌'} | SerpAPI: {'✅' if serpapi_key else '❌'}")

st.markdown('<div class="main-header">📚 學術引用自動化查核報表</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">整合多方資料庫 API，一鍵產出引文驗證結果與下載 CSV</div>', unsafe_allow_html=True)

st.markdown("### 📥 第一步：輸入引文內容")
raw_input = st.text_area("請直接貼上參考文獻列表：", height=250, placeholder="例如：\nStyleTTS 2...\nAIOS...")

if st.button("🚀 開始全自動核對並生成報表", type="primary", use_container_width=True):
    if not raw_input:
        st.warning("⚠️ 請先貼上內容。")
    else:
        st.session_state.results = []
        with st.status("🔍 正在查核作業中...", expanded=True) as status:
            _, struct_list = parse_references_with_anystyle(raw_input)
            if struct_list:
                progress_bar = st.progress(0)
                results_buffer = []
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(check_single_task, i+1, r, local_df, target_col, scopus_key, serpapi_key): i for i, r in enumerate(struct_list)}
                    for i, future in enumerate(as_completed(futures)):
                        results_buffer.append(future.result())
                        progress_bar.progress((i + 1) / len(struct_list))
                st.session_state.results = sorted(results_buffer, key=lambda x: x['id'])
                status.update(label="✅ 核對作業完成！", state="complete", expanded=False)

# 顯示與篩選邏輯 (維持同學代碼)
if st.session_state.results:
    st.divider()
    st.markdown("### 📊 第二步：查核結果與報表下載")
    total_refs = len(st.session_state.results)
    verified_db = sum(1 for r in st.session_state.results if r.get('found_at_step') and "6." not in str(r.get('found_at_step')))
    
    col1, col2, col3 = st.columns(3)
    col1.metric("總查核筆數", total_refs)
    col2.metric("資料庫匹配成功", verified_db)
    col3.metric("需人工確認/修正", total_refs - verified_db)

    df_export = pd.DataFrame([{
        "ID": r['id'],
        "狀態": r['found_at_step'] if r['found_at_step'] else "未找到",
        "抓取標題": r['title'],
        "原始文獻內容": r['text'],
        "驗證來源連結": next(iter(r['sources'].values()), "N/A") if r['sources'] else "N/A"
    } for r in st.session_state.results])

    st.download_button(
        label="📥 下載完整查核報告 (CSV)",
        data=df_export.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"Check_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv",
        use_container_width=True
    )

    filter_option = st.radio("顯示篩選項目：", ["全部顯示", "✅ 資料庫驗證", "🌐 網站有效來源", "⚠️ 網站 (連線失敗)", "❌ 未找到結果"], horizontal=True)

    for item in st.session_state.results:
        raw_step = item.get('found_at_step')
        step = str(raw_step) if raw_step is not None else ""
        
        # 簡易篩選顯示邏輯
        show = (filter_option == "全部顯示") or \
               (filter_option == "✅ 資料庫驗證" and step and "6." not in step) or \
               (filter_option == "🌐 網站有效來源" and "6." in step and "Failed" not in step) or \
               (filter_option == "⚠️ 網站 (連線失敗)" and "Failed" in step) or \
               (filter_option == "❌ 未找到結果" and not step)

        if show:
            with st.expander(f"ID {item['id']}：{item['text'][:80]}..."):
                st.write(f"**查核結果：** `{step if step else '資料庫未匹配'}`")
                st.markdown(f"<div class='ref-box'>{item['text']}</div>", unsafe_allow_html=True)
                if item.get('sources'):
                    for src, link in item['sources'].items(): st.write(f"- {src}: {link}")
                if not step and item.get("suggestion"):
                    st.warning(f"💡 建議：[點此手動搜尋]({item['suggestion']})")
