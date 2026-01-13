# modules/api_clients.py
import streamlit as st
import requests
import time
from difflib import SequenceMatcher
from serpapi import GoogleSearch
import urllib3
import re

# 導入標題清洗函式
from .parsers import clean_title

# --- 全域 API 設定 ---
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API_URL = "https://api.openalex.org/works"

MAX_RETRIES = 2
TIMEOUT = 10

# ========== API Key 管理 ==========
def get_scopus_key():
    return st.secrets.get("scopus_api_key") or _read_key_file("scopus_key.txt")

def get_serpapi_key():
    return st.secrets.get("serpapi_key") or _read_key_file("serpapi_key.txt")

def _read_key_file(filename):
    try:
        with open(filename, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

# ========== [核心] 1. 作者比對邏輯 ==========
def _check_author_match(query_author, result_authors_list):
    if not query_author or len(query_author) < 2:
        return True
    
    # 提取姓氏
    q_family = re.split(r'[, ]', query_author.strip())[0].lower().strip()
    if not q_family: return True

    formatted_results = []
    for auth in result_authors_list:
        if isinstance(auth, dict):
            family = auth.get('family') or auth.get('surname') or auth.get('ce:surname') or ''
            name = auth.get('name') or auth.get('authname') or ''
            formatted_results.append(str(family).lower())
            formatted_results.append(str(name).lower())
        else:
            formatted_results.append(str(auth).lower())
    
    for res_str in formatted_results:
        if q_family in res_str:
            return True
            
    return False

# ========== [核心修正] 2. 標題比對邏輯 (關鍵優化區) ==========

def _is_match(query, result):
    if not query or not result: return False
    
    def get_clean_words(text):
        # 1. 移除學術標籤與括號內容 (如 [PDF], (2022))
        text = re.sub(r'\[.*?\]|\(.*?\)', '', str(text))
        # 2. 移除開頭的數字編號 (如 "15. Wang" -> "Wang")
        text = re.sub(r'^\d+[\.\s]+', '', text.strip())
        # 3. 轉小寫，只留英文與數字單字
        words = re.findall(r'\b[a-z0-9]{3,}\b', text.lower())
        # 4. 移除極度常見的干擾字
        stops = {'the', 'and', 'for', 'with', 'from', 'using', 'based', 'journal', 'researchgate'}
        return [w for w in words if w not in stops]

    q_words = get_clean_words(query)
    r_words = get_clean_words(result)

    # --- 防呆：如果清洗後變空的，嘗試更簡單的清洗 ---
    if not q_words:
        q_words = re.findall(r'\w+', query.lower())
    if not r_words:
        r_words = re.findall(r'\w+', result.lower())

    if not q_words or not r_words: return False

    # --- 核心邏輯：關鍵字交集百分比 ---
    # 只要 query 裡的重要單字，有一定比例出現在結果中即可
    q_set = set(q_words)
    r_set = set(r_words)
    
    # 計算 query 的單字有多少比例出現在 result 中
    intersection = q_set.intersection(r_set)
    
    # 判定 A：如果 query 裡有 60% 以上的單字對中了 (救回 Ko, K.)
    hit_rate = len(intersection) / len(q_set) if q_set else 0
    
    # 判定 B：傳統相似度 (防止極短標題誤判)
    c_q_str = "".join(sorted(list(q_set)))
    c_r_str = "".join(sorted(list(r_set)))
    ratio = SequenceMatcher(None, c_q_str, c_r_str).ratio()

    # --- 最終門檻設定 ---
    # 這裡的門檻是關鍵：0.55 是處理「髒數據」最平衡的數字
    if hit_rate >= 0.55:
        return True
    if ratio > 0.7:
        return True
     # 在 _is_match 函數最後面 return False 之前加入：
     st.write(f"🔍 比對失敗詳情 | 目標: {q_words} | 搜尋結果: {r_words} | 命中率: {hit_rate:.2f}")   
    return False
# --- API 呼叫輔助 ---
def _call_external_api_with_retry(url: str, params: dict, headers=None):
    if not headers: headers = {'User-Agent': 'ReferenceChecker/1.0'}
    for _ in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if response.status_code == 200: return response.json(), "OK"
            if response.status_code in [401, 403]: return None, f"Auth Error ({response.status_code})"
        except: pass
    return None, "Error"

# ========== 1. Crossref ==========

def search_crossref_by_doi(doi, target_title=None):
    if not doi: return None, None, "Empty DOI"
    clean_doi = doi.strip(' ,.;)]}>')
    url = f"https://api.crossref.org/works/{clean_doi}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            item = response.json().get("message", {})
            titles = item.get("title", [])
            res_title = titles[0] if titles else ""
            if target_title and not _is_match(target_title, res_title):
                return None, None, f"DOI Title Mismatch"
            return res_title, item.get("URL") or f"https://doi.org/{clean_doi}", "OK"
        return None, None, f"HTTP {response.status_code}"
    except: return None, None, "Conn Error"

def search_crossref_by_text(title, author=None):
    if not title: return None, "Empty Title"
    params = {'query.bibliographic': title, 'rows': 3} 
    data, status = _call_external_api_with_retry("https://api.crossref.org/works", params)
    
    if status == "OK" and data and data.get('message', {}).get('items'):
        for item in data['message']['items']:
            res_title = item.get('title', [''])[0]
            res_authors = item.get('author', [])
            if _is_match(title, res_title):
                if _check_author_match(author, res_authors):
                    return item.get('URL') or f"https://doi.org/{item.get('DOI')}", "OK"
        return None, "Match failed"
    return None, status

# ========== 2. Scopus ==========

def search_scopus_by_title(title, api_key, author=None):
    if not api_key: return None, "No API Key"
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}
    params = {"query": f'TITLE("{title}")', "count": 1}
    data, status = _call_external_api_with_retry(url, params, headers)
    
    if status == "OK" and data:
        entries = data.get('search-results', {}).get('entry', [])
        if not entries or 'error' in entries[0]: return None, "No results"
        match = entries[0]
        res_title = match.get('dc:title', '')
        res_creator = match.get('dc:creator', '')
        if _is_match(title, res_title):
            if _check_author_match(author, [res_creator]):
                return match.get('prism:url', 'https://www.scopus.com'), "OK"
    return None, "Mismatch"

# ========== 3. Google Scholar (修正邏輯) ==========

def search_scholar_by_title(title, api_key, author=None, raw_text=None):
    if not api_key: return None, "No API Key"
    
    # 內部的搜尋小工具（修正版）
    def _do_search(query_string, match_mode):
        try:
            params = {"engine": "google_scholar", "q": query_string, "api_key": api_key, "num": 5}
            search = GoogleSearch(params)
            results = search.get_dict()
            organic = results.get("organic_results", [])
            
            for res in organic:
                res_title = res.get("title", "")
                res_link = res.get("link", "")
                # 這裡最關鍵：抓取 Google Scholar 回傳的作者片段
                # 格式通常是 "Y Wang, J Chen - Neural Computing and..., 2022"
                res_author_info = res.get("publication_info", {}).get("summary", "")
                
                # 1. 檢查標題
                if _is_match(title, res_title):
                    # 2. 檢查作者 (如果我們有提供 first_author)
                    if valid_search_author:
                        # 只要作者姓氏沒出現在 Google 的作者摘要中，就判定為誤判
                        if valid_search_author.lower() in res_author_info.lower():
                            return res_link, res_title
                        else:
                            continue # 雖然標題像，但作者不對，看下一筆
                    return res_link, res_title
            return None, None
        except: return None, None

    # 清洗作者
    valid_search_author = None
    if author:
        cleaned = re.sub(r'(?i)[\(\[]?\bet\.?\s*al\.?[\)\]]?', '', author).strip()
        cleaned = cleaned.strip(' .,;()[]')
        if len(cleaned) > 1: valid_search_author = cleaned

    # 步驟 1: 標題 + 作者
    if valid_search_author:
        link, status = _do_search(f'{title} {valid_search_author}', "match (Title+Author)")
        if link: return link, status

    # 步驟 2: 純標題
    link, status = _do_search(title, "match (Title Only)")
    if link: return link, status

    # 步驟 3: 原始全文保底 (針對 Ko, K. 最有效的一招)
    if raw_text:
        # 縮短全文避免搜尋過載
        short_raw = raw_text[:150]
        link, status = _do_search(short_raw, "match (Raw Text Fallback)")
        if link: return link, status

    return None, "Not found"

def search_scholar_by_ref_text(ref_text, api_key, target_title=None):
    if not api_key: return None, "No API Key"
    params = {"engine": "google_scholar", "q": ref_text[:150], "api_key": api_key, "num": 1}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if organic:
            res_title = organic[0].get("title", "")
            if target_title and not _is_match(target_title, res_title):
                return None, "Mismatch"
            return organic[0].get("link"), "similar"
    except: pass
    return None, "No results"

# ========== 4. Semantic Scholar & OpenAlex ==========

def search_s2_by_title(title, author=None):
    params = {'query': title, 'limit': 2, 'fields': 'title,url,authors'}
    data, status = _call_external_api_with_retry(S2_API_URL, params)
    if status == "OK" and data.get('data'):
        for match in data['data']:
            res_title = match.get('title')
            res_authors = match.get('authors', [])
            if _is_match(title, res_title):
                if _check_author_match(author, res_authors):
                    return match.get('url'), "OK"
    return None, status

def search_openalex_by_title(title, author=None):
    params = {'search': title, 'per_page': 2}
    data, status = _call_external_api_with_retry(OPENALEX_API_URL, params)
    if status == "OK" and data.get('results'):
        for match in data['results']:
            res_title = match.get('title')
            res_authors = [a['author'].get('display_name', '') for a in match.get('authorships', []) if 'author' in a]
            if _is_match(title, res_title):
                if _check_author_match(author, res_authors):
                    return match.get('doi') or match.get('id'), "OK"
    return None, status

def check_url_availability(url):
    if not url or not url.startswith("http"): return False
    if url.count('/') < 3: return False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True, verify=False)
        return 200 <= resp.status_code < 400
    except: return False





