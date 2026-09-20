"""LLM 配置管理路由"""
import json
import os
import time
import urllib.request
from aiohttp import web

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "llm_config.json")


def load_llm_config():
    """加载 LLM 配置"""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"llms": []}


def save_llm_config(data):
    """保存 LLM 配置"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def list_llms(request):
    """获取所有 LLM 配置"""
    data = load_llm_config()
    return web.json_response(data)


async def add_llm(request):
    """添加自定义 LLM"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    name = body.get("name", "").strip()
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "").strip()
    model = body.get("model", "").strip()
    desc = body.get("desc", "").strip()

    if not name or not base_url or not model:
        return web.json_response({"error": "名称、API地址、模型名不能为空"}, status=400)

    data = load_llm_config()
    llm_id = f"custom_{int(time.time() * 1000)}"

    new_llm = {
        "id": llm_id,
        "name": name,
        "desc": desc or "自定义 LLM",
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "builtin": False
    }

    data["llms"].append(new_llm)
    save_llm_config(data)

    return web.json_response({"ok": True, "id": llm_id})


async def update_llm(request):
    """更新 LLM 配置"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    llm_id = body.get("id")
    if not llm_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    data = load_llm_config()
    for m in data["llms"]:
        if m.get("id") == llm_id:
            if "name" in body:
                m["name"] = body["name"]
            if "base_url" in body:
                m["base_url"] = body["base_url"]
            if "api_key" in body:
                m["api_key"] = body["api_key"]
            if "model" in body:
                m["model"] = body["model"]
            if "desc" in body:
                m["desc"] = body["desc"]
            break
    else:
        return web.json_response({"error": "未找到该 LLM"}, status=404)

    save_llm_config(data)
    return web.json_response({"ok": True})


async def delete_llm(request):
    """删除 LLM"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    llm_id = body.get("id")
    if not llm_id:
        return web.json_response({"error": "缺少 id"}, status=400)

    data = load_llm_config()
    # 内置 LLM 只能清空配置，不能删除
    for m in data["llms"]:
        if m.get("id") == llm_id:
            if m.get("builtin"):
                m["api_key"] = ""
                save_llm_config(data)
                return web.json_response({"ok": True, "action": "cleared"})
            else:
                data["llms"] = [x for x in data["llms"] if x.get("id") != llm_id]
                save_llm_config(data)
                return web.json_response({"ok": True, "action": "deleted"})

    return web.json_response({"error": "未找到该 LLM"}, status=404)


async def test_llm(request):
    """测试 LLM 连接"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的JSON"}, status=400)

    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "").strip()
    model = body.get("model", "").strip()

    # Ollama 特殊处理
    if "localhost:11434" in base_url or "127.0.0.1:11434" in base_url:
        try:
            test_url = f"{base_url}"
            data = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False
            }).encode("utf-8")
            req = urllib.request.Request(test_url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                reply = result.get("message", {}).get("content", "OK")
                return web.json_response({"ok": True, "reply": reply})
        except Exception as e:
            return web.json_response({"error": f"连接失败: {e}"}, status=400)

    try:
        data = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 20
        }).encode("utf-8")

        req = urllib.request.Request(
            base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            reply = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return web.json_response({"ok": True, "reply": reply})
    except Exception as e:
        return web.json_response({"error": f"连接失败: {e}"}, status=400)


def get_llm_chat_func(llm_id):
    """获取 LLM 调用函数"""
    data = load_llm_config()
    for m in data.get("llms", []):
        if m.get("id") == llm_id:
            api_key = m.get("api_key", "")
            base_url = m.get("base_url", "")
            model = m.get("model", "")

            # Ollama 特殊处理
            if "localhost:11434" in base_url or "127.0.0.1:11434" in base_url:
                def ollama_chat(messages, base_url=base_url, model=model, max_tokens=None):
                    msg_data = json.dumps({
                        "model": model,
                        "messages": messages,
                        "stream": False
                    }).encode("utf-8")
                    req = urllib.request.Request(base_url, data=msg_data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        result = json.loads(resp.read().decode())
                        return result["message"]["content"]
                return ollama_chat

            # 百度文心特殊处理
            if "baidubce.com" in base_url:
                def baidu_chat(messages, api_key=api_key, model=model, max_tokens=None):
                    # 获取 access_token
                    parts = api_key.split("|")
                    if len(parts) == 2:
                        client_id, client_secret = parts
                    else:
                        return "百度 API Key 格式应为: API_Key|Secret_Key"

                    token_url = f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}"
                    req = urllib.request.Request(token_url)
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        token_data = json.loads(resp.read().decode())
                        access_token = token_data["access_token"]

                    chat_url = f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}?access_token={access_token}"

                    new_messages = [{"role": "user" if m["role"] == "system" else m["role"], "content": m["content"]} for m in messages]

                    msg_data = json.dumps({"messages": new_messages}).encode("utf-8")
                    req = urllib.request.Request(chat_url, data=msg_data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        result = json.loads(resp.read().decode())
                        return result["result"]
                return baidu_chat

            # 通用 OpenAI 兼容格式
            def openai_chat(messages, base_url=base_url, api_key=api_key, model=model, max_tokens=2000, temperature=0.8):
                body = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
                # DeepSeek V4 默认思考模式：reasoning 会占满 max_tokens 导致 content 为空（空回复/卡住）
                # 这里显式关闭思考模式，确保每次都有正常回复
                if "deepseek" in model.lower():
                    body["thinking"] = {"type": "disabled"}
                msg_data = json.dumps(body).encode("utf-8")

                req = urllib.request.Request(
                    base_url,
                    data=msg_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    }
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode())
                    return result["choices"][0]["message"]["content"]
            return openai_chat

    return None


def get_llm_stream_func(llm_id):
    """获取 LLM 流式调用函数，不支持流式则返回 None"""
    data = load_llm_config()
    for m in data.get("llms", []):
        if m.get("id") == llm_id:
            api_key = m.get("api_key", "")
            base_url = m.get("base_url", "")
            model = m.get("model", "")

            # 百度不支持流式
            if "baidubce.com" in base_url:
                return None

            # Ollama 流式
            if "localhost:11434" in base_url or "127.0.0.1:11434" in base_url:
                def ollama_stream(messages, base_url=base_url, model=model, max_tokens=None):
                    """流式调用 Ollama，yield 文本片段"""
                    msg_data = json.dumps({
                        "model": model,
                        "messages": messages,
                        "stream": True
                    }).encode("utf-8")
                    req = urllib.request.Request(base_url, data=msg_data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        for line in resp:
                            line = line.decode("utf-8").strip()
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                                content = chunk.get("message", {}).get("content", "")
                                if content:
                                    yield content
                                if chunk.get("done", False):
                                    break
                            except (json.JSONDecodeError, KeyError):
                                continue
                return ollama_stream

            # OpenAI 兼容流式
            def openai_stream(messages, base_url=base_url, api_key=api_key, model=model, max_tokens=2000, temperature=0.8):
                """流式调用 OpenAI 兼容接口，yield 文本片段"""
                body = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True
                }
                # DeepSeek V4 默认思考模式：reasoning 会占满 max_tokens 导致 content 为空（空回复/卡住）
                # 这里显式关闭思考模式，确保每次都有正常回复
                if "deepseek" in model.lower():
                    body["thinking"] = {"type": "disabled"}
                msg_data = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    base_url, data=msg_data,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    for line in resp:
                        line = line.decode("utf-8").strip()
                        if line.startswith("data: "):
                            payload = line[6:]
                            if payload.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(payload)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
            return openai_stream
    return None
