from decimal import Decimal

def balance(state):
    accepted=len(state['rows']);isolated=len(state['quarantine'])
    accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    minutes=state.get('adjudication',{}).get('minutes',{})
    raw=minutes.get('raw',0);payable=minutes.get('payable',0);deducted=minutes.get('deducted',0)
    # 考勤与工资一致：每一原始分钟要么进入可付、要么被扣回；计薪分钟必须等于可付分钟。
    attendance={'raw':raw,'payable':payable,'deducted':deducted,
        'consistent':raw==payable+deducted and state.get('business',{}).get('minutes')==payable}
    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,
        'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],
        'accounted':accounted,'attendance':attendance,
        'balanced':accounted==state['input_count'] and attendance['consistent'],
        'business':state.get('business',{})}
