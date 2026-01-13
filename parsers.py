# modules/parsers.py
import re
import unicodedata
import subprocess
import json
import streamlit as st
import tempfile
import os
import platform

# ==============================================================================
# AnyStyle 解析（語系自動偵測逐行模型切換版）
# ==============================================================================

# 🔴 請確保此路徑與你電腦中的 ruby.exe 位置一致
# 根據系統自動切換 ruby 呼叫方式
if platform.system() == "Windows":
    RUBY_EXE = r"ruby" 
else:
    RUBY_EXE = "ruby" # Linux 環境下直接呼叫系統名稱即可

def parse_references_with_anystyle(raw_text_for_anystyle):
    """
    將文獻列表拆分處理：
    1. 含有中文字元：使用自定義模型 (-P custom.mod)
    2. 純英文：使用 AnyStyle 內建預設模型
    """
    if not raw_text_for_anystyle or not raw_text_for_anystyle.strip():
        return [], []

    # 1️⃣ 確認 ruby.exe 存在
    if not os.path.exists(RUBY_EXE):
        st.error(f"❌ 找不到 ruby.exe：{RUBY_EXE}")
        return [], []

    # 2️⃣ 將輸入文字按行拆分，過濾掉空行
    lines = [line.strip() for line in raw_text_for_anystyle.split('\n') if line.strip()]
    
    structured_refs = []
    raw_texts = []

    # 建立進度條
    progress_bar = st.progress(0)
    total_lines = len(lines)

    for i, line in enumerate(lines):
        # 3️⃣ 針對單行文獻進行語言判定
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', line))

        # 4️⃣ 為單行文獻建立暫存檔
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
                encoding="utf-8"
            ) as tmp:
                tmp.write(line)
                tmp_path = tmp.name
        except Exception as e:
            st.error(f"❌ 無法建立暫存檔：{e}")
            continue

        # 5️⃣ 組合指令：根據單行內容動態切換模型
        command = [
            RUBY_EXE,
            "-S",
            "anystyle",
            "-f", "json",
            "parse"
        ]

        if has_chinese:
            # 偵測到中文：插入自定義模型參數
            command.insert(3, "-P")
            command.insert(4, "custom.mod")
        
        command.append(tmp_path)

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True
            )

            stdout = process.stdout.strip()

            # 擷取 JSON
            if not stdout.startswith("["):
                match = re.search(r"\[.*\]", stdout, re.DOTALL)
                if match:
                    stdout = match.group(0)

            line_data = json.loads(stdout)
            print(line_data)

            for item in line_data:
                cleaned_item = {}

                # 格式化欄位內容
                for key, value in item.items():
                    if isinstance(value, list):
                        if key == "author":
                            authors = []
                            for a in value:
                                if isinstance(a, dict):
                                    parts = [p for p in [a.get("given"), a.get("family")] if p]
                                    authors.append(" ".join(parts))
                                else:
                                    authors.append(str(a))
                            cleaned_item["authors"] = ", ".join(authors)
                        else:
                            cleaned_item[key] = " ".join(map(str, value))
                    else:
                        cleaned_item[key] = value

                # 建立 fallback 原始文字預覽（確保 app.py 搜尋時有 text 欄位）
                if "text" not in cleaned_item:
                    cleaned_item["text"] = line

                structured_refs.append(cleaned_item)
                raw_texts.append(cleaned_item["text"])

        except Exception as e:
            # 發生錯誤時記錄該行但繼續執行
            st.error(f"解析第 {i+1} 行時發生錯誤：{e}")
        finally:
            # 刪除暫存檔
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        
        # 更新進度
        progress_bar.progress((i + 1) / total_lines)

    return raw_texts, structured_refs


# ==============================================================================
# 標題清洗函式
# ==============================================================================

def clean_title(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    dash_chars = ["-", "–", "—", "−", "‐", "-"]
    for d in dash_chars:
        text = text.replace(d, "")
    cleaned = [
        ch.lower()
        for ch in text
        if unicodedata.category(ch)[0] in ("L", "N", "Z")
    ]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()

def clean_title_for_remedial(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    dash_chars = ["-", "–", "—", "−", "‐", "-"]
    for d in dash_chars:
        text = text.replace(d, "")
    text = re.sub(r"\b\d+\b", "", text)
    cleaned = [
        ch.lower()
        for ch in text
        if unicodedata.category(ch)[0] in ("L", "N", "Z")
    ]

    return re.sub(r"\s+", " ", "".join(cleaned)).strip()

