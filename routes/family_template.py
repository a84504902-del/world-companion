"""家庭/人物关系模板：把某个会话的人物+关系存成模板，新会话一键导入
存储在 family_templates.json（与 prompt_templates.json 同模式，按名字引用人物，
因为人物 id 是每会话独立的）"""
import json
import os
import time
from aiohttp import web

import db

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "family_templates.json")


def _load():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"templates": []}


def _save(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def list_family_templates(request):
    """模板列表（名称 + 人物/关系数量）"""
    data = _load()
    brief = [{"id": t["id"], "name": t["name"],
              "people_count": len(t["people"]), "relations_count": len(t["relations"])}
             for t in data["templates"]]
    return web.json_response({"templates": brief})


async def save_family_template(request):
    """把当前会话的人物+关系保存为模板（同名覆盖）"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    name = (body.get("name") or "").strip()
    session_id = body.get("session_id", "")
    if not name or not session_id:
        return web.json_response({"error": "缺少模板名或会话"}, status=400)

    people = db.get_people(session_id)
    relations = db.get_relations(session_id)
    if not people:
        return web.json_response({"error": "当前会话没有人物，没东西可存"}, status=400)

    id2name = {p["id"]: p["name"] for p in people}
    relations_by_name = []
    for r in relations:
        a, b = id2name.get(r["person_a_id"]), id2name.get(r["person_b_id"])
        if a and b:
            relations_by_name.append({"a": a, "b": b, "type": r["relation_type"]})

    template = {
        "id": f"fam_{int(time.time() * 1000)}",
        "name": name,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "people": [{"name": p["name"], "description": p.get("description", ""),
                    "age": p.get("age", 0), "birthday": p.get("birthday", ""),
                    "occupation": p.get("occupation", ""), "personality": p.get("personality", ""),
                    "speech_style": p.get("speech_style", ""), "likes": p.get("likes", ""),
                    "dislikes": p.get("dislikes", "")} for p in people],
        "relations": relations_by_name,
    }

    data = _load()
    data["templates"] = [t for t in data["templates"] if t["name"] != name]
    data["templates"].append(template)
    _save(data)
    return web.json_response({"ok": True, "people": len(people), "relations": len(relations_by_name)})


async def import_family_template(request):
    """把模板导入当前会话：人物按名字去重（已存在同名跳过），关系按三元组去重"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    name = (body.get("name") or "").strip()
    session_id = body.get("session_id", "")
    if not name or not session_id:
        return web.json_response({"error": "缺少模板名或会话"}, status=400)

    template = next((t for t in _load()["templates"] if t["name"] == name), None)
    if not template:
        return web.json_response({"error": f"模板不存在：{name}"}, status=404)

    existing_people = db.get_people(session_id)
    name2id = {p["name"]: p["id"] for p in existing_people}

    added_people = 0
    for p in template["people"]:
        if p["name"] in name2id:
            continue
        pid = db.add_person(p["name"], age=p.get("age", 0),
                            description=p.get("description", ""), session_id=session_id,
                            birthday=p.get("birthday", ""), occupation=p.get("occupation", ""),
                            personality=p.get("personality", ""), speech_style=p.get("speech_style", ""),
                            likes=p.get("likes", ""), dislikes=p.get("dislikes", ""))
        name2id[p["name"]] = pid
        added_people += 1

    existing_relations = db.get_relations(session_id)
    existing_set = {(r["person_a_name"], r["relation_type"], r["person_b_name"])
                    for r in existing_relations}

    added_relations = 0
    for r in template.get("relations", []):
        a_id, b_id = name2id.get(r["a"]), name2id.get(r["b"])
        if not a_id or not b_id:
            continue
        if (r["a"], r["type"], r["b"]) in existing_set:
            continue
        db.add_relation(a_id, r["type"], b_id, session_id=session_id,
                        call_a_to_b=r.get("call_a", ""), call_b_to_a=r.get("call_b", ""))
        added_relations += 1


def _apply_import_data(session_id, data):
    """应用一份 {people, relations} 配置到指定会话（add_family_template 和上传导入共用）"""
    name2id = {p["name"]: p["id"] for p in db.get_people(session_id)}
    added_people = 0
    for p in data.get("people", []):
        if not p.get("name") or p["name"] in name2id:
            continue
        name2id[p["name"]] = db.add_person(p["name"], age=p.get("age", 0),
                                           description=p.get("description", ""), session_id=session_id,
                                           birthday=p.get("birthday", ""), occupation=p.get("occupation", ""),
                                           personality=p.get("personality", ""), speech_style=p.get("speech_style", ""),
                                           likes=p.get("likes", ""), dislikes=p.get("dislikes", ""))
        added_people += 1
    existing = {(r["person_a_name"], r["relation_type"], r["person_b_name"])
                for r in db.get_relations(session_id)}
    added_relations = 0
    for r in data.get("relations", []):
        a_id, b_id = name2id.get(r.get("a")), name2id.get(r.get("b"))
        if not a_id or not b_id:
            continue
        if (r["a"], r["type"], r["b"]) in existing:
            continue
        db.add_relation(a_id, r["type"], b_id, session_id=session_id,
                        call_a_to_b=r.get("call_a", ""), call_b_to_a=r.get("call_b", ""))
        added_relations += 1
    return added_people, added_relations


async def export_current(request):
    """把当前会话的人物+关系导出为可下载的 JSON 配置文件"""
    session_id = request.query.get("session_id", "")
    if not session_id:
        return web.json_response({"error": "缺少会话"}, status=400)

    people = db.get_people(session_id)
    relations = db.get_relations(session_id)
    id2name = {p["id"]: p["name"] for p in people}
    payload = {
        "config": "family",
        "version": 1,
        "exported_at": time.strftime("%Y-%m-%d %H:%M"),
        "people": [{"name": p["name"], "description": p.get("description", ""),
                    "age": p.get("age", 0), "birthday": p.get("birthday", ""),
                    "occupation": p.get("occupation", ""), "personality": p.get("personality", ""),
                    "speech_style": p.get("speech_style", ""), "likes": p.get("likes", ""),
                    "dislikes": p.get("dislikes", "")} for p in people if p["name"] != "林风"],
        "relations": [{"a": r["person_a_name"], "b": r["person_b_name"], "type": r["relation_type"],
                       "call_a": r.get("call_a_to_b", ""), "call_b": r.get("call_b_to_a", "")}
                      for r in relations],
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return web.Response(
        body=body,
        content_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="family_config.json"'})


async def import_uploaded(request):
    """从上传的 JSON 配置文件导入（选择会话后应用）"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    session_id = body.get("session_id", "")
    data = body.get("data") or {}
    if not session_id or not isinstance(data, dict) or not data.get("people"):
        return web.json_response({"error": "配置文件格式不对（缺少 people）"}, status=400)

    added_people, added_relations = _apply_import_data(session_id, data)
    return web.json_response({"ok": True, "added_people": added_people,
                              "added_relations": added_relations})

    return web.json_response({"ok": True, "added_people": added_people,
                              "added_relations": added_relations,
                              "skipped_people": len(template["people"]) - added_people})


async def delete_family_template(request):
    """删除模板"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "缺少模板名"}, status=400)

    data = _load()
    before = len(data["templates"])
    data["templates"] = [t for t in data["templates"] if t["name"] != name]
    _save(data)
    return web.json_response({"ok": True, "deleted": before - len(data["templates"])})
