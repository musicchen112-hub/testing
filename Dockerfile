# 使用輕量級的 Ruby 基礎映像檔
FROM ruby:3.3-slim

# 🛠️ 關鍵修正：安裝編譯工具 (build-essential)
# AnyStyle 依賴底層 C 語言庫，必須有 gcc 和 make 才能安裝
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安裝 AnyStyle CLI 工具
RUN gem install anystyle-cli

# 設定工作目錄
WORKDIR /app

# 設定預設指令
ENTRYPOINT ["anystyle"]