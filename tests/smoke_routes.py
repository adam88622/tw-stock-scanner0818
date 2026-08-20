"""路由等價性煙霧測試 — 重構前後比對用。

用法：
    python tests/smoke_routes.py baseline   # 存基準到 tests/route_baseline.json
    python tests/smoke_routes.py compare    # 與基準比對（route 集合必須一致；
                                            #  status 變成 404/405 視為失敗，其餘差異列警告）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 停用 Basic Auth（空帳密 = 放行）；先佔位讓 load_dotenv 不覆蓋
os.environ['SCANNER_USER'] = ''
os.environ['SCANNER_PASS'] = ''

BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'route_baseline.json')

# 帶參數的 route 用這些替身值
PARAM_FILL = {'name': '半導體', 'filepath': '_smoke_nonexistent.html'}


def collect_status():
    from app import app
    app.config['TESTING'] = True
    results = {}
    client = app.test_client()
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        if rule.endpoint == 'static' or 'GET' not in rule.methods:
            continue
        url = str(rule)
        for arg in rule.arguments:
            fill = PARAM_FILL.get(arg, 'x')
            url = url.replace(f'<{arg}>', fill)
            url = url.replace(f'<path:{arg}>', fill)
        try:
            resp = client.get(url)
            results[str(rule)] = resp.status_code
        except Exception as e:
            results[str(rule)] = f'EXC:{type(e).__name__}'
    return results


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'compare'
    current = collect_status()
    if mode == 'baseline':
        with open(BASELINE_PATH, 'w', encoding='utf-8') as f:
            json.dump(current, f, ensure_ascii=False, indent=1, sort_keys=True)
        print(f'baseline saved: {len(current)} GET routes -> {BASELINE_PATH}')
        return 0

    with open(BASELINE_PATH, encoding='utf-8') as f:
        baseline = json.load(f)
    failed = False
    missing = set(baseline) - set(current)
    added = set(current) - set(baseline)
    if missing:
        failed = True
        print(f'FAIL 消失的 route: {sorted(missing)}')
    if added:
        print(f'WARN 新增的 route: {sorted(added)}')
    for r in sorted(set(baseline) & set(current)):
        if baseline[r] != current[r]:
            if current[r] in (404, 405):
                failed = True
                print(f'FAIL {r}: {baseline[r]} -> {current[r]}')
            else:
                print(f'WARN {r}: {baseline[r]} -> {current[r]}（可能是外部資料源波動）')
    print(f'{"FAIL" if failed else "OK"}: {len(current)} routes checked')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
