"""
Flask 主程式入口 — 台股掃描器網站

實際內容已模組化到 webapp/ 套件：
  webapp/core.py    app 物件、驗證、限流、錯誤頁、全域 context
  webapp/shared.py  跨頁面共用 helper 與快取
  webapp/views/     各功能頁面的路由（selection / stock / chips / ...）

保留本檔作為向後相容入口：`python app.py` 與 `from app import app` 皆照舊可用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp import app  # noqa: E402,F401

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    # 綁定 0.0.0.0 = 監聽所有網路介面，
    # 可同時由 localhost / 內網 IP / Tailscale IP 存取。
    # 可用環境變數 HOST / PORT 覆寫。
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '5000'))
    app.run(debug=debug_mode, host=host, port=port)
