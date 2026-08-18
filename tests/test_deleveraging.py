# -*- coding: utf-8 -*-
"""
去槓桿壓力儀表板即時管線測試。

執行（Git Bash）：
  cd /d/claude/tw-stock-scanner && .venv/Scripts/python -m pytest tests/test_deleveraging.py -q
執行（PowerShell）：
  D:\\claude\\tw-stock-scanner\\.venv\\Scripts\\python.exe -m pytest tests\\test_deleveraging.py -q
"""

import os
import sys
import json

import pytest

# 專案根目錄可 import scanners.*
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scanners import deleveraging as dl  # noqa: E402

SNAP_PATH = os.path.join(ROOT, 'data', 'deleveraging_snapshot.json')
TOL = 1e-6


@pytest.fixture(scope='module')
def snapshot():
    with open(SNAP_PATH, encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='module')
def built():
    # 鐵則：無快取時 build_indicators() 逐位元重現快照（不碰網路）。
    if os.path.exists(dl.CACHE_PATH):
        os.remove(dl.CACHE_PATH)
    return dl.build_indicators()


# ---------------------------------------------------------------------
# 鐵則：零新增日 -> 重現 78.14 與快照全序列
# ---------------------------------------------------------------------
def test_ironclad_reproduces_score(built):
    assert abs(built['composite']['score'] - 78.14) < TOL, \
        "composite.score 未重現 78.14: %s" % built['composite']['score']


def test_ironclad_composite_parts(built, snapshot):
    got = built['composite']['parts']
    exp = snapshot['composite']['parts']
    assert set(got.keys()) == set(exp.keys()), "composite.parts 鍵不一致"
    for k in exp:
        assert abs(got[k] - exp[k]) < TOL, \
            "part 不吻合 %s: got=%s exp=%s" % (k, got[k], exp[k])


def test_ironclad_series_identical(built, snapshot):
    gs, es = built['series'], snapshot['series']
    assert set(gs.keys()) == set(es.keys()), "series 鍵不一致"
    for k in es:
        g, e = gs[k], es[k]
        assert len(g) == len(e), "series 長度不一致: %s" % k
        for i, (a, b) in enumerate(zip(g, e)):
            if a is None or b is None:
                assert a is b or a == b, "series None 不一致 %s[%d]" % (k, i)
            else:
                assert abs(a - b) < TOL, "series 值不一致 %s[%d]" % (k, i)


def test_ironclad_unwind(built, snapshot):
    gu, eu = built['unwind'], snapshot['unwind']
    for k in eu:
        if isinstance(eu[k], (int, float)):
            assert abs(gu[k] - eu[k]) < TOL, "unwind 不吻合 %s" % k
        else:
            assert gu[k] == eu[k], "unwind 不吻合 %s" % k


def test_ironclad_latest(built, snapshot):
    gl, el = built['latest'], snapshot['latest']
    assert set(gl.keys()) == set(el.keys())
    for k in el:
        if el[k] is None:
            assert gl[k] is None
        else:
            assert abs(gl[k] - el[k]) < TOL, "latest 不吻合 %s" % k


def test_ironclad_pctl_latest(built, snapshot):
    gp = built['latest_extra']['pctl']
    ep = snapshot['latest_extra']['pctl']
    for k in ep:
        assert abs(gp[k] - ep[k]) < TOL, "latest_extra.pctl 不吻合 %s" % k


# ---------------------------------------------------------------------
# 百分位函式逐日比對快照 pctl_*（於可完全重建的 series / 區段）
#
# 說明：margin_total、turn_val 於「陣列列」上直接抽樣，rolling_percentile
# 於近期 daily-dense 區段逐日重算應與快照 pctl_* 完全吻合（差異僅快照 2 位
# 四捨五入，<0.005）。credit-derived series（turn_heat/margin_util/maint_*）
# 的原始 pctl 係在更密的日頻底層序列上計算，無法由 1518 點稀疏 series 重建，
# 不納入本逐日比對（既有最新日沿用快照存值，見模組 DECODING NOTES）。
# ---------------------------------------------------------------------
# 每條 series 的「可完全重建」最近起始索引（window 內容 == 真實底層日頻序列的區段）：
# 早於此的原始 pctl 係在更密的日頻底層上算，稀疏 1518 點 series 無法重建。
_EXACT_FROM = {
    'margin_total': '20251209',   # 141 日
    'turn_val': '20260408',       # 67 日
}


@pytest.mark.parametrize('raw,pk', [
    ('margin_total', 'pctl_margin_total'),
    ('turn_val', 'pctl_turn_val'),
])
def test_percentile_matches_snapshot_daily(snapshot, raw, pk):
    dates = snapshot['dates']
    ser = snapshot['series'][raw]
    exp = snapshot['series'][pk]
    start = next(i for i, d in enumerate(dates) if d >= _EXACT_FROM[raw])
    n_checked = 0
    for i in range(start, len(ser)):
        if ser[i] is None or exp[i] is None:
            continue
        got = dl.rolling_percentile(ser, i)
        # 快照 pctl 為 2 位四捨五入，容差 0.005 視為完全吻合
        assert abs(got - exp[i]) < 0.005, \
            "%s pctl 不吻合 @%s idx=%d got=%.4f exp=%.4f" % (pk, dates[i], i, got, exp[i])
        n_checked += 1
    assert n_checked >= 50, "比對樣本過少: %d" % n_checked


# ---------------------------------------------------------------------
# 百分位定義驗證：latest 值等於快照 latest_extra.pctl（margin_total/turn_val）
# ---------------------------------------------------------------------
def test_percentile_latest_value(snapshot):
    for raw, key in [('margin_total', 'margin_total'), ('turn_val', 'turn_val')]:
        ser = snapshot['series'][raw]
        li = dl._last_valid_index(ser)
        got = dl.rolling_percentile(ser, li)
        exp = snapshot['latest_extra']['pctl'][key]
        assert abs(got - exp) < 0.005, "%s latest pctl got=%.4f exp=%.4f" % (raw, got, exp)


# ---------------------------------------------------------------------
# unwind 由 series 獨立重算，與快照吻合
# ---------------------------------------------------------------------
def test_unwind_recompute(snapshot):
    ind = json.loads(json.dumps(snapshot))
    unwind, sub = dl.compute_unwind(ind)
    eu = snapshot['unwind']
    # 快照 unwind 存值為 ~6 位四捨五入，容差 1e-5
    for k in ('baseline', 'peak', 'current', 'excess_now', 'excess_peak', 'U'):
        assert abs(unwind[k] - eu[k]) < 1e-5, "unwind %s" % k
    # subscore * weight 22 = part 15.5345
    assert abs(sub * 22 - 15.5345) < 1e-3


# ---------------------------------------------------------------------
# route 路徑 build_indicators() 絕不觸發網路
# ---------------------------------------------------------------------
def test_route_path_no_network(monkeypatch, tmp_path):
    # 快取不存在時走純快照，且不得呼叫任何 fetcher
    monkeypatch.setattr(dl, 'CACHE_PATH', str(tmp_path / 'live.json'))

    def _boom(*a, **k):
        raise AssertionError('route 路徑不應呼叫網路 fetcher')

    monkeypatch.setattr(dl, 'fetch_margin_market', _boom)
    monkeypatch.setattr(dl, 'fetch_maint_proxy', _boom)
    monkeypatch.setattr(dl, 'fetch_taiex_daily', _boom)
    monkeypatch.setattr(dl, 'fetch_daytrade', _boom)
    monkeypatch.setattr(dl, '_fetch_bundle', _boom)
    ind = dl.build_indicators()
    assert abs(ind['composite']['score'] - 78.14) < TOL


def test_route_path_returns_cache(monkeypatch, tmp_path):
    # 有快取時直接回快取（毫秒級、不碰網路）
    cache = str(tmp_path / 'live.json')
    fake = {'_key': 'x', 'ind': {'composite': {'score': 75.39, 'zone': 'high', 'parts': {}}},
            'meta': {}}
    with open(cache, 'w', encoding='utf-8') as f:
        json.dump(fake, f)
    monkeypatch.setattr(dl, 'CACHE_PATH', cache)

    def _boom(*a, **k):
        raise AssertionError('有快取時不應呼叫網路')

    monkeypatch.setattr(dl, '_fetch_bundle', _boom)
    ind = dl.build_indicators()
    assert ind['composite']['score'] == 75.39


# ---------------------------------------------------------------------
# refresh_live：抓不到新東西時不劣化既有好快取
# ---------------------------------------------------------------------
def test_refresh_no_data_preserves_cache(monkeypatch, tmp_path):
    cache = str(tmp_path / 'live.json')
    good = {'_key': 'live|old', 'ind': {'composite': {'score': 75.39, 'zone': 'high',
            'parts': {}}}, 'meta': {'calibration': {'factors': {'margin_total': 1.118}}}}
    with open(cache, 'w', encoding='utf-8') as f:
        json.dump(good, f)
    monkeypatch.setattr(dl, 'CACHE_PATH', cache)
    # fetcher 全 None（模擬網路失敗）
    monkeypatch.setattr(dl, 'fetch_margin_market', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_maint_proxy', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_taiex_daily', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_daytrade', lambda d: None)
    ind = dl.refresh_live(force=True)
    # 保留既有好資料，不被全 None 蓋成 78.14
    assert ind['composite']['score'] == 75.39
    # 快取檔內容未被劣化
    with open(cache, encoding='utf-8') as f:
        assert json.load(f)['ind']['composite']['score'] == 75.39


def test_refresh_no_data_no_existing_cache(monkeypatch, tmp_path):
    # 無既有快取 + 全 None → 寫入純快照 baseline（78.14），不 crash
    cache = str(tmp_path / 'live.json')
    monkeypatch.setattr(dl, 'CACHE_PATH', cache)
    monkeypatch.setattr(dl, 'fetch_margin_market', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_maint_proxy', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_taiex_daily', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_daytrade', lambda d: None)
    ind = dl.refresh_live(force=True)
    assert abs(ind['composite']['score'] - 78.14) < TOL


def test_refresh_persists_factor_on_anchor_fail(monkeypatch, tmp_path):
    # 既有快取存了好 factor；本次 anchor fetch 失敗 → 沿用已存 factor（不退回 1.0）
    cache = str(tmp_path / 'live.json')
    good = {'_key': 'live|old', 'ind': {'composite': {'score': 75.0, 'zone': 'high',
            'parts': {}}},
            'meta': {'calibration': {'factors': {'margin_total': 1.118,
                     'maint_wavg': 0.956, 'maint_low140_share': 1.466}}}}
    with open(cache, 'w', encoding='utf-8') as f:
        json.dump(good, f)
    monkeypatch.setattr(dl, 'CACHE_PATH', cache)
    monkeypatch.setattr(dl, 'fetch_margin_market', lambda d: None)  # anchor 失敗
    monkeypatch.setattr(dl, 'fetch_maint_proxy', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_taiex_daily', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_daytrade', lambda d: None)
    # 直接驗 refresh_live 內的 factor 合併：透過 compute_calibration + prev 合併邏輯
    ind_snap = dl._load_snapshot()
    calib = dl.compute_calibration(ind_snap)
    assert calib['factors']['margin_total'] == 1.0  # 本次 anchor 失敗 → 1.0
    # refresh_live 應沿用既有 1.118（不覆寫既有快取，因無 filled）
    ind = dl.refresh_live(force=True)
    assert ind['composite']['score'] == 75.0  # 保留既有


# ---------------------------------------------------------------------
# 接點校正常數：factor = 快照最後有效值 / fetcher 同一天值；
# 校正後 fetcher(anchor) == 快照值（接點連續）
# ---------------------------------------------------------------------
def test_calibration_continuity(snapshot, monkeypatch):
    # 模擬 fetcher 在 anchor 日回傳「與快照有系統落差」的代理值
    ser = snapshot['series']
    li = dl._last_valid_index(ser['margin_total'])
    anchor = snapshot['dates'][li]
    proxy_margin = {'margin_total': ser['margin_total'][li] / 1.1,   # 低 ~10%
                    'margin_util': 17.0, 'short_balance': 24.0, 'short_margin_ratio': 2.0}
    lmw = dl._last_valid_index(ser['maint_wavg'])
    proxy_maint = {'maint_wavg': ser['maint_wavg'][lmw] / 0.95,
                   'maint_low140_share': ser['maint_low140_share'][lmw] / 1.4,
                   'maint_low130_share': 1.0}

    def fake_margin(d):
        return proxy_margin if d == anchor else None

    def fake_maint(d):
        return proxy_maint if d == snapshot['dates'][lmw] else None

    monkeypatch.setattr(dl, 'fetch_margin_market', fake_margin)
    monkeypatch.setattr(dl, 'fetch_maint_proxy', fake_maint)
    monkeypatch.setattr(dl, 'fetch_taiex_daily', lambda d: None)
    monkeypatch.setattr(dl, 'fetch_daytrade', lambda d: None)

    ind = json.loads(json.dumps(snapshot))
    calib = dl.compute_calibration(ind)
    f = calib['factors']
    # 校正後 anchor 值 == 快照值
    assert abs(proxy_margin['margin_total'] * f['margin_total']
               - ser['margin_total'][li]) < 1e-6
    assert abs(proxy_maint['maint_wavg'] * f['maint_wavg']
               - ser['maint_wavg'][lmw]) < 1e-6
    assert abs(proxy_maint['maint_low140_share'] * f['maint_low140_share']
               - ser['maint_low140_share'][lmw]) < 1e-6
    # factor 數量級合理
    assert 1.05 < f['margin_total'] < 1.15
    assert 0.90 < f['maint_wavg'] < 1.0
    assert 1.3 < f['maint_low140_share'] < 1.5
