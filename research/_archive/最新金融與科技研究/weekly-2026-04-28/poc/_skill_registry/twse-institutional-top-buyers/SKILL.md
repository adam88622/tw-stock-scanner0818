---
name: twse-institutional-top-buyers
description: 抓取 TWSE 三大法人買超排行（外資+投信+自營合計）
keywords: ["法人", "買超", "三大法人", "外資", "投信", "TWSE", "排行"]
---

## Steps
1. 呼叫 TWSE API: /fund/T86?date=YYYYMMDD
2. 解析 JSON 取得每檔股票之外資/投信/自營商買賣超
3. 計算三大法人合計買超金額
4. 依買超金額降冪排序
5. 取前 N 名輸出

## Params
```json
{
  "date": "YYYYMMDD",
  "top_n": 10
}
```
