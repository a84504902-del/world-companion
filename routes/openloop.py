"""未完结事件（她在等的事）路由

对应核心引擎 loops.py / db.open_loops
"""
from aiohttp import web

import db

_VALID_STATUS = ("fulfilled", "expired", "dropped")


async def status(request):
    """顶部状态条用：当前时间 + 她还在等什么。

    只读，不推进剧情时间（避免刷新页面就把剧情时间刷走）。
    """
    session_id = request.query.get("session_id", "")

    info = {}
    try:
        import timeflow
        info = timeflow.peek(session_id) or {}
    except Exception:
        info = {}

    pending_all = []
    try:
        rows = db.get_open_loops(session_id, status="pending")
        pending_all = [r["content"] for r in rows]
    except Exception:
        pending_all = []

    version = ""
    try:
        import config
        # 动态读文件，这样改了 VERSION 不用重启服务
        version = config.get_version()
    except Exception:
        version = ""

    scene = {}
    try:
        scene = db.get_scene(session_id) or {}
    except Exception:
        scene = {}

    return web.json_response({
        "time": info,
        # 全部返回，前端轮播展示（原来只给 3 条，轮播会漏）
        "pending": pending_all,
        "pending_count": len(pending_all),
        "version": version,
        "scene": scene,
    })


async def list_loops(request):
    """列出承诺；all=1 时含已闭环的"""
    session_id = request.query.get("session_id", "")
    include_closed = request.query.get("all", "") == "1"
    if include_closed:
        rows = db.get_all_open_loops(session_id)
    else:
        rows = db.get_open_loops(session_id, status="pending")
    return web.json_response({"loops": rows})


async def add_loop(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    content = (data.get("content") or "").strip()
    session_id = data.get("session_id", "")
    if not content:
        return web.json_response({"error": "内容不能为空"}, status=400)

    try:
        weight = max(1, min(5, int(data.get("weight") or 2)))
    except (TypeError, ValueError):
        weight = 2

    loop_id = db.save_open_loop(
        session_id, content,
        raw_quote=(data.get("raw_quote") or ""),
        promisor=data.get("promisor") or "user",
        due_at=data.get("due_at") or None,
        weight=weight,
    )
    return web.json_response({"ok": True, "id": loop_id})


async def update_loop(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    loop_id = data.get("id")
    if not loop_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    fields = {}
    if "content" in data and data["content"]:
        fields["content"] = data["content"]
    if "due_at" in data:
        fields["due_at"] = data["due_at"] or None
    if "weight" in data:
        try:
            fields["weight"] = max(1, min(5, int(data["weight"])))
        except (TypeError, ValueError):
            pass

    db.update_open_loop(loop_id, **fields)
    return web.json_response({"ok": True})


async def close_loop(request):
    """闭环：fulfilled（做到了）/ expired（没做到）/ dropped（不了了之）"""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    loop_id = data.get("id")
    status = data.get("status") or "fulfilled"
    if status not in _VALID_STATUS:
        return web.json_response({"error": "状态不合法"}, status=400)

    db.close_open_loop(loop_id, status, data.get("outcome") or "")
    return web.json_response({"ok": True})


async def delete_loop(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)
    loop_id = data.get("id")
    if loop_id:
        db.delete_open_loop(loop_id)
    return web.json_response({"ok": True})
