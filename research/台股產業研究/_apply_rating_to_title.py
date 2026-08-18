# -*- coding: utf-8 -*-
"""把投資評等標記接到每篇研究報告的 <title>（插在 | GiS 之前，/research 列表才顯示得到）。
可重複執行：若標題已含評等標記則跳過。只動 <title>，不改正文。"""
import re, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))

# 評等：個股=買進/增持/中立/減碼；產業=偏多；總經/策略/產業地圖=None(不標)
RATING = {
    '1_研究員觀點.html': '增持',
    '3008_大立光法說會20260417.html': '增持',
    '6411_研究員觀點.html': '中立',
    'Apple摺疊機供應鏈報告.html': '偏多',
    'CoPoS玻璃基板供應鏈_產業概覽.html': '偏多',
    'GiS_2049_上銀_研究報告.html': '中立',
    'GiS_2327_國巨_研究報告.html': '增持',
    'GiS_4958_臻鼎KY_研究報告.html': '中立',
    'GiS_BBU_Industry_Report_20260422.html': '偏多',
    'GiS_功率半導體與第三代半導體_產業研究報告.html': None,   # 產業地圖，未設評等
    'GiS_記憶體產業_研究報告.html': '偏多',
    'GiS_車用產業鏈研究報告.html': '偏多',
    'TW-01-taiwan-factor-study_report.html': None,            # 量化策略
    'ipc-industry-report.html': '偏多',
    'mideast-war-commodity-chain-report.html': None,          # 總經傳導鏈
    'rate-cycle-asset-study-report.html': None,               # 總經資產輪動
    '世芯3661_研究員觀點.html': '買進',
    '中砂1560_研究員觀點.html': '減碼',
    '京元電2449_研究員觀點.html': '買進',
    '低軌道衛星產業研究報告.html': '偏多',
    '優群3217_研究員觀點.html': '買進',
    '元太8069_研究員觀點.html': '增持',
    '光通訊產業研究報告.html': '偏多',
    '力成6239_研究員觀點.html': '買進',
    '匯鑽科_8431_GiS估值報告.html': '增持',
    '台積電1Q26法說會報告.html': '偏多',
    '台積電設備族群研究報告.html': '偏多',
    '台達電2308_研究員觀點.html': '買進',
    '國巨2327_研究員觀點.html': '增持',
    '川湖2059_研究員觀點.html': '增持',
    '弘塑3131_研究員觀點.html': '中立',
    '捷敏-KY6525_研究員觀點.html': '增持',
    '旺宏2337_研究員觀點.html': '增持',
    '昇陽半導體8028_研究員觀點.html': '減碼',
    '特化化工產業報告_202603.html': '偏多',
    '矽晶圓族群研究報告.html': '偏多',
    '研華2395_研究員觀點.html': '中立',
    '穎崴6515_研究員觀點.html': '中立',
    '穩懋聯亞產能溢出代工可行性研究_v4.html': '偏多',
    '群聯8299_研究員觀點.html': '買進',
    '聯發科2454_研究員觀點.html': '增持',
    '聯詠3034_研究員觀點.html': '買進',
    '被動元件族群研究報告.html': '增持',
}
# 標題殘缺者，整段重寫內文
RETITLE = {'金像電2368_研究員觀點.html': '金像電 2368 首次覆蓋 — 增持 | GiS'}

RATING_WORDS = ['買進', '增持', '中立', '減碼', '偏多', '賣出']
# 分隔符：插在第一個 (| / ｜ / · ) + GiS 之前
SEP = re.compile(r'(\s*[|｜·]\s*GiS)')

def patch_title(inner, rating):
    if any(w in inner for w in RATING_WORDS):   # 已標記過
        return inner, False
    suffix = ' — ' + rating
    m = SEP.search(inner)
    if m:
        new = inner[:m.start()] + suffix + inner[m.start():]
    else:
        new = inner + suffix
    return new, True

def main():
    for fn in sorted(set(list(RATING) + list(RETITLE))):
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            print('MISS  ', fn); continue
        with open(path, encoding='utf-8', errors='ignore') as fh:
            html = fh.read()
        m = re.search(r'(<title[^>]*>)(.*?)(</title>)', html, re.S | re.I)
        if not m:
            print('NOTITLE', fn); continue
        old_inner = m.group(2).strip()
        if fn in RETITLE:
            new_inner, changed = RETITLE[fn], (old_inner != RETITLE[fn])
        else:
            rating = RATING[fn]
            if rating is None:
                print('SKIP  ', fn, '(無評等) <%s>' % old_inner); continue
            new_inner, changed = patch_title(old_inner, rating)
        if not changed:
            print('KEEP  ', fn, '<%s>' % old_inner); continue
        new_html = html[:m.start()] + m.group(1) + new_inner + m.group(3) + html[m.end():]
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(new_html)
        print('OK    ', fn)
        print('        舊:', old_inner)
        print('        新:', new_inner)

if __name__ == '__main__':
    main()
