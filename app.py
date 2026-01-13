# app.py 雲端穩定 + 一鍵報表版
import streamlit as st
import pandas as pd
import time
import os
import re
import ast 
import subprocess
import difflib
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 1. 雲端環境自動修復 ==========
def ensure_anystyle_installed():
    possible_paths = [
        "/home/appuser/.local/share/gem/ruby/3.1.0/bin",
        "/home/adminuser/.local/share/gem/ruby/3.1.0/bin",
        subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
    ]
    for p in possible_paths:
        if p not in os.environ["PATH"]:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

    try:
        subprocess.run(["anystyle", "--version"], capture_output=True, check=True)
    except:
        with st.spinner("☁️ 正在初始化雲端 AnyStyle 環境..."):
            os.system("gem install anystyle-cli --user-install")
            new_path = subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
            if new_path not in os.environ["PATH"]:
                os.environ["PATH"] = new_path + os.pathsep + os.environ["PATH"]

ensure_anystyle_installed()

# 導入所有可能的模組 (確保 API 接口一個都不少)
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

# ========== 頁面設定與樣式 (100% 維持原樣) ==========
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

# ========== [核心工具函數] ==========

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

    # 1. 基礎符號清洗
    for key in ['doi', 'url', 'title', 'date']:
        if item.get(key) and isinstance(item[key], str):
            item[key] = item[key].strip(' ,.;)]}>')

    title = item.get('title', '')

    # Patch 1: 修復第二作者殘留問題
    if title and (title.startswith('&') or title.lower().startswith('and ')):
        fix_match = re.search(r'^&(?:amp;)?\s*[^0-9]+?\(?\d{4}\)?[\.\s]+(.*)', title)
        if fix_match:
            cleaned_title = fix_match.group(1).strip()
            if len(cleaned_title) > 5:
                title = cleaned_title
                item['title'] = title

    # Patch 2: 強力去噪
    if title:
        title = re.sub(r'^\s*\d{4}[\.\s]+', '', title)
        title = re.sub(r'(?i)\.?\s*arXiv.*$', '', title)
        title = re.sub(r'(?i)\.?\s*Available.*$', '', title)
        item['title'] = title

    if item.get('authors'): item['authors'] = format_name_field(item['authors'])
    return item

def check_single_task(idx, raw_ref, local_df, target_col, scopus_key, serpapi_key):
    ref = refine_parsed_data(raw_ref)
    title, text = ref.get('title', ''), ref.get('text', '')
    doi, parsed_url = ref.get('doi'), ref.get('url')
    first_author = ref['authors'].split(';')[0].split(',')[0].strip() if ref.get('authors') else ""
    year = str(ref.get('date', ''))[:4]
    
    res = {"id": idx, "title": title, "text": text, "parsed": ref, "sources": {}, "found_at_step": None, "suggestion": None}

    # 1. Local DB 
    if bool(re.search(r'[\u4e00-\u9fff]', title)) and local_df is not None and title:
        match_row, _ = search_local_database(local_df, target_col, title, threshold=0.85)
        if match_row is not None:
            res.update({"sources": {"Local DB": "匹配成功"}, "found_at_step": "0. Local Database"})
            return res

    # 2. Crossref & Scopus (嚴格匹配)
    url_cr, _ = search_crossref_by_text(title, first_author)
    if url_cr and isinstance(url_cr, str) and url_cr.startswith("http"):
        res.update({"sources": {"Crossref": url_cr}, "found_at_step": "1. Crossref"})
        return res

    # 3. Semantic Scholar & OpenAlex (防止 (None, 'Error'))
    try:
        url_s2 = search_s2_by_title(title)
        if url_s2 and isinstance(url_s2, str) and url_s2.startswith("http"):
            res.update({"sources": {"Semantic Scholar": url_s2}, "found_at_step": "3. Semantic Scholar"})
            return res
    except: pass

    # 4. Google Scholar (針對 Ko, K. 且防止錯判)
    if serpapi_key:
        try:
            # A. 精確標題搜尋
            url_gs, found_title = search_scholar_by_title(title, serpapi_key, author=first_author)
            
            # 相似度檢查：避免將錯誤文獻判定為正確 (閾值設為 0.7)
            if url_gs and found_title:
                sim = difflib.SequenceMatcher(None, title.lower(), str(found_title).lower()).ratio()
                if sim > 0.7:
                    res.update({"sources": {"Google Scholar": url_gs}, "found_at_step": "5. Google Scholar"})
                    return res

            # B. 針對 ResearchGate 邊緣案例 (Ko, K.) 的最後嘗試
            # 組合搜尋：作者 + 標題前 5 個字 + 年份
            keywords = " ".join(title.split()[:5])
            fallback_q = f"{first_author} \"{keywords}\" {year}"
            url_fb, title_fb = search_scholar_by_title(fallback_q, serpapi_key)
            if url_fb and title_fb:
                sim_fb = difflib.SequenceMatcher(None, title.lower(), str(title_fb).lower()).ratio()
                if sim_fb > 0.6: # 針對邊緣文獻稍微放寬
                    res.update({"sources": {"Google Scholar": url_fb}, "found_at_step": "5. Google Scholar (ResearchGate)"})
                    return res
        except: pass

    # 5. 直連檢查
    if parsed_url and str(parsed_url).startswith('http'):
        if check_url_availability(parsed_url):
            res.update({"sources": {"Direct Link": parsed_url}, "found_at_step": "6. Website Check"})
    
    return res

# ========== 側邊欄與 UI (100% 維持原樣) ==========
with st.sidebar:
    st.header("⚙️ 系統設定")
    DEFAULT_CSV_PATH = "112ndltd.csv"
    local_df, target_col = None, None
    if os.path.exists(DEFAULT_CSV_PATH):
        local_df = load_csv_data(DEFAULT_CSV_PATH)
        if local_df is not None:
            st.success(f"✅ 已載入本地庫: {len(local_df)} 筆")
            target_col = "論文名稱" if "論文名稱" in local_df.columns else local_df.columns[0]
    
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    st.divider()
    st.write(f"Scopus: {'✅' if scopus_key else '❌'} | SerpAPI: {'✅' if serpapi_key else '❌'}")

st.markdown('<div class="main-header">📚 學術引用自動化查核報表</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">整合多方資料庫 API，一鍵產出引文驗證結果與下載 CSV</div>', unsafe_allow_html=True)

raw_input = st.text_area("請直接貼上參考文獻列表：", height=250)

if st.button("🚀 開始全自動核對並生成報表", type="primary", use_container_width=True):
    if not raw_input:
        st.warning("⚠️ 請先貼上內容。")
    else:
        st.session_state.results = []
        with st.status("🔍 正在查核中...", expanded=True) as status:
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
                status.update(label="✅ 核對完成！", state="complete", expanded=False)

# ========== 報表顯示與下載 (100% 維持原樣) ==========
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
        "狀態": str(r.get('found_at_step') or "未找到"),
        "抓取標題": r.get('title'),
        "原始文獻內容": r.get('text'),
        "驗證來源連結": next(iter(r.get('sources', {}).values()), "N/A") if r.get('sources') else "N/A"
    } for r in st.session_state.results])

    st.download_button(
        label="📥 下載完整查核報告 (CSV)",
        data=df_export.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"Report_{time.strftime('%Y%m%d')}.csv",
        mime="text/csv",
        use_container_width=True
    )

    filter_option = st.radio("篩選顯示：", ["全部顯示", "✅ 資料庫驗證", "❌ 未找到結果"], horizontal=True)

    for r in st.session_state.results:
        raw_step = r.get('found_at_step')
        step = str(raw_step) if raw_step is not None else ""
        show = (filter_option == "全部顯示") or \
               (filter_option == "✅ 資料庫驗證" and step and "6." not in step) or \
               (filter_option == "❌ 未找到結果" and not step)

        if show:
            icon = "❌" if not step else ("🌐" if "6." in step else "✅")
            with st.expander(f"{icon} ID {r['id']}：{r['text'][:80]}..."):
                st.write(f"**查核結果：** `{step if step else '資料庫未匹配'}`")
                st.markdown(f"<div class='ref-box'>{r['text']}</div>", unsafe_allow_html=True)
                if r.get('sources'):
                    for src, link in r['sources'].items(): st.write(f"- {src}: {link}")
                if not step and r.get("suggestion"):
                    st.info(f"💡 [手動搜尋建議]({r['suggestion']})")
else:
    st.info("💡 請貼上文獻並開始查核。")
