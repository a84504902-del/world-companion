"""第二轮：真实对话准确性 + 边界压力（临时脚本）"""
import json
import urllib.request
import urllib.error
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
import db

BASE = 'http://localhost:8877'
PASS = 0
FAIL = []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  OK   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name}  {detail}')


def post(path, payload, timeout=240):
    try:
        rq = urllib.request.Request(BASE + path,
                                    data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json'})
        r = urllib.request.urlopen(rq, timeout=timeout)
        return r.status, r.read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'ignore')
    except Exception as e:
        return 0, str(e)


def chat(text):
    st, raw = post('/chat', {'text': text, 'mode': 'deepseek'})
    content = ''
    for line in raw.split('\n'):
        if not line.startswith('data: '):
            continue
        try:
            d = json.loads(line[6:])
        except Exception:
            continue
        if d.get('content'):
            content += d['content']
    return st, content


def latest_session():
    row = db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()
    return row['id'] if row else None


def loops_of(sid):
    return db.get_all_open_loops(sid)


print('=' * 64)
print('【A】特殊输入压力测试')
post('/new_chat', {})
sid = latest_session()
print('  测试会话:', sid)

# A1 空消息
st, _ = post('/chat', {'text': '   ', 'mode': 'deepseek'})
check('空消息被拒 400', st == 400, f'status={st}')

# A2 超长消息
long_text = '你好啊' * 1500   # 4500 字
st, reply = chat(long_text)
check('4500 字长消息不崩', st == 200 and len(reply) > 0, f'status={st}')

# A3 只有 emoji
st, reply = chat('🙂🙂🙂')
check('纯 emoji 不崩', st == 200 and len(reply) > 0, f'status={st}')

# A4 特殊字符（XSS 尝试）
st, reply = chat('<script>alert(1)</script>')
check('HTML 标签不崩', st == 200, f'status={st}')
check('回复里没把标签当指令执行', 'alert(1)' not in reply or len(reply) < 2000)

# A5 换行 + 引号
st, reply = chat('他说："你好"\n\n然后走了')
check('换行引号不崩', st == 200 and len(reply) > 0, f'status={st}')

print()
print('=' * 64)
print('【B】承诺抽取准确性（关键：误抽 / 漏抽）')

cases = [
    ('我明天带你去吃那家新开的火锅', True, '明确承诺'),
    ('今天天气挺不错的', False, '闲聊'),
    ('我明天下午要开会，可能很晚', False, '跟她无关的事'),
    ('行吧', False, '敷衍'),
    ('周末我一定陪你去海边看日出', True, '高权重承诺'),
    ('嗯嗯知道了', False, '敷衍'),
]

for text, should, label in cases:
    before = len(loops_of(sid))
    st, reply = chat(text)
    time.sleep(7)   # 等后台抽取
    after = loops_of(sid)
    got = len(after) > before
    mark = 'OK  ' if got == should else 'FAIL'
    if got == should:
        PASS += 1
    else:
        FAIL.append(f'{label}: {text}')
    newone = after[0]['content'] if after else ''
    print(f'  {mark} [{label}] "{text[:22]}"')
    print(f'       期望抽取={should} 实际={got}  最新记录: {newone[:40]}')

print()
print('=' * 64)
print('【C】多轮对话稳定性（连续 8 轮）')
msgs = [
    '我出门了', '到公司了', '中午吃了个饭', '下午好忙啊',
    '终于下班', '在路上了', '到家了', '洗完澡了',
]
errs = 0
for m in msgs:
    st, reply = chat(m)
    if st != 200 or not reply:
        errs += 1
        print(f'  FAIL 第 {msgs.index(m)+1} 轮异常: status={st}')
check('8 轮连续对话无异常', errs == 0, f'{errs} 轮失败')

time.sleep(8)
sc = db.get_scene(sid)
print('       最终场景:', sc and {k: sc[k] for k in ('place', 'her_doing', 'user_place')})
check('场景被成功更新（非空）', bool(sc and sc.get('place')))

print()
print('=' * 64)
print('【D】承诺注入 + 她是否主动提起')
# 造一条已过期的承诺，看下一轮她提不提
al = loops_of(sid)
if al:
    lid = al[0]['id']
    conn = db.connect()
    conn.execute('UPDATE open_loops SET due_at=?, weight=4, last_reminded_at=NULL, reminded_count=0 WHERE id=?',
                 ('2026-09-01 23:59:00', lid))
    conn.commit()
    st, reply = chat('我回来了')
    hit = ('火锅' in reply) or ('海边' in reply) or ('答应' in reply) or ('说好' in reply)
    check('过期承诺被她主动提起', hit, f'回复: {reply[:80]}')
    print('       她的回复:', reply[:120].replace('\n', ' '))

print()
print('=' * 64)
print('【E】数据一致性')
ai_msgs = db.load_messages(sid)
check('消息都有时间戳', all(m.get('timestamp') for m in ai_msgs),
      f'缺失 {sum(1 for m in ai_msgs if not m.get("timestamp"))} 条')
bad_ts = [m for m in ai_msgs if m.get('timestamp', '') and not m['timestamp'].startswith('2026')]
check('时间戳年份正确', not bad_ts, f'{len(bad_ts)} 条异常')

print()
print('=' * 64)
print(f'阶段小结: 通过 {PASS} 项, 失败 {len(FAIL)} 项')
for f in FAIL:
    print('   -', f)

# 清理
print()
print('清理测试会话:', sid)
conn = db.connect()
try:
    db.drop_message_table(sid)
except Exception:
    pass
for t in ('session_states', 'open_loops', 'conversation_chunks', 'scene_states'):
    conn.execute(f'DELETE FROM {t} WHERE session_id = ?', (sid,))
conn.execute('DELETE FROM sessions WHERE id = ?', (sid,))
conn.commit()
print('剩余会话:', conn.execute('SELECT COUNT(*) c FROM sessions').fetchone()['c'])
