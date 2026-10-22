"""人格与模板管理接口（角色列表 / 世界模板 / 引导）。

路由：
  GET  /api/personas                 角色列表
  GET  /api/personas/active          当前生效人设 + 来源（db/file/none）
  POST /api/personas/import-seed     从 JSON 种子导入人格库（自动先备份）
  POST /api/personas                 新建/更新角色
  DELETE /api/personas/{id}          删角色（主角不删）
  POST /api/personas/{id}/protagonist 设为主角
  GET  /api/world_templates          世界模板列表
  POST /api/world_templates          新建/更新模板
  DELETE /api/world_templates/{id}   删模板（内置不删）
  POST /api/world_templates/{id}/duplicate  复制一份（改造成新世界的起点）

  GET  /api/onboarding/status        有没有进行中的草稿
  POST /api/onboarding/start         开始/继续引导
  POST /api/onboarding/reply         引导中回一句话
  POST /api/onboarding/preview       看预览
  POST /api/onboarding/finish        确认落库
  POST /api/onboarding/quick_create  一句话快速建角色（lite 档）
  POST /api/onboarding/abort         取消引导
  POST /api/onboarding/form_draft    表单式建世界：固定字段直接存草稿（不走 AI 对话提问）
"""
import json
import logging

from aiohttp import web

import db
import onboarding as ob
import persona_store as ps

logger = logging.getLogger("persona_api")


async def _body(request):
    """请求体可能是 JSON 也可能是表单，统一读成 dict"""
    try:
        return json.loads(await request.text() or "{}")
    except Exception:
        return {}


def _list_fields(pa):
    """给前端用的精简字段（长文本不重复下发，前端要详情再单独 GET）"""
    return {
        "id": pa.get("id"),
        "name": pa.get("name"),
        "is_protagonist": bool(pa.get("is_protagonist")),
        "depth": pa.get("depth"),
        "world_template_id": pa.get("world_template_id"),
        "world_scope": pa.get("world_scope") or "",
        "summary": pa.get("summary"),
        "age": pa.get("age"),
        "occupation": pa.get("occupation"),
        "core_traits": pa.get("core_traits") or [],
        "speech_style": pa.get("speech_style"),
        "likes": pa.get("likes"),
        "dislikes": pa.get("dislikes"),
        "rule_count": len(pa.get("rules") or []),
        "example_count": len(pa.get("examples") or []),
        "created": pa.get("created"),
        "updated": pa.get("updated"),
    }


# ================================================================
# 角色
# ================================================================

async def active_persona_info(request):
    """当前生效人设 + 来源（db / file / none）—— 双源机制可观测化

    v0.15.3：以前「谁生效」只能翻代码，现在一个接口说清：
    source=db 正常；file 说明人格库为空、人设来自 JSON 种子兜底，
    此时改 JSON 不会生效、改库也没东西可改，需要 import-seed。
    """
    source = ps.persona_source()
    pa = ps.active_persona() or {}
    resp = {
        "ok": True,
        "source": source,
        "persona_id": pa.get("id", ""),
        "persona_name": pa.get("name", ""),
    }
    if source == "file":
        resp["warning"] = ("当前人设来自 JSON 种子文件兜底（人格库为空）。"
                           "此时改 JSON 文件不会立即生效、也没有库可改——"
                           "请用「从种子导入人格库」把文件写进库，之后走 API 维护。")
    return web.json_response(resp)


async def import_seed(request):
    """从 JSON 种子文件（persona_local.json / persona_anchor.json）导入人格库

    自动先全量备份现有库到 backups/。已有主角就地覆盖（保住 id 和 DB 专属字段），
    库空则新建 prs_xiaxue。
    """
    try:
        result = ps.import_seed_persona()
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.exception("种子导入失败")
        return web.json_response({"error": f"导入失败：{e}"}, status=500)
    try:
        import prompts
        prompts.reload_anchor()
    except Exception:
        pass
    action = "新建" if result["created"] else "覆盖更新"
    return web.json_response({
        "ok": True, **result,
        "reply": (f"已从种子文件（{result['source']}）{action}人格「{result['name']}」。"
                  f"原库备份：{result['backup'] or '无需备份（库原本为空）'}"),
    })


async def list_personas(request):
    """角色列表（含当前主角标记）"""
    items = [_list_fields(p) for p in ps.list_personas()]
    # v0.17.5：世界归属人话名（模板 id 显示模板名，自由世界显示世界名/题目）
    tpl_names = {t["id"]: t["name"] for t in ps.list_world_templates()}
    for p in items:
        scope = p.get("world_scope") or ""
        p["world_label"] = tpl_names.get(scope, scope) if scope else "未归属世界"
    pro = ps.get_protagonist()
    return web.json_response({
        "personas": items,
        "protagonist_id": (pro or {}).get("id", ""),
        "protagonist_name": (pro or {}).get("name", ""),
    })


async def get_persona_detail(request):
    """角色完整详情（含规则与样例，编辑用）"""
    pid = request.match_info.get("id", "")
    pa = ps.get_persona(pid)
    if not pa:
        return web.json_response({"error": "角色不存在"}, status=404)
    return web.json_response({"persona": pa})


async def save_persona(request):
    """新建或更新角色"""
    body = await _body(request)
    pid = body.get("id") or request.match_info.get("id")
    if not body.get("name"):
        return web.json_response({"error": "缺少 name"}, status=400)
    pid = ps.save_persona(body, pid)
    # 落库后让提示词缓存失效
    try:
        import prompts
        prompts.reload_anchor()
    except Exception:
        pass
    return web.json_response({"ok": True, "id": pid,
                              "persona": ps.get_persona(pid)})


async def delete_persona(request):
    pid = request.match_info.get("id", "")
    pa = ps.get_persona(pid)
    if pa and pa.get("is_protagonist"):
        return web.json_response({"error": "主角不能删。先把别的主角设上。"}, status=400)
    ps.delete_persona(pid)
    return web.json_response({"ok": True})


async def set_protagonist(request):
    """设为主角（v0.17.5：同一个世界内唯一，别世界的主角不受影响）"""
    pid = request.match_info.get("id", "")
    pa = ps.get_persona(pid)
    if not pa:
        return web.json_response({"error": "角色不存在"}, status=404)
    ps.save_persona({**pa, "is_protagonist": 1})
    try:
        import prompts
        prompts.reload_anchor()
    except Exception:
        pass
    return web.json_response({"ok": True, "name": pa["name"],
                              "reply": f"主角已换成「{pa['name']}」。"
                                       f"说「切回{pa['name']}」可切回。"})


# ================================================================
# 世界模板
# ================================================================

async def list_templates(request):
    tpls = ps.list_world_templates()
    # v0.17.5：旗下角色阵容按 world_scope 归属判（世界隔离的总口径）；
    # 老数据只有 world_template_id 没有 scope 的，按引用兜底算进来
    cast = {}
    for p in ps.list_personas():
        scope = p.get("world_scope") or ""
        tid = p.get("world_template_id") or ""
        if scope:
            cast.setdefault(scope, []).append(p["name"])
        elif tid:
            cast.setdefault(tid, []).append(p["name"])
    for t in tpls:
        names = cast.get(t["id"], [])
        t["used_by"] = len(names)
        t["cast_names"] = names
    return web.json_response({"templates": tpls})


async def save_template(request):
    body = await _body(request)
    if not body.get("name"):
        return web.json_response({"error": "缺少 name"}, status=400)
    tid = ps.save_world_template(body, body.get("id") or request.match_info.get("id"))
    return web.json_response({"ok": True, "id": tid,
                              "template": ps.get_world_template(tid)})


async def delete_template(request):
    tid = request.match_info.get("id", "")
    tpl = ps.get_world_template(tid)
    if tpl and tpl.get("is_builtin"):
        return web.json_response({"error": "内置模板不能删，可以复制一份改。"}, status=400)
    # 有角色在用就别删
    users = [p["name"] for p in ps.list_personas() if p.get("world_template_id") == tid]
    if users:
        return web.json_response(
            {"error": f"还有角色在用这个模板（{'、'.join(users)}），删了他们的世界会空。"}, status=400)
    ps.delete_world_template(tid)
    return web.json_response({"ok": True})


async def duplicate_template(request):
    """复制模板 —— 这是「新建一个自己的世界」的起点"""
    tid = request.match_info.get("id", "")
    tpl = ps.get_world_template(tid)
    if not tpl:
        return web.json_response({"error": "模板不存在"}, status=404)
    tpl = dict(tpl)
    tpl.pop("id", None)
    tpl["name"] = request.query.get("name") or f"{tpl['name']} 副本"
    tpl["is_builtin"] = 0
    nid = ps.save_world_template(tpl)
    return web.json_response({"ok": True, "id": nid,
                              "template": ps.get_world_template(nid)})


# ================================================================
# 引导
# ================================================================

async def onboarding_status(request):
    d = ps.get_active_draft()
    if not d:
        return web.json_response({"active": False})
    return web.json_response({
        "active": True,
        "draft_id": d["id"],
        "depth": d.get("depth"),
        "current_layer": d.get("current_layer"),
        "world_template_id": d.get("world_template_id", ""),
        "collected": d.get("collected"),
        "history": (d.get("history") or [])[-6:],
    })


async def onboarding_start(request):
    body = await _body(request)
    r = ob.start(draft_id=body.get("draft_id"),
                 depth=body.get("depth", "full"),
                 world_template_id=body.get("world_template_id", ""),
                 mode=body.get("mode"),
                 force_new=bool(body.get("force_new")))
    return web.json_response(r)


async def onboarding_reply(request):
    body = await _body(request)
    did = body.get("draft_id")
    if not did:
        return web.json_response({"reply": "先点开始引导。", "done": False})
    r = ob.reply(did, body.get("text", ""), mode=body.get("mode"))
    return web.json_response(r)


async def onboarding_preview(request):
    body = await _body(request)
    return web.json_response(ob.preview(body.get("draft_id", ""),
                                         mode=body.get("mode")))


async def onboarding_finish(request):
    body = await _body(request)
    r = ob.finalize(body.get("draft_id", ""), mode=body.get("mode"),
                    save_as_template=bool(body.get("save_as_template")))
    return web.json_response(r)


async def onboarding_quick_create(request):
    """一句话建对话（lite 档，不逐项问）"""
    body = await _body(request)
    if not body.get("name"):
        return web.json_response({"error": "缺少 name"}, status=400)
    r = ob.quick_create(body["name"], body.get("summary", ""),
                        world_template_id=body.get("world_template_id", ""),
                        mode=body.get("mode"))
    return web.json_response(r)


async def onboarding_abort(request):
    body = await _body(request)
    return web.json_response(ob.abort(body.get("draft_id", "")))


async def onboarding_form_draft(request):
    """表单式建世界（v0.17.2）：前端固定字段直接存草稿，不走 AI 对话提问。

    存完 current_layer=6（= 五层已收齐），之后走现成的 preview/finish。
    """
    import persona_store as ps
    body = await _body(request)
    collected = body.get("collected") or {}
    if not isinstance(collected, dict):
        return web.json_response({"error": "collected 格式不对"}, status=400)
    did = ps.save_draft(None, collected, 6, [],
                        world_template_id=body.get("world_template_id", ""))
    return web.json_response({"ok": True, "draft_id": did})


# ================================================================
# 引导页（v0.13.0）
# ================================================================

def _session_preview(session_id, limit=60):
    """取这个会话最后一条消息的一小段，做卡片上的预览文字。

    刻意只读尾部：引导页要一次列全部会话，逐个 load_messages 拉全量
    会让消息多的老会话卡住整个页面。
    """
    table = f"messages_{session_id}"
    try:
        rows = db.connect().execute(
            f"SELECT role, content FROM [{table}] ORDER BY id DESC LIMIT 1"
        ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    content = (rows[0]["content"] or "").strip().replace("\n", " ")
    # 去掉模型附带的状态标记，别把 [[STATE]] 露给用户看
    idx = content.find("[[STATE]]")
    if idx >= 0:
        content = content[:idx].strip()
    if len(content) > limit:
        content = content[:limit] + "…"
    return content


async def guide_home(request):
    """引导页一次性数据：会话列表 + 角色 + 世界模板 + 草稿状态。

    一次拉齐是为了避免引导页并发发 4 个请求（慢，且任一失败就半截页面）。
    """
    sessions = []
    for s in db.get_all_sessions():
        sessions.append({
            "id": s["id"],
            "title": s["title"] or "未命名对话",
            "created": s["created"],
            "msg_count": db.get_message_count(s["id"]),
            "preview": _session_preview(s["id"]),
        })

    personas = [_list_fields(p) for p in ps.list_personas()]
    templates = ps.list_world_templates()
    tpl_names = {t["id"]: t["name"] for t in templates}

    # 给角色补上世界模板名，引导页卡片上直接显示「属于哪个世界」
    for p in personas:
        tid = p.get("world_template_id") or ""
        p["world_name"] = tpl_names.get(tid, "")

    # 每个角色名下有几个对话——回答「建这个角色有什么用」：
    # 有对话的说明已经能开聊，没有的说明还差一步。
    session_counts = {}
    for s in db.get_all_sessions():
        pid = s.get("persona_id") or ""
        if pid:
            session_counts[pid] = session_counts.get(pid, 0) + 1
    for p in personas:
        p["session_count"] = session_counts.get(p.get("id", ""), 0)

    draft = ps.get_active_draft()
    pro = ps.get_protagonist()

    # v0.17.5 世界包视图：角色按 world_scope 分组（模板 id 显示模板名）。
    # 前端「和谁聊」按包展示——一个世界一摞卡，不再所有角色混排
    def _scope_label(scope):
        return tpl_names.get(scope, scope) if scope else "未归属世界"

    _groups = {}
    for p in personas:
        _groups.setdefault(p.get("world_scope") or "", []).append(p)
    worlds = [{
        "scope": scope,
        "label": _scope_label(scope),
        "persona_count": len(members),
        "persona_names": [m["name"] for m in members],
        "session_count": sum(m.get("session_count", 0) for m in members),
    } for scope, members in _groups.items()]

    return web.json_response({
        "sessions": sessions,
        "personas": personas,
        "worlds": worlds,
        "templates": [{"id": t["id"], "name": t["name"],
                       "genre": t.get("genre", ""),
                       "summary": t.get("summary", ""),
                       "cast_names": [p["name"] for p in ps.list_personas_by_scope(t["id"])],
                       # is_builtin 要给前端：引导页模板列表靠它决定显不显示「删」
                       "is_builtin": bool(t.get("is_builtin"))} for t in templates],
        "protagonist_id": (pro or {}).get("id", ""),
        "protagonist_name": (pro or {}).get("name", ""),
        "has_draft": bool(draft),
        "draft_id": (draft or {}).get("id", ""),
    })


async def guide_enter(request):
    """从引导页进入某个对话。

    两种入口：
    - 只给 session_id  → 继续那个已有对话
    - 给 persona_id（不给 session_id）→ 用这个角色开一个新对话，
      并把会话的扮演视角绑到该角色上（这样进去就能直接跟她说话）
    """
    body = await _body(request)
    from routes import chat as _chat

    persona_id = (body.get("persona_id") or "").strip()
    session_id = (body.get("session_id") or "").strip()

    if not session_id and persona_id:
        pa = ps.get_persona(persona_id)
        if not pa:
            return web.json_response({"error": "角色不存在"}, status=404)
        pro = ps.get_protagonist() or {}
        session_id = db.new_session_id()
        title = f"和{pa['name']}的对话"
        db.create_session(session_id, title=title, title_locked=True)
        db.create_message_table(session_id)
        # 会话记住用哪个角色（重开页面能恢复）
        ps.set_session_persona(session_id, persona_id)
        # 非主角才需要切视角，主角本来就是默认视角
        if not pa.get("is_protagonist"):
            db.set_actor(session_id, pa["name"])
        model = (body.get("model") or "").strip()
        if model:
            db.set_session_model(session_id, model)
        _chat._current_session_id = session_id
        _chat._session_states[session_id] = _chat.SessionState()

    if not session_id:
        # 兜底：只点了「新建对话」，不带任何角色
        session_id = db.new_session_id()
        db.create_session(session_id)
        db.create_message_table(session_id)
        _chat._current_session_id = session_id
        _chat._session_states[session_id] = _chat.SessionState()

    return web.json_response({"ok": True, "session_id": session_id})


def setup(app):
    # 注意：静态路径必须注册在 {id} 动态路径之前，否则 "active"/"import-seed"
    # 会被当成角色 id 匹配走
    app.router.add_get("/api/personas/active", active_persona_info)
    app.router.add_post("/api/personas/import-seed", import_seed)

    app.router.add_get("/api/personas", list_personas)
    app.router.add_get("/api/personas/{id}", get_persona_detail)
    app.router.add_post("/api/personas", save_persona)
    app.router.add_post("/api/personas/{id}", save_persona)
    app.router.add_post("/api/personas/{id}/delete", delete_persona)
    app.router.add_post("/api/personas/{id}/protagonist", set_protagonist)

    app.router.add_get("/api/world_templates", list_templates)
    app.router.add_post("/api/world_templates", save_template)
    app.router.add_post("/api/world_templates/{id}", save_template)
    app.router.add_post("/api/world_templates/{id}/delete", delete_template)
    app.router.add_post("/api/world_templates/{id}/duplicate", duplicate_template)

    app.router.add_get("/api/onboarding/status", onboarding_status)
    app.router.add_post("/api/onboarding/start", onboarding_start)
    app.router.add_post("/api/onboarding/reply", onboarding_reply)
    app.router.add_post("/api/onboarding/preview", onboarding_preview)
    app.router.add_post("/api/onboarding/finish", onboarding_finish)
    app.router.add_post("/api/onboarding/quick_create", onboarding_quick_create)
    app.router.add_post("/api/onboarding/abort", onboarding_abort)
    app.router.add_post("/api/onboarding/form_draft", onboarding_form_draft)

    # 引导页（v0.13.0）
    app.router.add_get("/api/guide/home", guide_home)
    app.router.add_post("/api/guide/enter", guide_enter)