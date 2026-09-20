"""人物管理路由"""
from aiohttp import web
import db


async def list_people(request):
    """获取人物列表"""
    session_id = request.query.get("session_id", "")
    people = db.get_people(session_id)
    return web.json_response({"people": people})


async def add_person(request):
    """添加人物"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    name = data.get("name", "").strip()
    session_id = data.get("session_id", "")

    if not name:
        return web.json_response({"error": "名字不能为空"}, status=400)

    person_id = db.add_person(
        name, age=data.get("age", 0), description=data.get("description", ""),
        session_id=session_id,
        birthday=data.get("birthday", ""), occupation=data.get("occupation", ""),
        personality=data.get("personality", ""), speech_style=data.get("speech_style", ""),
        likes=data.get("likes", ""), dislikes=data.get("dislikes", ""))
    return web.json_response({"id": person_id})


async def update_person(request):
    """更新人物（人物卡：只更新传入的字段）"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    person_id = data.get("id")
    if not person_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    db.update_person(
        person_id,
        name=data.get("name"), age=data.get("age"), description=data.get("description"),
        birthday=data.get("birthday"), occupation=data.get("occupation"),
        personality=data.get("personality"), speech_style=data.get("speech_style"),
        likes=data.get("likes"), dislikes=data.get("dislikes"))
    return web.json_response({"ok": True})


async def delete_person(request):
    """删除人物"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    person_id = data.get("id")
    if not person_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    db.delete_person(person_id)
    return web.json_response({"ok": True})
