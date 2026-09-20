"""综合测试：找 BUG（临时脚本，测完删除）"""
import json
import urllib.request
import urllib.error
import sys
import io
import os
import time
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

import db
import loops
import timeflow
import scene

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


def req(path, payload=None, timeout=60):
    """返回 (status, json或文本)"""
    try:
        if payload is None:
            r = urllib.request.urlopen(BASE + path, timeout=timeout)
        else:
            rq = urllib.request.Request(BASE + path,
                                        data=json.dumps(payload).encode(),
                                        headers={'Content-Type': 'application/json'})
            r = urllib.request.urlopen(rq, timeout=timeout)
        body = r.read().decode('utf-8', 'ignore')
        try:
            return r.status, json.loads(body)
        except Exception:
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        return 0, str(e)


def chat(text, mode='deepseek', length_mode='normal'):
    """真实对话，返回 (正文, 是否含残留标记)"""
    rq = urllib.request.Request(BASE + '/chat',
                                data=json.dumps({'text': text, 'mode': mode,
                                                 'length_mode': length_mode}).encode(),
                                headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(rq, timeout=240) as r:
        raw = r.read().decode('utf-8', 'ignore')
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
    return content


def latest_session():
    row = db.connect().execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone()
    return row['id'] if row else None


print('=' * 64)
print('【1】时间系统 · 饱和函数')
check('离开 0 小时 -> 0', timeflow.world_elapsed(0) == 0)
check('负数 -> 0', timeflow.world_elapsed(-10) == 0)
check('极大值不超上限',
      timeflow.world_elapsed(999999) <= timeflow.W_MAX_HOURS + 0.01,
      f"得到 {timeflow.world_elapsed(999999)}")
check('单调递增',
      timeflow.world_elapsed(1) < timeflow.world_elapsed(10) < timeflow.world_elapsed(100))
check('短时间有加速（1h 现实 > 1h 剧情）',
      timeflow.world_elapsed(1) > 1, f"得到 {timeflow.world_elapsed(1)}")

print()
print('=' * 64)
print('【2】承诺引擎 · 预判')
check('承诺句命中', loops.looks_like_promise('我明天带你去吃火锅'))
check('"周末"命中', loops.looks_like_promise('周末去看电影'))
check('普通句不命中', not loops.looks_like_promise('今天天气不错'))
check('太短不命中', not loops.looks_like_promise('好'))
check('空字符串不崩', not loops.looks_like_promise(''))
check('None 不崩', not loops.looks_like_promise(None))

print()
print('【2b】承诺引擎 · 时间词解析')
check('"明天" -> 有时间', loops._resolve_due('明天') is not None)
check('"周末" -> 有时间', loops._resolve_due('周末') is not None)
check('"下次" -> 有时间', loops._resolve_due('下次') is not None)
check('"无" -> None', loops._resolve_due('无') is None)
check('乱码 -> None', loops._resolve_due('???') is None)
check('空 -> None', loops._resolve_due('') is None)

print()
print('=' * 64)
print('【3】场景提取 · 值过滤')
check('"未知"被过滤', scene._clean('未知') == '')
check('"不清楚"被过滤', scene._clean('不清楚') == '')
check('正常值保留', scene._clean('家') == '家')
check('None -> 空串', scene._clean(None) == '')
check('超长截断', len(scene._clean('x' * 100)) <= 20)

print()
print('=' * 64)
print('【4】数据库 · 空/不存在的数据')
check('空 session 承诺列表不崩', db.get_open_loops('') == [])
check('不存在的 session 承诺列表', db.get_open_loops('__nope__') == [])
check('不存在的 session 时钟为 None', db.get_story_clock('__nope__') is None)
check('不存在的 session 场景为 None', db.get_scene('__nope__') is None)
try:
    n = loops.cleanup_stale_loops('__nope__')
    check('清理不存在的 session 不崩', True)
except Exception as e:
    check('清理不存在的 session 不崩', False, str(e))

print()
print('=' * 64)
print('【5】场景提取 · 边界')
try:
    r = scene.extract_scene('__nope__', lambda m: '{"place":"x"}')
    check('无消息时返回 None', r is None)
except Exception as e:
    check('无消息时返回 None', False, str(e))

try:
    scene.extract_scene('__nope__', lambda m: 'not json at all')
    check('LLM 返回非 JSON 不崩', True)
except Exception as e:
    check('LLM 返回非 JSON 不崩', False, str(e))

print()
print('=' * 64)
print('【6】API · 参数校验与异常')
st, d = req('/api/status?session_id=__nope__')
check('/api/status 不存在的会话仍 200', st == 200, f'status={st}')
check('/api/status 返回 time 字段', isinstance(d, dict) and 'time' in d)

st, d = req('/api/loops/list?session_id=__nope__')
check('/api/loops/list 空会话 200', st == 200)
check('返回 loops 为数组', isinstance(d, dict) and isinstance(d.get('loops'), list))

st, d = req('/api/loops/add', {'content': '', 'session_id': '__nope__'})
check('/api/loops/add 空内容被拒 400', st == 400, f'status={st}')

st, d = req('/api/loops/add', {'content': '测试承诺', 'weight': 999, 'session_id': '__nope__'})
check('/api/loops/add 权重超界被夹紧', st == 200, f'status={st}')
if st == 200:
    lid = d.get('id')
    st2, d2 = req('/api/loops/list?session_id=__nope__')
    w = d2['loops'][0]['weight'] if d2.get('loops') else None
    check('权重被夹到 5', w == 5, f'weight={w}')
else:
    lid = None

st, d = req('/api/loops/add', {'content': '负权重测试', 'weight': -5, 'session_id': '__nope__'})
st2, d2 = req('/api/loops/list?session_id=__nope__')
ws = [x['weight'] for x in d2.get('loops', [])]
check('负权重被夹到 1', 1 in ws, f'weights={ws}')

st, d = req('/api/loops/close', {'id': 999999, 'status': 'fulfilled'})
check('/api/loops/close 不存在的 id 不崩', st == 200, f'status={st}')

st, d = req('/api/loops/close', {'id': 1, 'status': 'bogus'})
check('/api/loops/close 非法状态被拒 400', st == 400, f'status={st}')

st, d = req('/api/loops/delete', {'id': 999999})
check('/api/loops/delete 不存在的 id 不崩', st == 200, f'status={st}')

st, d = req('/api/loops/update', {'id': 999999, 'weight': 3})
check('/api/loops/update 不存在的 id 不崩', st == 200, f'status={st}')

# 清理测试承诺
conn = db.connect()
conn.execute("DELETE FROM open_loops WHERE session_id = '__nope__'")
conn.commit()

print()
print('=' * 64)
print(f'阶段小结: 通过 {PASS} 项, 失败 {len(FAIL)} 项')
if FAIL:
    for f in FAIL:
        print('   -', f)
