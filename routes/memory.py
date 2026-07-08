"""记忆管理路由"""
from aiohttp import web
import db
import memory_retriever


async def list_memories(request):
    """获取记忆列表"""
    tag = request.query.get("tag")
    session_id = request.query.get("session_id")
    memories = db.load_memories(tag=tag, session_id=session_id)
    return web.json_response({"memories": memories})


async def save_memory(request):
    """保存记忆"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    content = data.get("content", "").strip()
    tags = data.get("tags", "")
    session_id = data.get("session_id", "")

    if not content:
        return web.json_response({"error": "内容不能为空"}, status=400)

    memory_retriever.store_memory_with_embedding(content, tags=tags, session_id=session_id)
    return web.json_response({"ok": True})


async def delete_memory(request):
    """删除记忆"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    memory_id = data.get("id")
    if not memory_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    db.delete_memory(memory_id)
    return web.json_response({"ok": True})


async def save_from_message(request):
    """从聊天消息保存为记忆"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    content = data.get("content", "").strip()
    session_id = data.get("session_id", "")
    tags = data.get("tags", "手动标记")

    if not content:
        return web.json_response({"error": "内容不能为空"}, status=400)

    memory_retriever.store_memory_with_embedding(content, tags=tags, session_id=session_id)
    return web.json_response({"ok": True})


async def update_weight(request):
    """更新记忆权重"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    memory_id = data.get("id")
    weight = data.get("weight", 1.0)
    if not memory_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    db.update_memory_weight(memory_id, weight)
    return web.json_response({"ok": True})
