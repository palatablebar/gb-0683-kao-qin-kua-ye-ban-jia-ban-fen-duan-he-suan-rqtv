import pytest
from copy import deepcopy
from decimal import Decimal
from app.workflow import run, load
from app import store
from app.modules.m5_calculate import overlap
from app.modules.m5_calculate.overlap import adjudicate
from app.modules.m7_archive.export_out import bundle

@pytest.fixture
def overlap_raw(): return load('overlap.csv')

@pytest.fixture
def overlap_state(overlap_raw): return run(overlap_raw, {})

def _shift(shift_id, day, version, at, start='22:00', end='06:00'):
    row = {'id': shift_id.replace('/', '-'), 'employee': shift_id.split('/')[0], 'shift_day': day,
           'version': str(version), 'punch': 'IN', 'at': at, 'shift_start': start, 'shift_end': end,
           'break_start': '02:00', 'break_end': '03:00', 'hourly_rate': '20', 'paid': '0', '_line': 1, '_raw': {}}
    return {'id': shift_id, 'employee': row['employee'], 'day': day,
            'in': row, 'out': dict(row, punch='OUT'), 'sources': [row]}

def _seg(shift_id, start, end, minutes):
    return {'id': shift_id, 'employee': shift_id.split('/')[0], 'shift_day': shift_id.split('/')[1],
            'calendar_day': start[:10], 'start': start, 'end': end, 'minutes': minutes, 'rate': '20', 'line': 1}

# 裁决：重叠分钟在可付工时中只有一个归属
def test_overlap_minutes_paid_exactly_once(overlap_state):
    assert overlap_state['adjudication']['minutes'] == {'raw': 1920, 'payable': 1590, 'deducted': 330}
    assert overlap_state['business']['minutes'] == 1590

def test_higher_version_keeps_overlap(overlap_state):
    e100 = [d for d in overlap_state['deductions'] if d['employee'] == 'E100']
    assert [(d['start'][11:16], d['end'][11:16], d['minutes']) for d in e100] == [('00:00', '02:00', 120), ('03:00', '04:00', 60), ('04:30', '06:00', 90)]
    assert all(d['shift'] == 'E100/2026-10-01' and d['against'] == 'E100/2026-10-02' for d in e100)

def test_same_version_settled_by_earlier_actual(overlap_state):
    e101 = [d for d in overlap_state['deductions'] if d['employee'] == 'E101']
    assert len(e101) == 1 and e101[0]['minutes'] == 60
    assert e101[0]['shift'] == 'E101/2026-10-02' and e101[0]['against'] == 'E101/2026-10-01'

def test_same_version_settled_by_planned_coverage():
    x = _shift('EC/2026-10-01', '2026-10-01', 1, '2026-10-01T14:00:00+00:00')
    y = _shift('EC/2026-10-02', '2026-10-02', 1, '2026-10-01T12:00:00+00:00', start='20:00', end='04:00')
    segs = [_seg('EC/2026-10-01', '2026-10-01T23:00:00+08:00', '2026-10-02T01:00:00+08:00', 120),
            _seg('EC/2026-10-02', '2026-10-01T23:00:00+08:00', '2026-10-02T01:00:00+08:00', 120)]
    out = adjudicate({'segments': segs, 'shifts': [x, y]})
    # y 到岗更早，但时段落在 x 计划班时内，归 x
    assert [p['id'] for p in out['payable']] == ['EC/2026-10-01']
    assert out['deductions'][0]['shift'] == 'EC/2026-10-02' and out['deductions'][0]['against'] == 'EC/2026-10-01'

def test_same_version_same_actual_settled_by_id():
    x = _shift('EA/2026-10-02', '2026-10-02', 1, '2026-10-01T12:00:00+00:00')
    y = _shift('EA/2026-10-03', '2026-10-03', 1, '2026-10-01T12:00:00+00:00')
    segs = [_seg('EA/2026-10-02', '2026-10-01T23:00:00+08:00', '2026-10-02T01:00:00+08:00', 120),
            _seg('EA/2026-10-03', '2026-10-01T23:00:00+08:00', '2026-10-02T01:00:00+08:00', 120)]
    out = adjudicate({'segments': segs, 'shifts': [x, y]})
    assert [p['id'] for p in out['payable']] == ['EA/2026-10-02']
    assert out['deductions'][0]['against'] == 'EA/2026-10-02'

# 整段退出核算时，扣回证据必须解释它对应哪两条班次
def test_fully_overlapped_shift_stays_explained():
    x = _shift('EX/2026-10-01', '2026-10-01', 1, '2026-10-01T14:00:00+00:00')
    y = _shift('EX/2026-10-02', '2026-10-02', 2, '2026-10-01T14:00:00+00:00')
    segs = [_seg('EX/2026-10-01', '2026-10-01T22:00:00+08:00', '2026-10-02T00:00:00+08:00', 120),
            _seg('EX/2026-10-01', '2026-10-02T00:00:00+08:00', '2026-10-02T02:00:00+08:00', 120),
            _seg('EX/2026-10-02', '2026-10-01T22:00:00+08:00', '2026-10-02T00:00:00+08:00', 120),
            _seg('EX/2026-10-02', '2026-10-02T00:00:00+08:00', '2026-10-02T02:00:00+08:00', 120)]
    out = adjudicate({'segments': segs, 'shifts': [x, y]})
    assert [p for p in out['payable'] if p['id'] == 'EX/2026-10-01'] == []
    assert sum(d['minutes'] for d in out['deductions']) == 240
    assert all(d['shift'] == 'EX/2026-10-01' and d['against'] == 'EX/2026-10-02' for d in out['deductions'])
    assert out['adjudication']['minutes'] == {'raw': 480, 'payable': 240, 'deducted': 240}
    assert len(out['shifts']) == 2

# 考勤记录与工资差额一致
def test_attendance_and_wage_difference_consistent(overlap_state):
    biz = overlap_state['business']
    assert biz['total'] == '770.00' and biz['paid'] == '720' and biz['difference'] == '50.00'
    assert sum(Decimal(c['amount']) for c in overlap_state['calculations']) == Decimal('770.00')
    attendance = overlap_state['reconcile']['attendance']
    assert attendance['consistent'] and overlap_state['reconcile']['balanced']
    assert attendance['raw'] == attendance['payable'] + attendance['deducted']

def test_no_overlap_no_deduction():
    s = run(load('clean.csv'), {})
    assert s['deductions'] == [] and s['adjudication']['minutes']['deducted'] == 0
    assert s['reconcile']['attendance']['consistent'] and s['reconcile']['balanced']

# 重新计算保留班次与补卡原件
def test_recount_preserves_shift_and_punch_originals(overlap_state):
    assert len(overlap_state['shifts']) == 5 and len(overlap_state['rows']) == 10
    lines = {r['_line'] for r in overlap_state['rows']}
    assert all(d['line'] in lines and d['against_line'] in lines for d in overlap_state['deductions'])
    assert sum(s['minutes'] for s in overlap_state['segments']) == 1920

def test_adjudicate_does_not_mutate_inputs(overlap_raw):
    state = run(overlap_raw, {}, 4)
    snapshot = deepcopy([state['segments'], state['shifts'], state['rows']])
    adjudicate(state)
    assert [state['segments'], state['shifts'], state['rows']] == snapshot

def test_adjudication_is_deterministic(overlap_raw):
    a = adjudicate(run(overlap_raw, {}, 4))
    b = adjudicate(run(overlap_raw, {}, 4))
    assert a['payable'] == b['payable'] and a['deductions'] == b['deductions']

# 重试同一输入不得新增已扣工时，也不得覆盖已存批次
def test_retry_same_input_adds_no_deduction(client):
    a = client.post('/api/runs', json={'sample': 'overlap.csv'}).get_json()
    b = client.post('/api/runs', json={'sample': 'overlap.csv'}).get_json()
    assert a['run_id'] == b['run_id']
    again = client.get('/api/runs/' + a['run_id']).get_json()
    assert again['deductions'] == a['deductions']
    assert again['adjudication']['minutes']['deducted'] == 330
    ids = [h['id'] for h in client.get('/api/runs').get_json()]
    assert ids.count(a['run_id']) == 1

def test_retry_never_overwrites_stored_basis(overlap_state, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(overlap_state['run_id'], overlap_state)
    store.save(overlap_state['run_id'], dict(overlap_state, deductions=[]))
    assert len(store.get(overlap_state['run_id'])['deductions']) == 4

# 归属规则变化后旧结果作废
def test_rule_change_invalidates_previous_results(client, monkeypatch):
    a = client.post('/api/runs', json={'sample': 'overlap.csv'}).get_json()
    monkeypatch.setattr(overlap, 'OVERLAP_RULE', 'overlap-test-v9')
    b = client.post('/api/runs', json={'sample': 'overlap.csv'}).get_json()
    assert a['run_id'] != b['run_id']
    assert client.get('/api/runs').get_json()[0]['id'] == b['run_id']
    old = client.get('/api/runs/' + a['run_id']).get_json()
    assert old['adjudication']['rule'] == 'overlap-2026.11.19-v1'
    assert b['adjudication']['rule'] == 'overlap-test-v9'

# 切换失败时保留旧版，新结果不得覆盖可付款依据
def test_failed_recount_keeps_payable_basis(client):
    good = client.post('/api/runs', json={'sample': 'overlap.csv'}).get_json()
    bad = client.post('/api/runs', json={'csv': 'wrong\n1\n'})
    assert bad.status_code == 422
    assert client.get('/api/runs').get_json()[0]['id'] == good['run_id']
    export = client.get('/api/archive/' + good['run_id'] + '/export.csv')
    assert export.status_code == 200 and '扣回' in export.data.decode('utf-8-sig')

def test_failed_save_keeps_previous_latest(overlap_state, tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'r.db'))
    store.save(overlap_state['run_id'], overlap_state)
    with pytest.raises(TypeError):
        store.save('broken', {'unserializable': object()})
    assert store.history()[0]['id'] == overlap_state['run_id']

# 页面和导出只呈现可付工时与证据
def test_export_lists_clawback_evidence(overlap_state):
    text = bundle(overlap_state)
    assert text.count('扣回') == len(overlap_state['deductions'])
    assert 'E100/2026-10-01' in text and 'E100/2026-10-02' in text
    assert overlap_state['archive']['evidence_count'] == len(overlap_state['calculations']) + len(overlap_state['deductions']) + len(overlap_state['quarantine'])

def test_page_shows_payable_and_evidence(client):
    client.post('/api/runs', json={'sample': 'overlap.csv'})
    html = client.get('/m5_calculate').data.decode()
    assert '扣回证据' in html and 'E100/2026-10-02' in html and '可付分钟' in html
    reconcile = client.get('/m6_reconcile').data.decode()
    assert '扣回分钟' in reconcile and '相符' in reconcile
