# modules/gemini_client.py

import streamlit as st
import google.generativeai as genai
import json
import re

# --- 初始化 Gemini 模型 (接收使用者輸入的 key) ---
def get_gemini_model(api_key):
    """
    初始化 Gemini 模型
    :param api_key: 使用者輸入的 API 金鑰
    """
    try:
        if not api_key:
            st.error("❌ 未提供 Gemini API 金鑰")
            st.stop()

        # 設定 API Key
        genai.configure(api_key=api_key)
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        generation_config = {
            "response_mime_type": "application/json",
        }
        
        # 使用 Flash 模型以求速度與成本平衡
        model = genai.GenerativeModel(
            'gemini-2.5-flash',
            safety_settings=safety_settings,
            generation_config=generation_config
        )
        return model
    except Exception as e:
        st.error(f"❌ 初始化 Gemini 模型失敗，請檢查 Key 是否正確：{e}")
        st.stop()

# --- [修改] 核心 Prompt：採用您提供的詳細分類定義 ---
PROMPT_PARSE_REFERENCES = """
你是一個精確的學術引用解析器。我將提供一段從 PDF/Word 提取的參考文獻原始文本，其中可能包含錯誤的換行。

你的任務是：
1. 將跨越多行的引用合併為單一條目。
2. 識別每一筆獨立的參考文獻。
3. 對於每一筆文獻，提取以下欄位：
   - "text": 完整的參考文獻字符串。
   - "title": 文獻標題，若無正式標題請使用主要描述。
   - "authors": 文獻的作者列表或主要作者。
   - "venue": 文獻出現的期刊名稱或研討會名稱，如果找不到則為 null。
   - "year": 文獻發表年份，如果找不到則為 null。
   - "doi": 文獻的 DOI (如果沒有則為 null)。
   - "url": 文獻的主要 URL (如果沒有則為 null)。
   - "style": 請根據以下定義，選擇最合適的一個分類：
        - "Journal Article" (期刊文章：最常見的學術文獻，格式通常包含作者、標題、期刊名稱、出版年份、卷期和頁碼)
        - "Book" (專著/書籍：包括學術專著、教科書等)
        - "Thesis" (學位論文：如碩士或博士論文)
        - "Conference Paper" (會議資料：包括會前、會中及會後的會議資料，例如會議錄、論文集)
        - "Report" (研究報告：科學研究的報告、技術報告)
        - "Patent" (專利：專利文件)
        - "Newspaper Article" (報紙文章：報刊上發表的文章)
        - "Standard" (標準文件：國家或行業標準，如 ISO, ITU, IEEE)
        - "Preprint" (預印本：尚未正式發表於期刊的論文，如 arXiv, bioRxiv)
        - "Website" (純網頁資源：非上述學術格式的普通網頁)
        - "Other" (其他類：不屬於以上類型的文獻)
   - "citation_format": 文獻引用格式，判斷是 APA、IEEE、Chicago、MLA 或 Other。
4. 請以 JSON 陣列的形式返回所有獨立參考文獻物件。

這是原始文本：
---
{reference_text}
---
"""

def parse_document_with_gemini(model, paragraphs):
    """
    單階段解析參考文獻段落為結構化資料。
    """
    reference_text = "\n".join(paragraphs)

    try:
        prompt = PROMPT_PARSE_REFERENCES.format(reference_text=reference_text)
        response = model.generate_content(prompt)
        
        # 清洗 Markdown 格式，確保只留下純 JSON
        clean_json_text = re.sub(r'```json\n(.*?)\n```', r'\1', response.text, flags=re.DOTALL)

        parsed_refs = json.loads(clean_json_text)

        if isinstance(parsed_refs, list) and len(parsed_refs) > 0:
            return parsed_refs, "解析成功"
        else:
            return None, "Gemini 返回了空的或無效的 JSON 列表。"

    except json.JSONDecodeError:
        return None, f"Gemini 返回了無效的 JSON 格式。原始回應：\n{response.text}"
    except Exception as e:
        return None, f"Gemini 呼叫失敗: {e}"