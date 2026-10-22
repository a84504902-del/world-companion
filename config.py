"""配置管理模块"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "memory.db")
ENV_PATH = os.path.join(BASE_DIR, ".env")


def load_env():
    """从 .env 文件读取配置"""
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


ENV = load_env()

# 服务器配置
# HOST 默认只绑本机：服务无鉴权，绑 0.0.0.0 时同一 Wi-Fi 下任何人都能访问/偷 Key。
# 确实需要手机等局域网设备访问时，在 .env 显式写 HOST=0.0.0.0（并建议同时配 ACCESS_TOKEN）
HOST = ENV.get("HOST", "127.0.0.1")
PORT = int(ENV.get("PORT", 8765))

# 访问令牌（可选）：配了非空值后，所有请求必须带 ?token=xxx 或 X-Access-Token: xxx 头。
# 不配 = 不启用鉴权（默认）。配了之后浏览器打开 http://127.0.0.1:8877/?token=xxx 用。
ACCESS_TOKEN = ENV.get("ACCESS_TOKEN", "")

# LLM 配置
DEEPSEEK_API_KEY = ENV.get("DEEPSEEK_API_KEY", "")
ZHIPU_API_KEY = ENV.get("ZHIPU_API_KEY", "")
DASHSCOPE_API_KEY = ENV.get("DASHSCOPE_API_KEY", "")
BAIDU_API_KEY = ENV.get("BAIDU_API_KEY", "")
BAIDU_SECRET_KEY = ENV.get("BAIDU_SECRET_KEY", "")
AGNES_API_KEY = ENV.get("AGNES_API_KEY", "")
OLLAMA_URL = ENV.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = ENV.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")

# TTS 配置
TTS_PROXY_PORT = int(ENV.get("TTS_PROXY_PORT", 7851))
TTS_VOICE = ENV.get("TTS_VOICE", "zh-CN-XiaoyiNeural")


def get_version():
    """读 VERSION 文件作为版本号唯一来源，避免多处不同步"""
    try:
        with open(os.path.join(BASE_DIR, "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


VERSION = get_version()
