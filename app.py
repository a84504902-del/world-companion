"""AI Companion - 主应用入口"""
import os
import shutil
import asyncio
import threading
import logging
from datetime import datetime

from aiohttp import web

import config
import db
import embedding
import memory_retriever
from log import setup_logging
from routes import chat, memory, person, relation, admin, custom_llm, template, stt, openloop, family_template
from routes import persona_api

setup_logging()
logger = logging.getLogger("app")


async def index_handler(request):
    """引导页（v0.13.0）：应用入口。选人、选对话、点进去才进聊天页。"""
    with open(os.path.join(config.BASE_DIR, "templates", "guide.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def chat_page_handler(request):
    """聊天页。支持 ?s=<session_id> 直接进入指定对话（引导页点进来时带）。"""
    # 一个会话都没有 → 没什么可聊的，直接回引导页
    if not db.get_all_sessions():
        raise web.HTTPFound(location="/")
    with open(os.path.join(config.BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def admin_page_handler(request):
    """返回后台管理页面"""
    with open(os.path.join(config.BASE_DIR, "templates", "admin.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


def _find_text_by_hash(text_hash):
    """TTS 缓存未命中时，在 DB 消息里反查 hash 对应的回复原文（限每个会话最近100条）"""
    import hashlib as _hl
    try:
        for s in db.get_all_sessions():
            msgs = db.load_messages(s["id"])
            for m in msgs[-100:]:
                if m["role"] != "assistant":
                    continue
                if _hl.md5(m["content"].encode("utf-8", errors="ignore")).hexdigest()[:12] == text_hash:
                    return m["content"]
    except Exception as e:
        logger.error("反查 TTS 原文失败: %s", e)
    return None


async def audio_handler(request):
    """返回音频文件（懒生成：文件不在就现场合成，不再卡回复热路径）"""
    filename = request.match_info["filename"]
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio", f"{filename}.mp3")

    if not os.path.exists(filepath):
        import tempfile
        filepath = os.path.join(tempfile.gettempdir(), f"tts_{filename}.mp3")

    if not os.path.exists(filepath):
        # 先查本次进程的回复缓存，再反查 DB 消息
        text = None
        try:
            from routes import chat as _chat
            text = _chat._tts_text_cache.get(filename)
        except Exception:
            text = None
        if not text:
            text = _find_text_by_hash(filename)
        if text:
            try:
                from tts import synthesize as tts_synthesize
                filepath = await tts_synthesize(text)
            except Exception as e:
                logger.error("TTS 现场合成失败: %s", e)
                return web.Response(status=500, text=f"合成失败: {e}")

    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        with open(filepath, "rb") as f:
            data = f.read()
        return web.Response(body=data, content_type="audio/mpeg")
    else:
        return web.Response(status=404, text="音频不存在")


KEEP_BACKUPS = 5  # 数据库自动备份保留份数


def backup_database():
    """备份数据库文件（保留最近 KEEP_BACKUPS 份）"""
    if not os.path.exists(config.DB_PATH):
        return
    backup_dir = os.path.join(config.BASE_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"memory_{timestamp}.db")

    # 同一秒内重复备份会撞名，加后缀
    if os.path.exists(backup_path):
        backup_path = os.path.join(backup_dir, f"memory_{timestamp}_2.db")

    try:
        # 用 VACUUM INTO 做一致性备份：
        # 直接 shutil.copy2 会漏掉 wal 里还没落盘的数据
        db.connect().execute("VACUUM INTO ?", (backup_path.replace("\\", "/"),))
    except Exception as e:
        logger.error("数据库备份失败: %s", e)
        return

    # 清理旧备份
    try:
        backups = sorted(
            [f for f in os.listdir(backup_dir)
             if f.startswith("memory_") and f.endswith(".db")]
        )
        while len(backups) > KEEP_BACKUPS:
            old = backups.pop(0)
            try:
                os.remove(os.path.join(backup_dir, old))
            except Exception as e:
                logger.warning("删除旧备份失败（不影响使用）: %s - %s", old, e)
        logger.info("数据库已备份: %s（保留最近 %d 份）", backup_path, KEEP_BACKUPS)
    except Exception as e:
        logger.error("清理旧备份失败: %s", e)


async def backup_handler(request):
    """手动触发数据库备份"""
    try:
        backup_database()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def create_app():
    """创建应用"""

    # 前端文件禁缓存：改了 JS/CSS 用户刷新就能看到，不用强刷（踩过"改了没反应"的坑）
    @web.middleware
    async def no_cache_frontend(request, handler):
        resp = await handler(request)
        if (request.path.startswith("/static")
                or request.path in ("/", "/index.html", "/chat")):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    # 可选访问令牌鉴权：.env 配了 ACCESS_TOKEN 才启用；未配则零影响
    @web.middleware
    async def access_token_guard(request, handler):
        if config.ACCESS_TOKEN:
            token = (
                request.query.get("token")
                or request.headers.get("X-Access-Token")
                or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
            if token != config.ACCESS_TOKEN:
                raise web.HTTPUnauthorized(
                    text="访问令牌无效", content_type="text/plain"
                )
        return await handler(request)

    middlewares = [no_cache_frontend] + ([access_token_guard] if config.ACCESS_TOKEN else [])
    app = web.Application(client_max_size=10 * 1024 * 1024, middlewares=middlewares)

    async def on_cleanup(app):
        db.close_conn()
    app.on_cleanup.append(on_cleanup)

    # 引导页（应用入口）
    app.router.add_get("/", index_handler)

    # 聊天页（?s=xxx 直接进指定对话）
    app.router.add_get("/chat", chat_page_handler)
    app.router.add_get("/index.html", chat_page_handler)

    # 后台管理页面
    app.router.add_get("/admin", admin_page_handler)

    # 音频
    app.router.add_get("/audio/{filename}", audio_handler)

    # 聊天
    app.router.add_post("/chat", chat.chat_handler)
    app.router.add_post("/api/catchup", chat.catchup_handler)
    app.router.add_post("/api/session_model", chat.set_model_handler)
    app.router.add_get("/history", chat.history_handler)
    app.router.add_get("/chat_sessions", chat.sessions_handler)
    app.router.add_post("/new_chat", chat.new_chat_handler)
    app.router.add_post("/switch_chat", chat.switch_chat_handler)
    app.router.add_post("/clear_chat", chat.clear_chat_handler)
    app.router.add_post("/delete_session", chat.delete_session_handler)
    app.router.add_get("/search_history", chat.search_history_handler)
    app.router.add_get("/export_chat", chat.export_chat_handler)
    app.router.add_post("/api/system_prompt", chat.update_system_prompt_handler)
    app.router.add_get("/api/system_prompt", chat.get_system_prompt_handler)
    app.router.add_get("/api/summarize", chat.summarize_handler)
    app.router.add_get("/api/search_chat", chat.search_chat_handler)
    app.router.add_post("/api/rename_session", chat.rename_session_handler)
    app.router.add_post("/api/cancel_chat", chat.cancel_chat_handler)
    app.router.add_get("/api/proposals", chat.get_proposals_handler)
    app.router.add_post("/api/proposals/accept", chat.accept_proposal_handler)
    app.router.add_post("/api/proposals/reject", chat.reject_proposal_handler)

    # 角色人格 / 世界模板 / 对话式引导（v0.12.0）
    persona_api.setup(app)

    # 记忆
    app.router.add_get("/memories", memory.list_memories)
    app.router.add_post("/api/save_memory", memory.save_memory)
    app.router.add_post("/delete_memory", memory.delete_memory)
    app.router.add_post("/api/save_memory_from_message", memory.save_from_message)
    app.router.add_post("/api/memory/update_weight", memory.update_weight)
    app.router.add_get("/api/pins/list", memory.list_pins)
    app.router.add_post("/api/pins/delete", memory.delete_pin)

    # 人物/关系模板（独立配置模块）
    app.router.add_get("/api/family_template/list", family_template.list_family_templates)
    app.router.add_post("/api/family_template/save", family_template.save_family_template)
    app.router.add_post("/api/family_template/import", family_template.import_family_template)
    app.router.add_post("/api/family_template/delete", family_template.delete_family_template)
    app.router.add_get("/api/family_template/export", family_template.export_current)
    app.router.add_post("/api/family_template/import_data", family_template.import_uploaded)

    # 人物
    app.router.add_get("/api/people/list", person.list_people)
    app.router.add_post("/api/people/add", person.add_person)
    app.router.add_post("/api/people/update", person.update_person)
    app.router.add_post("/api/people/delete", person.delete_person)

    # 关系
    app.router.add_get("/api/relations/list", relation.list_relations)
    app.router.add_post("/api/relations/add", relation.add_relation)
    app.router.add_post("/api/relations/update", relation.update_relation)
    app.router.add_post("/api/relations/delete", relation.delete_relation)

    # 后台 API
    app.router.add_get("/api/stats", admin.stats_handler)
    app.router.add_get("/api/llm_status", admin.llm_status_handler)
    app.router.add_get("/api/sessions", admin.sessions_handler)
    app.router.add_get("/api/database/info", admin.database_info_handler)
    app.router.add_post("/api/database/vacuum", admin.database_vacuum_handler)

    # 未完结事件（她在等的事 —— 核心引擎）
    app.router.add_get("/api/status", openloop.status)
    app.router.add_get("/api/loops/list", openloop.list_loops)
    app.router.add_post("/api/loops/add", openloop.add_loop)
    app.router.add_post("/api/loops/update", openloop.update_loop)
    app.router.add_post("/api/loops/close", openloop.close_loop)
    app.router.add_post("/api/loops/delete", openloop.delete_loop)

    # 自定义 LLM
    app.router.add_get("/api/llms", custom_llm.list_llms)
    app.router.add_post("/api/llms/add", custom_llm.add_llm)
    app.router.add_post("/api/llms/update", custom_llm.update_llm)
    app.router.add_post("/api/llms/delete", custom_llm.delete_llm)
    app.router.add_post("/api/llms/test", custom_llm.test_llm)

    # 提示词模板
    app.router.add_get("/api/templates", template.list_templates)
    app.router.add_post("/api/templates/add", template.add_template)
    app.router.add_post("/api/templates/update", template.update_template)
    app.router.add_post("/api/templates/delete", template.delete_template)

    # 数据库备份
    app.router.add_post("/api/database/backup", backup_handler)

    # 语音识别
    app.router.add_post("/api/stt", stt.speech_to_text)

    # 静态文件
    app.router.add_static("/static", os.path.join(config.BASE_DIR, "static"))

    return app


def _init_embedding_background():
    """后台初始化嵌入模型、预加载向量缓存、索引存量对话原文"""
    try:
        memory_retriever.preload_cache()
        if not embedding.is_ready():
            embedding.load_model()
            # 预加载后重新构建缓存（如果有新记忆需要向量化）
            memory_retriever.preload_cache()
        # 增量索引所有会话的存量对话原文（幂等，可重复执行）
        sessions = db.get_all_sessions()
        for s in sessions:
            try:
                memory_retriever.index_conversation_chunks(s["id"])
            except Exception as e:
                logger.error("索引会话 %s 对话原文失败: %s", s["id"], e)
    except Exception as e:
        logger.error("嵌入初始化失败: %s", e)


def main():
    """启动应用"""
    # 初始化数据库
    db.init_db()

    # 备份数据库
    backup_database()

    # 创建默认会话
    sessions = db.get_all_sessions()
    if not sessions:
        session_id = db.new_session_id()
        db.create_session(session_id)
        db.create_message_table(session_id)
        chat._current_session_id = session_id
    else:
        chat._current_session_id = sessions[0]["id"]

    logger.info("AI Companion 启动中...")
    logger.info("访问地址: http://localhost:%s", config.PORT)
    logger.info("数据库: %s", config.DB_PATH)

    # 后台初始化嵌入模型（不阻塞启动）
    threading.Thread(target=_init_embedding_background, daemon=True).start()

    app = create_app()
    web.run_app(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
