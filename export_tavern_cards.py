#!/usr/bin/env python3
"""把 world-companion 里的人物导出成诺拉·酒馆可导入的角色卡 + 关系世界书。

用法：
    python export_tavern_cards.py                      # 导出全部人物
    python export_tavern_cards.py --session 会话ID      # 只导出某个会话的人物
    python export_tavern_cards.py --out D:\某目录       # 指定输出目录

产物：
    <name>.json        角色卡（V2 格式，酒馆「角色卡库」直接导入）
    relations.json     关系网（lolibook 格式，进「世界书 / 设定」导入）

人物字段来自 memory.db 的 people 表，relations 表里的关系会写进 relations.json。
注意：导出前先把 people 表里 personality / speech_style / likes / dislikes 这类字段
补好，卡片才演得像 —— 只有名字和一句话的角色卡，模型演不出味道。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.db")

PEOPLE_COLS = (
    "name", "age", "description", "session_id",
    "birthday", "occupation", "personality", "speech_style", "likes", "dislikes",
)


def safe(text: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", str(text or "")).strip() or "unnamed"


def build_description(row: dict) -> str:
    parts = []
    if row.get("name"):
        parts.append(f"名称：{row['name']}")
    pairs = (
        ("年龄", "age"),
        ("生日", "birthday"),
        ("职业", "occupation"),
        ("简介", "description"),
        ("喜欢", "likes"),
        ("讨厌", "dislikes"),
    )
    for label, key in pairs:
        value = row.get(key)
        if value and str(value).strip() and str(value) != "0":
            parts.append(f"{label}：{value}")
    return "\n".join(parts)


def build_personality(row: dict) -> str:
    parts = []
    if row.get("personality"):
        parts.append(str(row["personality"]))
    if row.get("speech_style"):
        parts.append(f"说话方式：{row['speech_style']}")
    return "\n".join(parts)


def card_for(row: dict) -> dict:
    return {
        "name": row.get("name") or "未命名",
        "description": build_description(row),
        "personality": build_personality(row),
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
        "creator_notes": "",
        "creator": "world-companion",
        "character_version": "1.0",
        "avatar": "none",
        "chat": "",
        "talkativeness": "0.5",
        "fav": False,
        "tags": ["world-companion"],
        "system": "",
        "post_history_instructions": "",
        "alternate_greetings": [],
        "extensions": {
            "source": "world-companion",
            "session_id": row.get("session_id") or "",
            "exported_at": datetime.now().isoformat(timespec="seconds"),
        },
        "source": ["world-companion"],
    }


def lorebook(people: dict, relations: list) -> dict:
    entries = {}
    uid = 0
    for r in relations:
        a = people.get(r["person_a_id"], {})
        b = people.get(r["person_b_id"], {})
        an, bn = a.get("name", "?"), b.get("name", "?")
        lines = [f"{an} 和 {bn} 是{r['relation_type']}关系。"]
        if r.get("call_a_to_b"):
            lines.append(f"{an} 平时称呼 {bn} 为「{r['call_a_to_b']}」。")
        if r.get("call_b_to_a"):
            lines.append(f"{bn} 平时称呼 {an} 为「{r['call_b_to_a']}」。")
        entries[str(uid)] = {
            "uid": uid,
            "key": [an, bn],
            "keysecondary": [],
            "comment": "由 world-companion 导出的关系",
            "content": "".join(lines),
            "constant": True,          # 常驻设定：每次都进上下文
            "enabled": True,
            "order": 100 + uid,
            "position": "before_char",  # 放在角色设定之后、聊天之前
            "disable": False,
        }
        uid += 1
    return {"entries": entries}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="只导出指定会话的人物")
    ap.add_argument("--out", help="输出目录，默认 ./exports/tavern/<会话>")
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit(f"找不到数据库：{DB}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    sql = "SELECT rowid AS rid, * FROM people"
    params = []
    if args.session:
        sql += " WHERE session_id = ?"
        params.append(args.session)
    rows = [dict(r) for r in conn.execute(sql, params)]

    if not rows:
        sys.exit("没有找到人物。先在 world-companion 里添加人物吧。")

    people = {r["rid"]: r for r in rows}
    rel_sql = "SELECT * FROM relations"
    rel_params = []
    if args.session:
        rel_sql += " WHERE session_id = ?"
        rel_params.append(args.session)
    relations = [dict(r) for r in conn.execute(rel_sql, rel_params)]
    conn.close()

    out_dir = args.out or os.path.join(
        "exports", "tavern", args.session or "all", datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    os.makedirs(out_dir, exist_ok=True)

    for r in rows:
        card = card_for(r)
        path = os.path.join(out_dir, f"{safe(card['name'])}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(card, fh, ensure_ascii=False, indent=2)

    if relations:
        path = os.path.join(out_dir, "relations.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(lorebook(people, relations), fh, ensure_ascii=False, indent=2)

    print(f"导出 {len(rows)} 张角色卡，{len(relations)} 条关系到：{out_dir}")
    print("把 .json 拖进酒馆窗口即可导入；relations.json 走「世界书 / 设定」导入。")


if __name__ == "__main__":
    main()
