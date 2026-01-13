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

# ========== [核心] 1. 作者比對邏輯 (新增) ==========
def _check_author_match(query_author, result_authors_list):
    """
    寬鬆比對作者姓氏
    :param query_author: 使用者輸入的作者字串 (例如 "Smith, J." 或 "Li")
    :param result_authors_list: API 回傳的作者列表 (List of strings or dicts)
    """
    # 如果使用者沒提供作者，或是輸入的作者字串太短(可能解析失敗)，就跳過檢查(視為通過)
    if not query_author or len(query_author) < 2:
        return True
    
    # 提取查詢作者的姓氏 (假設格式為 "Family, Given" 或 "Family Given")
    # 簡單策略：取逗號前或空格前的第一個詞作為姓氏
    q_family = re.split(r'[, ]', query_author.strip())[0].lower().strip()
    
    # 如果姓氏太短 (例如 "Li", "Ng")，比對時要小心，但這裡先採寬鬆策略
    if not q_family: return True

    # 處理 API 回傳的作者列表
    formatted_results = []
    for auth in result_authors_list:
        if isinstance(auth, dict):
            # 針對 Crossref/Scopus 常見的 dict 結構 {'family': 'Smith', 'given': 'John'}
            family = auth.get('family') or auth.get('surname') or auth.get('ce:surname') or ''
            name = auth.get('name') or auth.get('authname') or '' # Semantic Scholar 有時是 'name'
            formatted_results.append(str(family).lower())
            formatted_results.append(str(name).lower())
        else:
            # 純字串
            formatted_results.append(str(auth).lower())
    
    # 檢查：只要查詢的姓氏出現在 API 結果的任何一個作者名字中，就算 Pass
    for res_str in formatted_results:
        if q_family in res_str:
            return True
            
    return False

# ========== [核心] 2. 標題比對邏輯 (包含您之前的寬鬆優化) ==========
# 在 modules/api_clients.py 中找到 _is_match 函式並修改

def _is_match(query, result):
    if not query or not result: return False
    c_q = clean_title(query)
    c_r = clean_title(result)
    
    # --- 新增：強效去噪 ---
    # 移除常見的非標題字眼，避免它們導致比對失敗
    def remove_noise(text):
        # 移除 4位數年份 (如 2023, 2024)
        text = re.sub(r'\b(19|20)\d{2}\b', '', text)
        # 移除 arXiv, bioRxiv, Available, Online 等字眼
        text = re.sub(r'\b(arxiv|biorxiv|available|online|access)\b', '', text, flags=re.IGNORECASE)
        # 移除多餘空白
        return " ".join(text.split())

    c_q = remove_noise(c_q)
    c_r = remove_noise(c_r)
    # ---------------------

    # 1. 針對 Query 是長段落... (維持原樣)
    if len(c_q) > len(c_r) * 1.5:
        if c_r in c_q: return True

    # 2. 相似度比對 (維持原樣)
    ratio = SequenceMatcher(None, c_q, c_r).ratio()
    if ratio >= 0.8: return True  # 建議稍微調降到 0.8 以容忍少許差異
    
    # 3. 關鍵字比對
    q_words = set(c_q.split())
    r_words = set(c_r.split())
    stop_words = {'a', 'an', 'the', 'of', 'in', 'for', 'with', 'on', 'at', 'by', 'and', 'from', 'to'} # 增加一些介係詞
    
    # ... (中間省略) ...

    # 反向檢查 (Query 的重要單字都在 Result 裡)
    missing_important_in_result = [w for w in q_words if w not in stop_words and w not in r_words]
    
    # --- 新增：容錯機制 ---
    # 如果只差 1 個字，且那個字很短或是數字，我們就當作它是雜訊，予以通過
    if len(missing_important_in_result) <= 1:
        # 如果 Query 很長，容許 1 個字的誤差是合理的
        if len(q_words) >= 5: 
            return True
    # ---------------------

    if len(missing_important_in_result) == 0:
        if len(c_q) > len(c_r) * 0.3:
            return True

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

# ========== 1. Crossref (含作者比對) ==========

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
                return None, None, f"DOI Title Mismatch: {res_title[:40]}..."
                
            return res_title, item.get("URL") or f"https://doi.org/{clean_doi}", "OK"
        return None, None, f"HTTP {response.status_code}"
    except: return None, None, "Conn Error"

def search_crossref_by_text(title, author=None):
    if not title: return None, "Empty Title"
    params = {'query.bibliographic': title, 'rows': 2} # 抓前2筆增加機會
    if author:
        params['query.author'] = author # Crossref 支援直接搜作者
        
    data, status = _call_external_api_with_retry("https://api.crossref.org/works", params)
    
    if status == "OK" and data and data.get('message', {}).get('items'):
        for item in data['message']['items']:
            res_title = item.get('title', [''])[0]
            res_authors = item.get('author', []) # 取得作者列表
            
            # 雙重檢查：標題要對 + 作者要對
            if _is_match(title, res_title):
                if _check_author_match(author, res_authors):
                    return item.get('URL') or f"https://doi.org/{item.get('DOI')}", "OK"
                else:
                    # 如果標題對但作者不對，繼續找下一筆 (可能剛好是同名文章)
                    continue 
                    
        return None, "Match failed (Title or Author mismatch)"
    return None, status

# ========== 2. Scopus (新增作者比對) ==========

def search_scopus_by_title(title, api_key, author=None):
    """
    注意：app.py 呼叫此函式時，建議更新傳入 author 參數
    """
    if not api_key: return None, "No API Key"
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}
    params = {"query": f'TITLE("{title}")', "count": 1}
    
    data, status = _call_external_api_with_retry(url, params, headers)
    
    if status == "OK" and data:
        entries = data.get('search-results', {}).get('entry', [])
        if not entries or 'error' in entries[0]:
            return None, "(No results found)"
        
        match = entries[0]
        res_title = match.get('dc:title', '')
        
        # Scopus 的作者通常在 'dc:creator' (第一作者) 或需要另外解析
        # Search API 的簡單回應通常只給 'dc:creator'
        res_creator = match.get('dc:creator', '')
        
        if _is_match(title, res_title):
            if _check_author_match(author, [res_creator]):
                return match.get('prism:url', 'https://www.scopus.com'), "OK"
            else:
                return None, f"Author Mismatch (Found: {res_creator})"
        else:
            return None, f"Title Mismatch: {res_title[:30]}..."
            
    return None, "Error"

# ========== 3. Google Scholar (無作者欄位，維持原樣) ==========


def search_scholar_by_title(title, api_key, author=None, raw_text=None):
    st.write(f"🔍 正在嘗試指紋搜尋: {title[:30]}...") # 這是暫時的測試碼，會在畫面顯示
    import re
    from serpapi import GoogleSearch

    # 1. 構建指紋搜尋字串 (Fingerprint Query)
    # 如果原始文本中有研討會名稱 (如 ICAIT)，一定要抓進來
    conference = ""
    if raw_text:
        conf_match = re.search(r'(ICAIT|CVPR|nips|arxiv|IEEE|ACM)\s*20\d{2}', raw_text, re.I)
        if conf_match:
            conference = conf_match.group(0)

    # 組合搜尋詞：標題前段 + 作者 + 研討會
    # 對於 Ko, K. 這筆，搜尋詞會變成 "RAG for Document Query Automation Ko 2024 ICAIT"
    clean_title = re.sub(r'[^\w\s]', '', title)[:60]
    query = f"{clean_title} {author if author else ''} {conference}".strip()

    search = GoogleSearch({
        "q": query,
        "api_key": api_key,
        "engine": "google_scholar",
        "hl": "en"
    })
    
    results = search.get_dict()
    
    if "organic_results" in results:
        # 這裡很關鍵：我們檢查前三筆，而不是只抓第一筆
        for entry in results["organic_results"][:3]:
            found_title = entry.get("title", "")
            found_link = entry.get("link", "")
            
            # 比對關鍵字是否出現在搜尋結果標題中
            # 只要 RAG 和 Document 同時出現，就極大機率是正確的
            keywords = ["RAG", "Document", "Automation"]
            if all(k.lower() in found_title.lower() for k in keywords[:2]):
                return found_link, found_title
                
    return None, None

    # ==========================================
    # 步驟 0: 智慧清洗作者 (針對您提到的混合狀況)
    # ==========================================
    valid_search_author = None
    if author:
        # 1. 先把 (et al), [et al], et al. 全部拿掉
        cleaned = re.sub(r'(?i)[\(\[]?\bet\.?\s*al\.?[\)\]]?', '', author).strip()
        
        # 2. 清理乾淨後，把頭尾多餘的標點符號 (逗號、句號、分號) 修剪掉
        # 這樣 "Smith, et al." 會變成 "Smith" (原本會剩下 "Smith,")
        cleaned = cleaned.strip(' .,;()[]')
        
        if len(cleaned) > 1:
            valid_search_author = cleaned

    # ==========================================
    # 步驟 1: 標題 + 作者 (最準確)
    # ==========================================
    # 狀況 A: 原本是 "Smith et al" -> 這裡會搜 "Title Smith" (成功!)
    # 狀況 B: 原本是 "John Smith"  -> 這裡會搜 "Title John Smith" (更準!)
    if valid_search_author:
        link, status = _do_search(f'{title} {valid_search_author}', "match (Title+Author)")
        if link: return link, status

    # ==========================================
    # 步驟 2: 純標題 (寬鬆補救)
    # ==========================================
    # 如果作者解析出來是空的，或第一關沒找到，自動退回這裡
    link, status = _do_search(title, "match (Title Only)")
    if link: return link, status

    # ==========================================
    # 步驟 3: 原始全文 (終極保底)
    # ==========================================
    if raw_text and len(raw_text) > 10:
        link, status = _do_search(raw_text, "match (Raw Text Fallback)")
        if link: return link, status

    return None, "No match found after 3 attempts"

def search_scholar_by_ref_text(ref_text, api_key, target_title=None):
    if not api_key: return None, "No API Key"
    params = {"engine": "google_scholar", "q": ref_text, "api_key": api_key, "num": 1}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if organic:
            res_title = organic[0].get("title", "")
            if target_title and not _is_match(target_title, res_title):
                return None, "Title mismatch in fallback"
            return organic[0].get("link"), "similar"
    except: pass
    return None, "No results"

# ========== 4. Semantic Scholar & OpenAlex (含作者比對) ==========

def search_s2_by_title(title, author=None):
    # 增加請求 'authors' 欄位
    params = {'query': title, 'limit': 1, 'fields': 'title,url,authors'}
    data, status = _call_external_api_with_retry(S2_API_URL, params)
    if status == "OK" and data.get('data'):
        match = data['data'][0]
        res_title = match.get('title')
        res_url = match.get('url')
        res_authors = match.get('authors', []) # S2 回傳 [{'authorId':..., 'name': '...'}]

        if _is_match(title, res_title):
            if _check_author_match(author, res_authors):
                return res_url, "OK"
            return None, "Author mismatch"
            
        return None, "Match failed"
    return None, status

def search_openalex_by_title(title, author=None):
    params = {'search': title, 'per_page': 1}
    data, status = _call_external_api_with_retry(OPENALEX_API_URL, params)
    
    if status == "OK" and data.get('results'):
        match = data['results'][0]
        res_title = match.get('title')
        # OpenAlex 作者結構: 'authorships': [{'author': {'display_name': '...'}}]
        res_authors = []
        for authorship in match.get('authorships', []):
            if 'author' in authorship:
                res_authors.append(authorship['author'].get('display_name', ''))

        if _is_match(title, res_title):
            if _check_author_match(author, res_authors):
                url = match.get('doi') or match.get('id')
                if url: return url, "OK"
                return None, "No Link"
            return None, "Author mismatch"
            
        return None, "Title mismatch"
            
    return None, status if status != "OK" else "No results found"

def check_url_availability(url):
    # 這裡加入您提過的：過濾純首頁 (例如 https://www.sans.org)
    if not url or not url.startswith("http"): return False
    
    # 簡單過濾：如果路徑只有 domain，極大機率是首頁而非論文頁
    # 邏輯：計算 '/' 的數量。https://abc.com 只有 2 個 '/'。https://abc.com/paper 有 3 個。
    if url.count('/') < 3: 
        return False
        
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True, verify=False)
        return 200 <= resp.status_code < 400

    except: return False

