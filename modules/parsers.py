# modules/parsers.py
import re
import unicodedata
import subprocess
import json
import streamlit as st
import tempfile
import os
import shutil

# ==============================================================================
# AnyStyle 解析 (雲端環境自動適配版)
# ==============================================================================

def get_ruby_path():
    """自動偵測環境中的 ruby 路徑"""
    path = shutil.which("ruby")
    if path:
        return path
    # 如果系統找不到，給予預設值
    return "ruby"

RUBY_EXE = get_ruby_path()

def parse_references_with_anystyle(raw_text_for_anystyle):
    if not raw_text_for_anystyle or not raw_text_for_anystyle.strip():
        return [], []

    # 移除原本的 os.path.exists 檢查，改用 try-except 捕捉執行錯誤
    lines = [line.strip() for line in raw_text_for_anystyle.split('\n') if line.strip()]
    structured_refs = []
    raw_texts = []

    progress_bar = st.progress(0)
    total_lines = len(lines)

    for i, line in enumerate(lines):
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', line))

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(line)
                tmp_path = tmp.name
            
            # 組合指令
            command = [RUBY_EXE, "-S", "anystyle", "-f", "json", "parse"]
            
            # 檢查 custom.mod 是否存在，存在才使用
            if has_chinese and os.path.exists("custom.mod"):
                command.insert(3, "-P")
                command.insert(4, "custom.mod")
            
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8"
            )

            if process.returncode != 0:
                # 如果報錯是因為找不到 anystyle，嘗試直接用 anystyle 指令
                alt_command = ["anystyle", "-f", "json", "parse"]
                if has_chinese and os.path.exists("custom.mod"):
                    alt_command.insert(1, "-P")
                    alt_command.insert(2, "custom.mod")
                alt_command.append(tmp_path)
                process = subprocess.run(alt_command, capture_output=True, text=True, encoding="utf-8")

            stdout = process.stdout.strip()

            if stdout:
                if not stdout.startswith("["):
                    match = re.search(r"\[.*\]", stdout, re.DOTALL)
                    if match: stdout = match.group(0)
                
                line_data = json.loads(stdout)
                for item in line_data:
                    cleaned_item = {}
                    for key, value in item.items():
                        if isinstance(value, list):
                            if key == "author":
                                authors = []
                                for a in value:
                                    if isinstance(a, dict):
                                        parts = [p for p in [a.get("given"), a.get("family")] if p]
                                        authors.append(" ".join(parts))
                                    else: authors.append(str(a))
                                cleaned_item["authors"] = ", ".join(authors)
                            else:
                                cleaned_item[key] = " ".join(map(str, value))
                        else:
                            cleaned_item[key] = value

                    if "text" not in cleaned_item:
                        cleaned_item["text"] = line
                    structured_refs.append(cleaned_item)
                    raw_texts.append(cleaned_item["text"])

        except Exception as e:
            st.error(f"解析第 {i+1} 行時發生錯誤：{e}")
        finally:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        
        progress_bar.progress((i + 1) / total_lines)

    return raw_texts, structured_refs

# 標題清洗函式保持不變...
def clean_title(text):
    if not text: return ""
    text = unicodedata.normalize("NFKC", str(text))
    cleaned = [ch.lower() for ch in text if unicodedata.category(ch)[0] in ("L", "N", "Z")]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()

def clean_title_for_remedial(text):
    if not text: return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = re.sub(r"\b\d+\b", "", text)
    cleaned = [ch.lower() for ch in text if unicodedata.category(ch)[0] in ("L", "N", "Z")]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()
