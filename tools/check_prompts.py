"""提示词配置自检（v0.13.1 新增，v0.16.0 支持文件夹）。

解决什么问题：
prompts/ 是「本文件优先、代码 default 兜底」的覆盖层。这带来两个容易踩的坑：

1. **死配置**：json 里写了某个键，但代码从来没 get_prompt 它 → 改了以为生效，实际没反应。
   （真踩过：summary_detail 的文案被硬编码在 chat.py 里，json 那份是死的。）
2. **硬编码角色名**：文案里写死「夏雪」，换个主角这套提示词就失效。
   （真踩过：state_card 里写死夏雪 + 大方主动坦率，而 get_prompt 优先读 json，
     所以 chat.py 里那份动态兜底文案从来没被执行过。）

用法：
    python tools/check_prompts.py
退出码 0 = 全过，1 = 有问题（可直接挂到回归里）。
"""
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(BASE, "prompts")
CODE_FILES = ["routes/chat.py", "llm.py", "prompts.py", "routes/persona_api.py",
              "scene.py", "loops.py", "proactive.py", "onboarding.py",
              "memory_retriever.py"]

# prompts/ 里允许出现的角色名白名单（只放"确实属于该角色数据"的地方，
# 通用文案里出现就是硬编码）
GENERIC_NAME_HINTS = ("主角", "角色", "人设", "对方")


def load_prompts():
    """合并 prompts/ 文件夹下全部 *.json（与 prompts.py 同规则：按文件名排序）"""
    merged = {}
    sources = {}
    try:
        files = sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith(".json"))
    except OSError as e:
        print("✗ 无法读取 prompts/ 文件夹: %s" % e)
        return None
    for name in files:
        try:
            with io.open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print("✗ prompts/%s 读取失败: %s" % (name, e))
            return None
        if isinstance(data, dict):
            for k, v in data.items():
                if k in merged:
                    print("✗ 提示词 key '%s' 在 %s 与 %s 重复（后者覆盖前者）"
                          % (k, sources[k], name))
                merged[k] = v
                sources[k] = name
    return merged


def read_source():
    src = {}
    for rel in CODE_FILES:
        p = os.path.join(BASE, rel)
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8") as f:
            src[rel] = f.read()
    return src


def main():
    d = load_prompts()
    if d is None:
        return 1
    src = read_source()
    all_src = "\n".join(src.values())
    problems = []

    # ---- 1. 死配置：json 有键但代码没 get_prompt ----
    reserved = {"length_modes"}
    called = set(re.findall(r'get_prompt\(\s*f?"([a-z_0-9.]+)"', all_src))
    called |= set(re.findall(r'get_length_mode\(\s*[\'"]?([a-z_0-9]*)', all_src))
    # f-string 动态 key（如 f"onboarding.layer_{layer}"）：按前缀匹配数字后缀
    f_prefixes = re.findall(r'get_prompt\(\s*f"([a-z_0-9.]+)\{', all_src)
    for key in d:
        if key in reserved or key in called:
            continue
        if any(key.startswith(p) and key[len(p):].isdigit() for p in f_prefixes):
            continue
        problems.append(
            "死配置：prompts/ 里的 '%s' 没有对应的 get_prompt 调用，改它不会有任何效果。" % key
        )

    # ---- 2. 硬编码角色名（只扫通用文案，跳过 _readme 这类说明）----
    known_names = _known_persona_names()
    for key, val in d.items():
        if key in reserved or key.startswith("_"):
            continue
        text = val if isinstance(val, str) else "\n".join(
            v for v in val.values() if isinstance(v, str))
        for name in known_names:
            if name in text:
                problems.append(
                    "硬编码角色名：prompts/ 里 '%s' 里写死了「%s」。"
                    "应改成 {{PROTAGONIST}} 占位符。" % (key, name)
                )

    # ---- 3. 占位符未接线：json 里的占位符，调用方没传值 ----
    for key, val in d.items():
        if key in reserved or key.startswith("_") or not isinstance(val, str):
            continue
        # 代码里出现该 key 的 get_prompt 调用，收集它传的 kwargs
        passed = _passed_vars(all_src, key)
        for ph in set(re.findall(r"\{\{([A-Z_]+)\}\}", val)):
            if ph in passed:
                continue        # 调用方显式传了
            if ph in ("PROTAGONIST", "TRAITS", "USER_NAME", "RELATION"):
                continue        # persona_vars() 自动提供
            problems.append(
                "占位符未接线：'%s' 用了 {{%s}} 但调用方没传该变量。"
                % (key, ph)
            )

    # ---- 报告 ----
    if problems:
        print("✗ 提示词配置自检未通过（%d 项）\n" % len(problems))
        for i, p in enumerate(problems, 1):
            print("  %d. %s" % (i, p))
        print()
        return 1

    print("✓ 提示词配置自检通过：无死配置、无硬编码角色名、占位符全部接线")
    print("  已检查 %d 个 prompt 键，%d 个源码文件" % (len(d), len(src)))
    return 0


def _known_persona_names():
    """从人格库取当前角色名（用于硬编码检测）。取不到就返回空列表，不阻断自检。"""
    names = set()
    try:
        sys.path.insert(0, BASE)
        import persona_store
        try:
            pa = persona_store.active_persona()
            if pa and pa.get("name"):
                names.add(pa["name"])
        except Exception:
            pass
        try:
            for p in persona_store.list_personas():
                if p.get("name"):
                    names.add(p["name"])
        except Exception:
            pass
    except Exception:
        pass
    return {n for n in names if len(n) >= 2}


def _passed_vars(src, key):
    """粗略提取 get_prompt(key, ...) 调用里传的 kwargs 名（含 f-string 动态 key）。"""
    passed = set()
    patterns = [r'get_prompt\(\s*"%s"' % re.escape(key)]
    for p in re.findall(r'get_prompt\(\s*f"([a-z_0-9.]+)\{', src):
        if key.startswith(p) and key[len(p):].isdigit():
            patterns.append(r'get_prompt\(\s*f"%s\{' % re.escape(p))
    for pattern in patterns:
      for m in re.finditer(pattern, src):
        # 从 get_prompt( 之后截 800 字符，找其中的大写标识符= 值
        tail = src[m.end():m.end() + 800]
        # 到配平的右括号为止
        depth, cut = 1, len(tail)
        for i, ch in enumerate(tail):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    cut = i
                    break
        seg = tail[:cut]
        passed |= set(re.findall(r"\b([A-Z][A-Z_0-9]*)\s*=", seg))
        # **persona_vars() 展开的变量名拿不到字面量，这里不猜
    return passed


if __name__ == "__main__":
    sys.exit(main())