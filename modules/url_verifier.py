# modules/url_verifier.py

import re
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

from .parsers import clean_title
from .api_clients import _is_match


# =============================================================================
# URL 類型判斷
# =============================================================================

def classify_url_type(url: str) -> str:
    u = url.lower()

    # DOI
    if "doi.org" in u or re.search(r'10\.\d{4,9}/', u):
        return "doi"

    # 明確學術出版頁
    if any(d in u for d in [
        "arxiv.org",
        "acm.org",
        "ieee.org",
        "springer.com",
        "sciencedirect.com",
        "nature.com"
    ]):
        return "academic"

    # Software / Project
    if any(d in u for d in [
        "github.com",
        "gitlab.com",
        "project",
        "software",
        "platform"
    ]):
        return "software"

    return "generic"


# =============================================================================
# 工具：作者姓氏抽取（支援簡稱）
# =============================================================================

def extract_author_surnames(author_str: str) -> set:
    if not author_str:
        return set()

    surnames = set()
    for part in re.split(r'[;,]', author_str):
        part = part.strip()
        if not part:
            continue

        # LeCun, Y. | Y. LeCun | Goodfellow et al.
        tokens = part.replace("et al.", "").split()
        surname = tokens[0] if "," in part else tokens[-1]
        surnames.add(surname.lower())

    return surnames


# =============================================================================
# 工具：抓取半結構化頁面 metadata
# =============================================================================

def fetch_page_semantic_meta(url: str, parsed_ref: dict) -> dict | None:
    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # ---------- Title ----------
        title = None

        # meta priority
        for key in [
            {"name": "citation_title"},
            {"property": "og:title"},
            {"name": "dc.title"}
        ]:
            tag = soup.find("meta", key)
            if tag and tag.get("content"):
                title = tag["content"].strip()
                break

        # DOM fallback
        if not title:
            for tag_name in ["h1", "h2"]:
                tag = soup.find(tag_name)
                if tag and len(tag.text.strip()) > 8:
                    title = tag.text.strip()
                    break

        if not title:
            return None

        # ---------- Title 必須先命中 ----------
        if not _is_match(parsed_ref.get("title"), title):
            return None

        # ---------- Search Zone ----------
        zone = soup.find("article") or soup.find("main") or soup

        text = zone.get_text(" ", strip=True).lower()

        # ---------- Authors ----------
        authors = set()

        # meta authors
        for tag in soup.find_all("meta", {"name": "citation_author"}):
            val = tag.get("content", "")
            if val:
                authors.add(val.split()[-1].lower())

        # DOM surname scan
        ref_surnames = extract_author_surnames(parsed_ref.get("authors", ""))
        for s in ref_surnames:
            if s and s in text:
                authors.add(s)

        # ---------- Year ----------
        year = None
        year_match = re.search(r'\b(19|20)\d{2}\b', text)
        if year_match:
            year = year_match.group(0)

        return {
            "title": title,
            "authors": list(authors),
            "year": year,
            "raw_text": text
        }

    except Exception:
        return None


# =============================================================================
# 驗證策略 1：論文（DOI / Academic）
# =============================================================================

def verify_academic_reference(parsed_ref: dict, meta: dict) -> bool:
    score = 0

    # 標題已在前面檢查過
    score += 1

    # 作者（姓氏交集）
    ref_surnames = extract_author_surnames(parsed_ref.get("authors", ""))
    meta_surnames = set(meta.get("authors", []))

    if ref_surnames & meta_surnames:
        score += 1

    # 年份
    if parsed_ref.get("date") and meta.get("year"):
        if str(parsed_ref["date"])[:4] == str(meta["year"]):
            score += 1

    # 高度標題相似再加分
    if SequenceMatcher(
        None,
        clean_title(parsed_ref.get("title")),
        clean_title(meta.get("title"))
    ).ratio() >= 0.95:
        score += 1

    return score >= 2


# =============================================================================
# 驗證策略 2：Software / Project（MISP 類）
# =============================================================================

def verify_software_project(parsed_ref: dict, meta: dict) -> bool:
    ref_title = clean_title(parsed_ref.get("title"))
    page_title = clean_title(meta.get("title"))

    if not ref_title or not page_title:
        return False

    # 標題關鍵字交集
    overlap = set(ref_title.split()) & set(page_title.split())

    if len(overlap) >= 2:
        return True

    # fallback：整體包含
    return ref_title in page_title or page_title in ref_title


# =============================================================================
# 驗證策略 3：Generic Website（最低可信）
# =============================================================================

def verify_generic_website(parsed_ref: dict, meta: dict) -> bool:
    # 只要求標題弱命中
    return _is_match(parsed_ref.get("title"), meta.get("title"))


# =============================================================================
# 對外統一入口
# =============================================================================

def verify_url_candidate(parsed_ref: dict, url: str) -> tuple[bool, str]:
    """
    回傳：
        (是否通過驗證, 類型標籤)
    """

    url_type = classify_url_type(url)

    meta = fetch_page_semantic_meta(url, parsed_ref)
    if not meta:
        return False, f"{url_type}: no semantic match"

    if url_type in ("doi", "academic"):
        ok = verify_academic_reference(parsed_ref, meta)
        return ok, "academic"

    if url_type == "software":
        ok = verify_software_project(parsed_ref, meta)
        return ok, "software"

    ok = verify_generic_website(parsed_ref, meta)
    return ok, "generic"
