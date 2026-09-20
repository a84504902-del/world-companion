"""关系管理路由"""
from aiohttp import web
import db


async def list_relations(request):
    """获取关系列表"""
    session_id = request.query.get("session_id", "")
    relations = db.get_relations(session_id)
    return web.json_response({"relations": relations})


async def add_relation(request):
    """添加关系"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    person_a_id = data.get("person_a_id")
    relation_type = data.get("relation_type", "").strip()
    person_b_id = data.get("person_b_id")
    session_id = data.get("session_id", "")

    if not person_a_id or not person_b_id or not relation_type:
        return web.json_response({"error": "参数不完整"}, status=400)

    relation_id = db.add_relation(person_a_id, relation_type, person_b_id, session_id)
    return web.json_response({"id": relation_id})


async def update_relation(request):
    """更新关系"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    relation_id = data.get("id")
    relation_type = data.get("relation_type", "").strip()

    if not relation_id or not relation_type:
        return web.json_response({"error": "参数不完整"}, status=400)

    db.update_relation(relation_id, relation_type)
    return web.json_response({"ok": True})


async def delete_relation(request):
    """删除关系"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    relation_id = data.get("id")
    if not relation_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    db.delete_relation(relation_id)
    return web.json_response({"ok": True})
