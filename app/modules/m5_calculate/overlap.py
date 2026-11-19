from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from app.modules.m3_versions.rules import schedule

# 归属裁决规则版本。规则一旦变化，旧核算结果必须作废：
# 该常量经 app.manifest.identity 计入批次身份，改动即产生新批次。
OVERLAP_RULE = 'overlap-2026.11.19-v1'

def _meta(shifts):
    # 每条班次的裁决依据：补卡/调整版本、计划班时窗口、实际到岗时刻。
    out = {}
    for s in shifts:
        if s.get('in') is None or s.get('out') is None:
            continue
        planned_start, planned_end, _, _ = schedule(s['in'])
        out[s['id']] = {
            'version': max(Decimal(str(s['in']['version'])), Decimal(str(s['out']['version']))),
            'planned': (planned_start, planned_end),
            'actual': datetime.fromisoformat(s['in']['at']),
        }
    return out

def _winner(ids, meta, a, b):
    # 同一时段只归属一条班次：版本高者胜；计划覆盖者胜；到岗早者胜；ID小者胜。
    def key(shift_id):
        m = meta[shift_id]
        covers = m['planned'][0] <= a and b <= m['planned'][1]
        return (-m['version'], 0 if covers else 1, m['actual'], shift_id)
    return min(ids, key=key)

def _merge(spans):
    out = []
    for a, b in sorted(spans):
        if out and out[-1][1] == a:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]

def adjudicate(state):
    # 纯函数式裁决：不改写 rows/shifts/segments 原件，只新增 payable/deductions/adjudication。
    meta = _meta(state.get('shifts', []))
    by_employee = defaultdict(list)
    for seg in state['segments']:
        item = dict(seg)
        item['_a'] = datetime.fromisoformat(seg['start'])
        item['_b'] = datetime.fromisoformat(seg['end'])
        by_employee[seg['employee']].append(item)
    payable = []
    deductions = []
    for employee in sorted(by_employee):
        segs = sorted(by_employee[employee], key=lambda s: (s['_a'], s['id']))
        points = sorted({s['_a'] for s in segs} | {s['_b'] for s in segs})
        won = defaultdict(list)   # 片段序号 -> 归属该班次的子时段
        lost = defaultdict(list)  # (片段序号, 归属班次, 归属来源行) -> 被扣回的子时段
        for a, b in zip(points, points[1:]):
            covering = [i for i, s in enumerate(segs) if s['_a'] <= a and s['_b'] >= b]
            if not covering:
                continue
            if len(covering) == 1:
                won[covering[0]].append((a, b))
                continue
            keep = _winner([segs[i]['id'] for i in covering], meta, a, b)
            keeper = next(i for i in covering if segs[i]['id'] == keep)
            won[keeper].append((a, b))
            for i in covering:
                if i != keeper:
                    lost[(i, segs[keeper]['id'], segs[keeper]['line'])].append((a, b))
        for i, s in enumerate(segs):
            for a, b in _merge(won.get(i, [])):
                piece = {k: v for k, v in s.items() if not k.startswith('_')}
                piece['start'] = a.isoformat()
                piece['end'] = b.isoformat()
                piece['minutes'] = int((b - a).total_seconds() // 60)
                payable.append(piece)
        for (i, keep, keep_line), spans in lost.items():
            s = segs[i]
            for a, b in _merge(spans):
                deductions.append({
                    'employee': employee, 'shift': s['id'], 'against': keep,
                    'shift_day': s['shift_day'], 'start': a.isoformat(), 'end': b.isoformat(),
                    'minutes': int((b - a).total_seconds() // 60),
                    'line': s['line'], 'against_line': keep_line, 'rule': OVERLAP_RULE,
                })
    payable.sort(key=lambda p: (p['employee'], p['start'], p['id']))
    deductions.sort(key=lambda d: (d['employee'], d['start'], d['shift'], d['against']))
    raw = sum(s['minutes'] for s in state['segments'])
    paid = sum(p['minutes'] for p in payable)
    deducted = sum(d['minutes'] for d in deductions)
    state['payable'] = payable
    state['deductions'] = deductions
    state['adjudication'] = {'rule': OVERLAP_RULE, 'minutes': {'raw': raw, 'payable': paid, 'deducted': deducted}}
    return state
