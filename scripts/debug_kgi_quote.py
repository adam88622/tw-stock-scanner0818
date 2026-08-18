"""手動測試 KGI portal quote endpoint。

執行方式：
    python scripts/debug_kgi_quote.py

需求：
    1. KGI portal (D:/claude/src/KGI-Trading-Package) 已啟動，listen :8890
    2. user 已透過 portal UI 登入（登入完才會自動訂閱 TSE001 1mK）
    3. 在盤中執行（9:00~13:30）效果最好；盤後 store 不會更新但 store 內仍有最後快取
"""
import json
import time

import requests


def main():
    base = "http://127.0.0.1:8890"

    try:
        r = requests.get(f"{base}/api/quote/store", timeout=5)
        print("查 store:", r.status_code, r.json())
    except Exception as e:
        print(f"查 store 失敗：{e}")
        return

    for i in range(3):
        try:
            r = requests.get(f"{base}/api/quote/kbar/TSE001", timeout=5)
            text = json.dumps(
                r.json() if r.status_code == 200 else {"detail": r.text},
                ensure_ascii=False,
                default=str,
                indent=2,
            )
            print(f"\n[round {i+1}] HTTP {r.status_code}:")
            print(text[:500])
        except Exception as e:
            print(f"\n[round {i+1}] 查 TSE001 失敗：{e}")
        if i < 2:
            time.sleep(60)


if __name__ == "__main__":
    main()
