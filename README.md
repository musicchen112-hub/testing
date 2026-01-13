# 📚 Reference Checker

Reference Checker 是一個簡單實用的工具，可自動從 Word 檔（`.docx`）或 pdf 檔（`.pdf`）中擷取參考文獻標題，並透過**Crossref API** 、 **Scopus API** 與 **SerpAPI** 進行查詢，協助研究者快速判斷參考資料的正確性與可查詢性。

⚠️ 注意事項
為節省比對時間，本系統僅查核具有 DOI 的期刊論文，不會檢查期刊名稱、作者、卷期或頁碼，比對僅以「篇名」為主。本系統提供之比對結果僅供初步參考，建議使用者在比對後仍應進行人工確認。請勿將本系統結果作為學術倫理判斷之唯一依據。

---

主要功能：
- 自動擷取 APA 或 IEEE 格式的參考文獻
- 若參考文獻中有 DOI，則直接比對Crossref，優先採用
- 若無 DOI，改以篇名（title）查詢 Scopus
- 若 Scopus 查無結果，則使用篇名（title）查詢 Google Scholar (SerpAPI)
- 若以篇名（title）查詢 Google Scholar 無結果，系統會改以 **整段參考文獻文字** 透過 SerpAPI 呼叫 Google Scholar，僅搜尋 1 筆結果。
- 取回的第一筆結果之標題若包含於原參考文獻文字中，則視為 **Google Scholar 補救命中**。
- 根據查詢結果自動分類為「Crossref 有 DOI 資訊」「標題命中（Scopus）」「標題命中（Google Scholar）」「Google Scholar 補救命中」「Google Scholar 類似標題」「均無結果」
- 提供結果視覺化、分頁顯示，方便使用者人工確認
- 支援結果下載為 CSV 檔案

---

申請 API key：
- **Scopus API Key**  
  申請網址：[Elsevier Developer Portal](https://dev.elsevier.com/)

- **SerpAPI Key**  
  申請網址：[Google Search API](https://serpapi.com)

- **Crossref**  
  Crossref 不需申請 API key，但建議提供一組有效 Email，作為 API 請求中的 mailto 參數，避免被限速並提高查詢成功率。

---

設定金鑰檔案：
1. 在專案根目錄建立一個 `.streamlit` 資料夾  
2. 在 `.streamlit` 資料夾內新增 `secrets.toml` 檔案  
3. 在 `secrets.toml` 內貼上以下內容（用你的金鑰取代）：

```toml
scopus_api_key = "在這裡貼您的 Scopus Key"
serpapi_key    = "在這裡貼您的 SerpAPI Key"
```


---
streamlit 地端部署：
1. streamlit run app.py
2. 預設會在 http://localhost:8501 開啟
3. 可自行更改埠號

Streamlit Cloud：
1. 將整個程式上傳到 GitHub，並設定為公開
2. 前往 https://streamlit.io/cloud 並登入
3. 點選 New App → 選擇你的 GitHub 專案 → 指定主程式 app.py
4. 部署完成後，可以自行取名一個公開網址（例如 https://your-app.streamlit.app）
5. 請到 App → Settings → Secrets 貼上金鑰：
```toml
scopus_api_key = "在這裡貼您的 Scopus Key"
serpapi_key = "在這裡貼您的 SerpAPI Key"
```