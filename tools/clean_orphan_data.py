"""一次性清理脚本：删除所有"会话已不存在"的孤儿数据（2026-10-13 修复遗留）

背景：delete_session 以前漏清 open_loops / scene_states 等表，
更早版本还漏过 people/relations/summaries，导致库里积了 16 行孤儿。
本脚本按当前 sessions 表的 id 扫描所有带 session_id 列的表，清掉对不上的行。

用法：.venv/Scripts/python.exe tools/clean_orphan_data.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH  # noqa: E402


def main():
    conn = sqlite3.connect(DB_PATH)
    live = {r[0] for r in conn.execute("SELECT id FROM sessions")}
    print(f"存活会话 {len(live)} 个")

    # 找出所有带 session_id 列的表
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    total = 0
    for t in tables:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
        if "session_id" not in cols:
            continue
        rows = conn.execute(f'SELECT session_id FROM "{t}"').fetchall()
        orphans = {r[0] for r in rows if r[0] not in live}
        if orphans:
            placeholders = ",".join("?" * len(orphans))
            cur = conn.execute(
                f'DELETE FROM "{t}" WHERE session_id IN ({placeholders})',
                tuple(orphans))
            conn.commit()
            n = sum(1 for r in rows if r[0] not in live)
            total += n
            print(f"  {t}: 清除 {n} 行孤儿（会话 {', '.join(sorted(orphans))}）")

    # 孤儿会话的消息表（msg_xxx）也一并清掉
    msg_tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'msg_%'")]
    for mt in msg_tables:
        sid = mt[4:]  # 去掉 msg_ 前缀
        if sid not in live:
            conn.execute(f'DROP TABLE IF EXISTS "{mt}"')
            conn.commit()
            total += 1
            print(f"  {mt}: 已删除（会话 {sid} 不存在）")

    print(f"\n完成：共清理 {total} 项孤儿")
    conn.close()


if __name__ == "__main__":
    main()
