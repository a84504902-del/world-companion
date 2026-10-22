"""对话式角色引导引擎。

设计核心（这是整个系统最重要的设计决定）：
**引导员≠ 角色本身。**

引导的时候用户面对的不是角色，而是一个"角色设计师"。项目只有一个 system prompt
位置，所以引导期间临时用引导员人设，聊完落库再切回角色人设。
好处是角色不会在还没成型时就"演"出来——避免"角色知道自己还没被定义"的混乱。

两档生成（用户明确要求）：
- full 主角档：用户逐项说核心（关系/性格/雷区），AI 补语气样例与规则。
- lite 配角档：用户只给一句话（"小雨，夏雪的表妹，大学生，闷骚"），
  **AI 读完全部上下文后自己推导全套人设**，用户不参与。

AI 自动生成的关键：喂给它足够的既有上下文（世界模板 / 已有角色 / 关系称呼表），
这样它推出的辈分、称呼、调性才自洽。
"""
import json
import logging
import re

import db
from prompts import get_prompt
import persona_store as ps

logger = logging.getLogger("onboarding")

# ================================================================
# 五层引导框架 —— 建的是「一个新对话」，不是「一个孤立角色」
# ---------------------------------------------------------------
# 定位：一次引导 = 一个新剧本（世界 + 你是谁 + 主要人物 + 关系 + 关系网）。
# 引导结束会直接落成一个可玩的对话，出现在引导页，点一下就能开始角色扮演。
# 「建一个孤立人设」不是这里的目标——人设只是这个对话的组成部分。
# ================================================================
LAYERS = [
    {
        "id": 1,
        "name": "话题",
        "fields": ["title", "world_name", "genre", "era", "place", "factions"],
        "must": ["title", "genre"],
        "hint": "先定这个话题的题目（标题），比如「云溪小筑」——这是这段对话的名字。再说说题材：现代都市、古代、架空修真、校园、悬疑还是科幻？",
    },
    {
        "id": 2,
        "name": "你是谁",
        "fields": ["user_name", "user_role", "user_state"],
        "must": ["user_name", "user_role"],
        "hint": "先说好：你在这个话题里叫什么名字？这个名字代表你自己，AI 永远不会扮演它。然后说说你是谁、现在什么处境。",
    },
    {
        "id": 3,
        "name": "对话对象",
        "fields": ["name", "gender", "age", "occupation", "core_traits",
                   "speech_style", "likes", "dislikes", "redlines"],
        "must": ["name", "core_traits"],
        "hint": "接下来这个是陪你聊天的人，由 AI 扮演，不是你自己。他/她叫什么、多大、什么性格、怎么说话？",
    },
    {
        "id": 4,
        "name": "关系与互动",
        "fields": ["relation", "how_met", "interaction_style", "example_seed"],
        "must": ["relation"],
        "hint": "你跟他/她是什么关系？怎么认识的？希望他/她怎么对你？",
    },
    {
        "id": 5,
        "name": "初始人物",
        "fields": ["cast_desc"],
        "must": [],
        "hint": "这个话题里一开始还有哪些人？（家人、朋友……都是配角，一句话描述就行，AI 会展开成角色列表。）不想要别人也可以说没有。",
    },
]

# 每层给 AI 的引导提示（AI 一次只问一件事）
# v0.16.0：文案搬进 prompts/onboarding.json（onboarding.layer_1..5 /
# onboarding.guider / onboarding.preview），此 dict 仅作 default 兜底。
# 改文案请改 prompts/onboarding.json，改完秒生效，不用重启。
LAYER_PROMPTS = {
    1: """你在帮用户确定这个话题的**题目**和它的舞台。这个对话待会儿要能直接开始角色扮演。

要分清三层，别混：
- **题目**（标题）：这段对话叫什么，比如「云溪小筑」——这是标题，必须问
- **大世界**（选填）：题目所在的大背景叫什么，比如「云岚大陆」——用户提了就记，没提就跳过
- **生活地/舞台**（选填）：用户平时待在哪、那是什么地方，比如「与世隔绝的私人空间」——提了就记，没提跳过
- 题材：现代都市 / 古代 / 架空修真 / 校园 / 悬疑 / 科幻（必须问）

如果已有世界模板列表，优先建议用户"沿用某个模板"或"基于某个模板改"——
比从零描述省事得多。

已确认（不要重复问）：{{COLLECTED}}
还缺：{{MISSING}}

一次只问一件事，问完等用户回答。语气自然，像朋友聊天不像填表。
不要一次抛一长串问题。""",
    2: """你在帮用户确定他在这个新对话里的身份。

这一层决定别人会怎么对他说话，别跳过。要问的是：
- 他在这个故事里是谁（职业/身份/处境）
- 他现在是什么状态（刚搬家？失业？在养病？）

注意：这一层问的是**用户自己**，不是 AI 角色。

已确认：{{COLLECTED}}
还缺：{{MISSING}}

一次只问一件事。""",
    3: """你在帮用户确定这个对话里**主要跟他聊的那个人**是谁。

要问的是：
- 姓名（平时你会怎么叫他/她）
- 性别（关系到称呼和剧情走向，别猜，直接问）
- 年龄 / 做什么的
- 性格：3-5 个形容词，要能组合出行为
- 说话方式：他/她说话的"味道"
- 喜欢 / 讨厌什么（这条最有用——决定他能主动引出什么话题）
- 雷区：绝对不能踩的

已确认：{{COLLECTED}}
还缺：{{MISSING}}

一次只问一件事。已经问过的绝不再问。""",
    4: """你在帮用户确定**他和主要人物之间的关系**。

这一层决定整个对话的基调。要问的是：
- 两人是什么关系（恋人 / 刚认识 / 暧昧未表 / 家人 / 同事同学 / 其他）
- 怎么认识的，或者为什么现在是这个阶段
- 希望对方怎么对自己（黏人 / 独立 / 温柔但有边界…）
- 语气样例：能不能给两三组不同情绪的真实对话片段？
  （这条最出效果，但用户往往给不全——给不全就由AI 自己生成，别死磕）

注意：不要问"亲密到哪一步"这种阶段推进问题，那不是设定人物时要定的。

已确认：{{COLLECTED}}
还缺：{{MISSING}}

一次只问一件事。如果用户说"你帮我想"或"随便"，就说你来定，然后自己补上。""",
    5: """你在帮用户确定这个对话里的**其他角色**。

要问的是：
- 除了主要人物，还有谁在这个故事里？一句话描述就行
  （比如"有个开建材店的爸、话不多的妈、读大学的妹妹"）
- 有没有特别要交代的人物

AI 会把这段描述展开成角色列表让用户确认、改、删。

已确认：{{COLLECTED}}
还缺：{{MISSING}}

一次只问一件事。""",
}

# 抽取路径的铁律（v0.17.1）。正式文案在 prompts/onboarding.json 的
# onboarding.extract_rules，这里只是 default 兜底——改文案改 JSON，秒生效不重启。
_EXTRACT_RULES_DEFAULT = """
铁律（比什么都重要）：
1. 只问字段表里标注为「必须」的项。可选字段用户没提就不问，缺了由后续生成阶段补全——
   「剩下的我帮你补」这句话只能对可选字段说。
2. 禁止发明字段表以外的问题。剧情开头、玩法、节奏、氛围……统统不许问，你的问题只能来自「必须项」。
3. 只有五层全部收完（本轮返回的 next layer > 5）才能提议「看预览」。提前提议 = 严重错误。
4. 绝不重复问「已确认」清单里已有的内容，也不要用「是不是」「对吧」二次确认。
5. 角色定义（唯一标准，不许混淆）：
   - 「你」= 用户本人，不建 AI 角色卡；用户报自己的名字 → 记 user_name（第 2 层）
   - 「对话对象」= 唯一由 AI 扮演的角色，建角色卡，之后由他/她陪用户聊天；用户报他/她的名字 → 记 name（第 3 层）
   - 禁止把用户称作「主角」——「主角」在这个流程里专指 AI 扮演的对话对象
6. 用户答案很短（几个字）或明说「不用详细设定 / 这些不需要 / 你定」= 快进信号：
   停止一切细节追问，把剩余必须项最多并成一个问题问完。
7. 每轮回复最多两句：一句极短确认（行/好/成，或没有）+ 一个问题。
   禁止捧场感叹（「挺有意思的」「失敬失敬」「肯定不闷」这类一律不许出现）。
"""

_GUIDER_SYSTEM = """你是一个专业的 AI 角色扮演剧本设计师，正在帮用户从零搭建一个**新对话**。

你的任务：引导他把这个世界、他自己、主要人物、两人的关系都想清楚。
你**不是**那个角色本身，不要扮演角色、不要用角色的语气说话。

规则：
1. **一次只问一件事**。问完就停下等用户回答。
2. **绝不重复问已经确认过的内容**。已确认清单里有的，绝不再问。
3. 语气自然、像朋友聊天，不要像填表单，不要一次抛问题清单。
4. 用户说"你帮我想"、"随便"、"都行"时，你就替他定，不要追问。
5. 用户想跳过某一层就跳过，不要强求。
6. 你可以主动给建议和选项（"现代都市？还是古代？前者更好写日常"）。
7. 全程中文口语，不许用 emoji 之外的排版符号堆砌。

现在开始。{{LAYER_PROMPT}}"""

_PREVIEW_PROMPT = """你在帮用户确认他设计的这个**新对话**。

下面是引导收集到的全部信息，请生成一段**给用户看的对话设定摘要**。

要求：
- 用第二人称"你"对用户说话（"我大概想明白了：这个世界是…，你是…，主要跟你聊的是…，你们是…关系"）
- 分行罗列，每行一项，不要写成一大段
- 结尾问一句"这样对吗？有要改的直接说"
- 不要输出 JSON，不要输出解释，只输出这段摘要本身

收集到的信息：
{{COLLECTED}}

现有世界模板：{{TEMPLATES}}
"""

# ⚠️ 全项目唯一留在代码里的提示词（例外，经用户确认）：
# 内含大段 JSON 格式示例，搬进 JSON 文件会变成一行 \n 转义的字符串，没法人读。
# 占位符仍是 .format 风格。要改这条只能改这里并重启服务。
_GENERATE_PROMPT = """你在为一个 AI 角色扮演系统生成对话中主要人物的完整人设（JSON）。

用户通过对话式引导描述了这个世界、他自己、以及这个主要人物。
你要把它推导成一套完整、可用、自洽的人设。

## 关键：你必须读下面的既有上下文，让设定自洽
### 世界设定
{world}

### 已有角色（他们的名字、身份、性格）
{existing_people}

### 人物关系与称呼表（决定这个角色该怎么叫人）
{relations}

### 用户信息
{user_info}

### 用户引导收集到的设定
{summary}

## 要求
1. 语气样例 examples **必须 3 组**，覆盖不同情绪（日常闲聊 / 被安慰 / 开玩笑），
   每组要有说明为什么这么回。这是最影响"像不像人"的部分，认真写。
2. rules 要分级：L0=硬性（违反就出戏）、L1=风格、L2=倾向。
   把世界设定和既有关系里的约束**翻译成这个角色自己的规则**，
   不要照抄别人的规则。
3. 称呼必须符合关系表里的辈分，不确定就建议叫名字。
4. 性格 3-5 个形容词，要能组合出具体行为，不要"温柔善良"这种空词。
5. redlines/anti_pattern 根据世界边界和已有关系推导。
6. family_proposals：从引导的"关系网"层把提到的家人/朋友展开成角色列表，
   每项 {{"name": "名字", "relation": "跟主角人物的关系", "description": "一句话"}}。

只输出 JSON，格式：
{{
  "core_traits": ["...", "...", "..."],
  "speech_style": "说话风格一句话",
  "reply_format": "回复格式要求",
  "likes": "爱好（能主动引出话题的开关）",
  "dislikes": "讨厌/雷区",
  "summary": "一句话人设概要",
  "bond_origin": {{"relation": "跟用户的关系", "how_met": "怎么认识的", "interaction_style": "希望他/她怎么对用户"}},
  "rules": ["【L0·...】...", "【L1·...】..."],
  "rule_levels": {{"L0": "硬性：违反就完全出戏", "L1": "风格：决定说话的味道", "L2": "倾向：做得到更好"}},
  "examples": ["用户：xxx\\n角色名：xxx\\n（说明：...）"],
  "anti_pattern": "禁忌清单",
  "family_proposals": [{{"name": "名字", "relation": "关系", "description": "一句话"}}]
}}"""


# ================================================================
# 上下文收集（喂给 AI 的既有资料）
# ---------------------------------------------------------------
# v0.17.5 世界隔离：_collect_context 支持按世界归属（world_scope）过滤——
# 生成新角色时 AI 只看得见本世界的角色和关系，不会把别的世界的人设
# 搅进来（2026-10-21 用户拍板：每个世界模板是独立单位，角色不串门）。
# world_scope 传 None = 不过滤（世界还没定型的早期引导层用）。
# ================================================================

def _draft_world_scope(draft, collected=None):
    """当前草稿所属世界的归属键（跟 finalize 的口径一致）。

    显式模板 id > L1 大世界名 > L1 题目。都还没定（刚开头）返回 None，
    调用方看到 None 就不过滤——早期层还没法按世界隔离。
    """
    draft = draft or {}
    collected = collected if collected is not None else (draft.get("collected") or {})
    L1 = collected.get("L1") or {}
    return ((draft.get("world_template_id") or "")
            or (L1.get("world_name") or "").strip()
            or (L1.get("title") or "").strip()
            or None)


def _collect_context(world_scope=None):
    """把库里现有的角色/关系/称呼整理成给 AI 参考的文本。

    v0.17.5：传 world_scope 时只收集该世界的角色与关系
    （None = 不过滤；"" = 未归属世界那一批）。
    """
    parts = {}

    # 世界模板（列表本身不串门——只是给 AI 参考有哪些世界可选）
    tpls = ps.list_world_templates()
    parts["templates"] = "\n".join(
        f"- {t['name']}：{t.get('summary', '')}" for t in tpls) or "（还没有模板）"

    # 已有角色人设（按世界过滤）
    people = ps.list_personas() if world_scope is None \
        else ps.list_personas_by_scope(world_scope)
    if people:
        lines = []
        for p in people:
            tag = "（主角）" if p.get("is_protagonist") else ""
            # 世界归属标签：不过滤时跨世界可能有同名角色（云溪小筑的夏雪 ≠
            # 旧世界的夏雪），不标出来生成时必然混淆；按世界过滤后都是本世
            # 界的角色，不用标
            scope = p.get("world_scope") or ""
            stag = f"（世界：{scope}）" if (scope and world_scope is None) else ""
            lines.append(f"- {p['name']}{tag}{stag}：{p.get('summary') or '、'.join(p.get('core_traits') or [])}")
        parts["existing_people"] = "\n".join(lines)
    elif world_scope:
        parts["existing_people"] = "（这个世界还没有角色，全新开始）"
    else:
        parts["existing_people"] = "（还没有角色）"

    # 关系与称呼（决定辈分怎么叫）——只收本世界的（v0.17.5）
    try:
        rels = db.connect().execute(
            """SELECT p1.name AS a, r.relation_type AS t, p2.name AS b,
                      r.call_a_to_b AS a2b, r.call_b_to_a AS b2a,
                      p1.persona_id AS pa, p2.persona_id AS pb
               FROM relations r
               JOIN people p1 ON p1.id = r.person_a_id
               JOIN people p2 ON p2.id = r.person_b_id"""
        ).fetchall()

        def _row_scope(pid):
            # 人物卡的世界归属走它绑的人格；没绑人格的算未归属（""）
            if not pid:
                return ""
            p = ps.get_persona(pid)
            return (p or {}).get("world_scope") or ""

        lines = []
        for r in rels:
            if world_scope is not None:
                # 两端都必须属于本世界（防止别世界的关系搅进来）
                if _row_scope(r["pa"]) != (world_scope or "") \
                        or _row_scope(r["pb"]) != (world_scope or ""):
                    continue
            lines.append(f"- {r['a']} -[{r['t']}]- {r['b']}"
                         + (f"（{r['a']}叫{r['b']}：{r['a2b']}）" if r["a2b"] else "")
                         + (f"（{r['b']}叫{r['a']}：{r['b2a']}）" if r["b2a"] else ""))
        if lines:
            parts["relations"] = "\n".join(lines)
        elif world_scope:
            parts["relations"] = "（本世界还没有关系）"
        else:
            parts["relations"] = "（关系表为空）"
    except Exception as e:
        logger.warning("读关系表失败: %s", e)
        parts["relations"] = "（关系表为空）"

    # 用户信息（称呼按世界取：本世界没主角时沿用全局主角的称呼——
    # 用户还是同一个人，名字默认带过来；要改在本世界 L2 说一声就行）
    pro = (ps.get_protagonist(world_scope) if world_scope else None) \
        or ps.active_persona() or {}
    parts["user_info"] = (
        f"用户在这个世界里的称呼：{pro.get('user_name') or '未设定'}"
        if pro else "用户信息未设定")

    return parts


def _world_text_for(tpl_id):
    tpl = ps.get_world_template(tpl_id)
    return ps.world_template_to_text(tpl) if tpl else ""


# ================================================================
# 覆盖度判定
# ================================================================

def missing_fields(layer, collected):
    """这一层还缺什么（只算 must 里的必填项 + 关键可选项）"""
    L = next((l for l in LAYERS if l["id"] == layer), None)
    if not L:
        return []
    got = (collected or {}).get(f"L{layer}", {})
    return [f for f in L["must"] if not got.get(f)]


def current_layer(draft):
    """当前该问第几层（1-5）；都齐了返回 6（=准备预览）"""
    collected = (draft or {}).get("collected") or {}
    for l in LAYERS:
        if missing_fields(l["id"], collected):
            return l["id"]
    return 6


def _format_collected(collected):
    """把收集到的信息渲染成给 AI 看的文本"""
    lines = []
    for l in LAYERS:
        got = (collected or {}).get(f"L{l['id']}", {})
        if got:
            lines.append(f"【第{l['id']}层 {l['name']}】")
            for k, v in got.items():
                if v:
                    vv = "、".join(str(x) for x in v) if isinstance(v, list) else str(v)
                    lines.append(f"  - {k}: {vv}")
    return "\n".join(lines) or "（还没开始问）"


# ================================================================
# 对话接口
# ================================================================

def _llm_chat(mode):
    from routes.custom_llm import get_llm_chat_func
    return get_llm_chat_func(mode or "deepseek")


def _json_from(text):
    """从模型输出里抠出 JSON（模型常带markdown 围栏 / 尾逗号 / 中文引号）"""
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    raw = m.group(0)
    # 三种脏输出依次尝试：直接解 → 去尾逗号 → 去中文引号
    for fixer in (lambda s: s,
                  lambda s: re.sub(r",\s*([}\]])", r"\1", s),
                  lambda s: re.sub(r",\s*([}\]])", r"\1",
                                    s.replace("“", '"').replace("”", '"')
                                     .replace("‘", "'").replace("’", "'"))):
        try:
            return json.loads(fixer(raw))
        except Exception:
            continue
    return None


def _retire_active_draft():
    """把进行中的旧草稿标记为 done（不删，留个记录）。

    用户从世界模板点「用这个模板开对话」时，旧草稿继续赖在 'active'
    会把下一次无参 start 又带回去——必须先把它请出进行中队列。
    """
    old = ps.get_active_draft()
    if not old:
        return
    ps.save_draft(old["id"], old.get("collected") or {},
                  old.get("current_layer") or 1, old.get("history") or [],
                  target_name=old.get("target_name", ""),
                  depth=old.get("depth", "full"),
                  world_template_id=old.get("world_template_id", ""),
                  status="done")


def start(draft_id=None, depth="full", world_template_id="", mode="deepseek",
          force_new=False):
    """开始（或继续）一次引导。返回 {reply, draft_id, layer, done}

    force_new=True：无视进行中的旧草稿，按给定 world_template_id 开新的。
    用户在世界模板上点「开对话」时用——他要的是套这个模板，不是续旧草稿。
    """
    if draft_id:
        draft = ps.get_draft(draft_id)
    else:
        if force_new:
            _retire_active_draft()
            draft = None
        else:
            #续接进行中的草稿，或开新的
            draft = ps.get_active_draft()
    if not draft:
        draft_id = ps.save_draft(None, {}, 1, [], depth=depth,
                                 world_template_id=world_template_id)
        draft = ps.get_draft(draft_id)
    else:
        draft_id = draft["id"]

    layer = current_layer(draft)
    chat = _llm_chat(mode)
    if not chat:
        return {"reply": "LLM 未配置，无法引导。", "draft_id": draft_id,
                "layer": layer, "done": False}

    if layer > 5:
        return {"reply": "信息已经收集得差不多了，要我生成预览吗？（说「预览」或「完成」）",
                "draft_id": draft_id, "layer": layer, "done": False, "ready_preview": True}

    L = next(l for l in LAYERS if l["id"] == layer)
    collected = draft.get("collected") or {}
    # 有模板就直接沿用，不用问世界（题目仍要问——那是这段对话的标题）
    if layer == 1 and draft.get("world_template_id"):
        collected.setdefault("L1", {})
        collected["L1"]["template_id"] = draft["world_template_id"]
        collected["L1"]["genre"] = "沿用所选模板"
        # 模板名顺延为大世界名（题目还得用户自己起）
        try:
            _tpl = next((t for t in ps.list_world_templates()
                         if t.get("id") == draft["world_template_id"]), None)
            if _tpl and _tpl.get("name"):
                collected["L1"].setdefault("world_name", _tpl["name"])
        except Exception:
            pass
        # ⚠️ world_template_id 必须原样带回：save_draft 的默认值是 ""，
        # 漏传就会把草稿的模板绑定清空，落库时世界又掉回主角的世界。
        ps.save_draft(draft_id, collected, 1, draft.get("history") or [],
                      world_template_id=draft.get("world_template_id", ""))

    # v0.17.5：按本草稿的世界收集上下文（模板世界只看见该世界的角色；
    # 还没定世界时不过滤）。生成/提问都不串门
    ctx = _collect_context(_draft_world_scope(draft, collected))
    lp = get_prompt(f"onboarding.layer_{layer}",
                    default=LAYER_PROMPTS.get(layer, ""),
                    COLLECTED=_format_collected(collected),
                    MISSING="、".join(missing_fields(layer, collected)) or "（本层关键项已齐，可补充）")
    sys_prompt = get_prompt("onboarding.guider", default=_GUIDER_SYSTEM,
                            LAYER_PROMPT=lp, TEMPLATES=ctx["templates"],
                            EXISTING_PEOPLE=ctx["existing_people"])
    reply = chat([{"role": "system", "content": sys_prompt},
                  {"role": "user", "content": L["hint"]}])

    hist = list(draft.get("history") or [])
    ps.save_draft(draft_id, collected, layer, hist, target_name="",
                  depth=draft.get("depth", depth),
                  world_template_id=draft.get("world_template_id", world_template_id))
    return {"reply": reply, "draft_id": draft_id, "layer": layer,
            "layer_name": L["name"], "done": False}


def reply(draft_id, text, mode="deepseek"):
    """用户在引导里回了一句话。返回 {reply, done, layer, preview?}"""
    draft = ps.get_draft(draft_id)
    if not draft:
        return {"reply": "引导草稿不在了，说「引导角色」重新开始。", "done": False}

    collected = draft.get("collected") or {}
    layer = draft.get("current_layer") or 1
    hist = list(draft.get("history") or [])

    # 用户主动喊停/要预览
    if text.strip() in ("预览", "看看", "完成", "就这样"):
        return preview(draft_id, mode=mode)

    chat = _llm_chat(mode)
    if not chat:
        return {"reply": "LLM 未配置。", "done": False}

    # v0.17.5：按本草稿的世界收集上下文——已有角色只给本世界的看，不串门
    ctx = _collect_context(_draft_world_scope(draft, collected))

    # 让 AI 同时做两件事：抽取字段 + 给下一个问题
    layer = current_layer(draft)
    L = next((l for l in LAYERS if l["id"] == layer), None) if layer <= 5 else None
    lp = get_prompt(f"onboarding.layer_{layer}",
                    default=LAYER_PROMPTS.get(layer, ""),
                    COLLECTED=_format_collected(collected),
                    MISSING="、".join(missing_fields(layer, collected)) or "（已齐，可补充）")

    extract_sys = (
        "你在帮用户设计 AI 角色。用户刚回答了一句话，你要做两件事：\n"
        "1. 从这句话里抽取信息，填进下面的字段表（没提到的字段不要瞎填）\n"
        "2. 简短确认，然后问下一件事\n"
        f"\n当前层：第{layer}层{L['name'] if L else ''}\n"
        f"本层字段：{L['fields'] if L else '（已全部收齐）'}\n"
        f"其中必须问到才推进的：{L['must'] if L else '（已全部收齐）'}\n"
        f"已确认（不要重复问）：\n{_format_collected(collected)}\n"
        f"现有世界模板：\n{ctx['templates']}\n"
        f"已有角色：\n{ctx['existing_people']}\n"
        + get_prompt("onboarding.extract_rules",
                     default=_EXTRACT_RULES_DEFAULT)
        + f"\n本层详细指引：\n{lp}\n"
        "\n输出 JSON：\n"
        '{"extracted": {"字段名": "值", ...}, "reply": "给用户看的回应+下一个问题"}\n'
        "\n注意：extracted 只放用户这句话里明确提到的信息；"
        "字段名用上面「本层字段」里的原名。"
    )

    messages = [{"role": "system", "content": extract_sys}]
    for h in hist[-8:]:
        messages.append(h)
    messages.append({"role": "user", "content": text})

    raw = chat(messages)
    data = _json_from(raw)
    if not data:
        # 模型没给 JSON，就当普通回应，不推进
        hist.append({"role": "user", "content": text})
        # world_template_id 也要原样带回，别把模板绑定丢了（同 start 里的坑）
        ps.save_draft(draft_id, collected, layer, hist,
                      world_template_id=draft.get("world_template_id", ""))
        return {"reply": raw or "（没听清，再说一次？）", "done": False, "layer": layer}

    got = data.get("extracted") or {}
    if got and layer <= 5:
        collected.setdefault(f"L{layer}", {})
        collected[f"L{layer}"].update(got)
    hist.append({"role": "user", "content": text})
    hist.append({"role": "assistant", "content": data.get("reply", "")})

    nxt = current_layer({"collected": collected})
    ps.save_draft(draft_id, collected, nxt, hist,
                  target_name=collected.get("L3", {}).get("name", ""),
                  depth=draft.get("depth", "full"),
                  world_template_id=draft.get("world_template_id", ""))

    if nxt > 5:
        return {"reply": data.get("reply", "") + "\n\n（信息收齐了，说「预览」看我整理的角色设定对不对）",
                "done": True, "layer": nxt, "ready_preview": True}

    return {"reply": data.get("reply", ""), "done": False, "layer": nxt}


def preview(draft_id, mode="deepseek"):
    """生成角色预览让用户确认"""
    draft = ps.get_draft(draft_id)
    if not draft:
        return {"reply": "草稿不在了。", "done": False}
    chat = _llm_chat(mode)
    collected = draft.get("collected") or {}
    ctx = _collect_context(_draft_world_scope(draft, collected))
    msg = get_prompt("onboarding.preview", default=_PREVIEW_PROMPT,
                     COLLECTED=_format_collected(collected),
                     TEMPLATES=ctx["templates"])
    text = chat([{"role": "system", "content": msg}]) if chat else _format_collected(collected)
    return {"reply": text, "done": False, "preview": True, "collected": collected}


# ================================================================
# 落库：草稿 → 一个可玩的对话
# ---------------------------------------------------------------
# 一次引导的产物不是"一个孤立人设"，而是：
#   1. 主要人物的人格（personas 表，is_protagonist=1 = 本世界门面）
#   2. 一个绑定这个人格的对话（sessions 表）——出现在引导页，点一下就能开聊
# v0.17.5：主角唯一按世界判（world_scope），建新世界的伴侣只当该世界的主角，
# 不会再抢旧世界的主角位；同世界再建第二个会顶替门面，但老会话绑的是
# 自己的角色，聊天/切回都不受影响。
# ================================================================

def _default_world_template_id():
    """没显式选世界时，继承当前主角的世界——保证新角色不孤立。"""
    pro = ps.active_persona() or {}
    return pro.get("world_template_id") or ""


def _scope_label(scope):
    """世界归属键 → 人话（模板 id 显示模板名，其余原样）"""
    if not scope:
        return "默认世界"
    try:
        for t in ps.list_world_templates():
            if t.get("id") == scope:
                return t.get("name") or scope
    except Exception:
        pass
    return scope


def finalize(draft_id, mode="deepseek", save_as_template=False):
    """确认预览后落库。返回 {ok, persona_id, name, session_id, reply}"""
    draft = ps.get_draft(draft_id)
    if not draft:
        return {"ok": False, "reply": "草稿不在了。"}
    collected = draft.get("collected") or {}
    depth = draft.get("depth", "full")
    L1, L2, L3, L4 = (collected.get(f"L{i}", {}) for i in range(1, 5))
    L5 = collected.get("L5", {})

    name = (L3.get("name") or draft.get("target_name") or "").strip()

    # 没起名字就别落库：硬起个"未命名角色"只会污染角色库
    if not name:
        return {"ok": False, "reply": "还没给主要人物起名字，先告诉我他/她叫什么。"}

    # 题目（标题）≠ 世界名：题目是这段对话叫什么（比如「云溪小筑」），
    # 世界名是它所在的大背景（比如「云岚大陆」）——生活地/舞台由 place 描述。
    topic_title = (L1.get("title") or "").strip()
    world_name = (L1.get("world_name") or "").strip()

    # 世界继承：草稿没选就跟着当前主角的世界，不留空
    world_tpl_id = draft.get("world_template_id") or _default_world_template_id()

    # 世界归属（v0.17.4）：每个世界模板/话题是独立单位，角色跟着世界走。
    # 归属键优先级：显式选的世界模板 id > 大世界名 > 题目。
    # ⚠️ 绝不继承主角的世界当归属——没选模板就是全新世界，独立存在。
    world_scope = draft.get("world_template_id") or world_name or topic_title

    # 同名查重（按世界归属判，2026-10-21：全局查重会拦住"新世界建同名角色"）
    dup = db.connect().execute(
        "SELECT id FROM personas WHERE name = ? AND world_scope = ?",
        (name, world_scope)).fetchone()
    if dup:
        return {"ok": False, "persona_id": dup[0], "duplicated": True,
                "reply": f"「{_scope_label(world_scope)}」里已经有一个「{name}」了，不能重复建。"
                         f"想改他/她的人设的话去角色库里编辑；想在别的世界建同名的他/她，"
                         f"回去把题目或大世界改一下就行。"}

    # 防呆：新角色不能和用户自己重名。引导建的角色会被设成 AI 扮演对象
    #（set_actor），若名字=用户名，AI 就会把自己演成用户，主次颠倒
    #（2026-10-21 实测翻车：用户输入「林风」= 自己的名字，会话变成"和林风的对话"）
    # 名字锚点：L2 现在会显式收集「你的名字」（user_name），优先用它校验；
    # 如果用户这轮改了名字，同步更新主角人设的 user_name
    _my_name = ""
    try:
        _my_name = (ps.active_persona() or {}).get("user_name") or ""
    except Exception:
        pass
    _new_uname = (L2.get("user_name") or "").strip()
    _forbidden_names = {n for n in (_my_name, _new_uname) if n}
    if name in _forbidden_names:
        return {"ok": False, "reply": (
            f"「{name}」是你自己的名字呀——这个流程建的是陪你聊的角色，不能是你本人。"
            f"回去给角色起个别的名字；想以「{name}」的身份聊天，"
            f"直接开对话就行，不用新建角色。")}
    if _new_uname and _new_uname != _my_name:
        # v0.17.5：名字锚点按世界更新——只改本世界的主角（全局主角仅当它同属
        # 该世界或还没归属世界时才跟着改）。以前一把梭改全世界，新世界起的
        # 名字会污染旧世界（用户在世界B改名，世界A的称呼也跟着变）。
        try:
            _targets = []
            _pro = ps.active_persona() or {}
            if _pro.get("id") and _pro.get("id") != "file_fallback" \
                    and (_pro.get("world_scope") or "") in (world_scope, ""):
                _targets.append(_pro["id"])
            for _p in ps.list_personas_by_scope(world_scope):
                if _p.get("is_protagonist") and _p.get("id") \
                        and _p["id"] != "file_fallback" and _p["id"] not in _targets:
                    _targets.append(_p["id"])
            for _tid in _targets:
                _pa_full = ps.get_persona(_tid)
                if _pa_full:
                    _pa_full["user_name"] = _new_uname
                    ps.save_persona(_pa_full, _tid)
            _my_name = _new_uname
            if _targets:
                logger.info("用户名字锚点已更新: → %s（世界：%s）",
                            _new_uname, _scope_label(world_scope))
        except Exception as _ue:
            logger.warning("更新 user_name 失败: %s", _ue)

    chat = _llm_chat(mode)
    # v0.17.5：按本世界的归属收集上下文——生成只参考本世界的角色与关系
    ctx = _collect_context(world_scope)

    #用户说的都要保留；AI 补的全套人设
    if chat:
        # 世界参考：显式选了模板才用模板文本；没选 = 全新世界，
        # 用用户自己填的题目/大世界/题材/生活地拼——绝不把主角的世界
        # （比如现代家庭）硬塞给一个修仙新世界（v0.17.4 用户拍板：世界独立）
        _world_ref = _world_text_for(world_tpl_id) if draft.get("world_template_id") else ""
        if not _world_ref:
            _world_ref = (f"题目：{topic_title or '（未命名）'}；"
                          f"大世界：{world_name or '（未设定）'}；"
                          f"题材：{L1.get('genre', '')}；"
                          f"生活地：{L1.get('place', '')}").strip()
        gen_text = chat([{"role": "system", "content": _GENERATE_PROMPT.format(
            world=_world_ref,
            existing_people=ctx["existing_people"],
            relations=ctx["relations"],
            user_info=ctx["user_info"],
            summary=_format_collected(collected)[:1500],
        )}])
        gen = _json_from(gen_text) or {}
    else:
        gen = {}

    def _pick(*keys, default=""):
        for k in keys:
            v = gen.get(k)
            if v:
                return v
            u = collected.get("L3", {}).get(k)
            if u:
                return u
        return default

    persona = {
        "name": name,
        # v0.17.5：建成的伴侣即本世界的门面（主角唯一按世界判，不抢别世界的位）。
        # 同世界再建第二个伴侣时会顶替主角位——老会话绑的是自己的角色，
        # 聊天/切回都不受影响（active_persona 优先取会话绑定的角色）
        "is_protagonist": 1,
        "depth": depth,
        "world_template_id": draft.get("world_template_id", ""),
        "world_scope": world_scope,         # v0.17.4：世界归属（重名检查按它判）
        "summary": gen.get("summary") or L3.get("summary", ""),
        "age": int(L3.get("age") or 0) if str(L3.get("age", "")).isdigit() else 0,
        "occupation": L3.get("occupation", ""),
        "core_traits": gen.get("core_traits") or L3.get("core_traits") or [],
        "speech_style": gen.get("speech_style") or L3.get("speech_style", ""),
        "reply_format": gen.get("reply_format", ""),
        "likes": _pick("likes"),
        "dislikes": _pick("dislikes"),
        # bond_origin 记的是"这段关系怎么来的"——对话的起点，不是人物档案
        "bond_origin": {"relation": L4.get("relation", ""),
                        "how_met": L4.get("how_met", ""),
                        "interaction_style": L4.get("interaction_style", ""),
                        "user_role": L2.get("user_role", ""),
                        "user_state": L2.get("user_state", "")},
        "intimacy": {"interaction_style": L4.get("interaction_style", ""),
                     "example_seed": L4.get("example_seed", "")},
        "rules": gen.get("rules") or [],
        "rule_levels": gen.get("rule_levels") or {
            "L0": "硬性：违反就完全出戏，任何时候都不能破",
            "L1": "风格：决定说话的味道，尽力遵守",
            "L2": "倾向：做得到更好，做不到也无妨"},
        "examples": gen.get("examples") or [],
        "anti_pattern": gen.get("anti_pattern") or L3.get("redlines", ""),
        # user_name：本轮回收集到的名字优先；没填就沿用当前世界的称呼
        # （v0.17.5 前是硬取全局主角的 user_name，会把别的世界的称呼带过来）
        "user_name": (L2.get("user_name") or "").strip() or _my_name or L2.get("user_role", ""),
        "notes": f"由引导生成（草稿 {draft_id}，{depth} 档）",
    }
    if isinstance(persona["core_traits"], str):
        persona["core_traits"] = [t.strip() for t in re.split(r"[、,，]", persona["core_traits"]) if t.strip()]

    persona_id = ps.save_persona(persona)

    # 登记进 people 表（视角切换要靠它）。
    # 注意：如果已有同名行是"用户本人"这类非 AI 角色，绝不能往上绑人格——
    # 否则用户自己的人物卡会被误当成 AI 角色。
    try:
        row = db.connect().execute(
            "SELECT id, role FROM people WHERE name = ?", (name,)).fetchone()
        if row and (row["role"] or "") in ("", "user", "self"):
            logger.info("people 表已有同名非 AI 角色「%s」，跳过人格绑定", name)
        elif row:
            ps.bind_persona_to_people(persona_id, name)
        else:
            c = db.connect()
            c.execute(
                "INSERT INTO people (name, age, occupation, personality, speech_style,"
                " likes, dislikes, description, session_id, persona_id, role)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, persona.get("age") or 0, persona.get("occupation", ""),
                 "、".join(persona.get("core_traits") or []), persona.get("speech_style", ""),
                 persona.get("likes", ""), persona.get("dislikes", ""),
                 persona.get("summary", ""), "", persona_id,
                 "companion"))
            c.commit()
    except Exception as e:
        logger.warning("登记人物卡失败: %s", e)

    # 世界设定存成模板（用户要的：确定下来能沉淀成模板）
    tpl_msg = ""
    if save_as_template:
        # v0.17.5：把自己这个世界的角色一起收进模板（整包），
        # 新人格的模板引用也指过去——模板页上认得出她
        tpl_id = _save_world_as_template(collected, name, world_scope)
        try:
            _pa_now = ps.get_persona(persona_id)
            if _pa_now and _pa_now.get("world_template_id") != tpl_id:
                _pa_now["world_template_id"] = tpl_id
                ps.save_persona(_pa_now, persona_id)
        except Exception as _te:
            logger.warning("回写模板引用失败: %s", _te)
        tpl_msg = (f"\n世界设定已存成模板（{tpl_id}），这个世界里的角色都归它管；"
                   f"以后新建对话可以直接选它。")

    # 关键：直接落成一个可玩的对话。不自动跳转，但它已经躺在引导页等你点。
    session_id = db.new_session_id()
    _title = topic_title or f"和{name}的对话"
    db.create_session(session_id, title=_title, title_locked=True)
    db.create_message_table(session_id)
    ps.set_session_persona(session_id, persona_id)
    # 新人物不是主角，必须把会话视角切到他/她——否则打开这个对话，
    # AI 还是按主角（夏雪）说话，等于白建。
    db.set_actor(session_id, name)

    ps.save_draft(draft_id, collected, 6, draft.get("history") or [],
                  target_name=name, depth=depth,
                  world_template_id=world_tpl_id,
                  status="done")
    try:
        import prompts
        prompts.reload_anchor()
    except Exception:
        pass

    return {"ok": True, "persona_id": persona_id, "name": name,
            "session_id": session_id,
            "reply": f"新世界建好了：「{_title}」\n"
                     f"{name} 的人设（{len(persona['rules'])} 条规则、"
                     f"{len(persona['examples'])} 组语气样例）已经就位。\n"
                     f"回引导页点这张卡片就能开始聊，不用再做别的。{tpl_msg}"}


def _save_world_as_template(collected, name, old_scope=""):
    """把这次引导出的世界设定沉淀成可复用模板

    模板名优先用用户起的世界名（L1.world_name），不再生成「XX的世界」这种机器名。

    v0.17.5 模板即整包：存成模板后，把这个世界的角色全部改归属到新模板
    （world_scope 重指向 tpl_id）——模板从此不只是「设定文本」，而是带着
    整套角色阵容的一个包；角色本身不动，只是换了归属键。
    """
    L1 = collected.get("L1", {})
    L5 = collected.get("L5", {})
    _world_name = (L1.get("world_name") or "").strip() \
        or (L1.get("title") or "").strip() or f"{name}的世界"
    tpl_id = ps.save_world_template({
        "name": _world_name,
        "summary": L1.get("summary") or f"由对话「{name}」的世界观沉淀而来",
        "genre": L1.get("genre", ""),
        "era": L1.get("era", ""),
        "factions": L1.get("factions") or L5.get("cast_desc", ""),
        "boundaries": L1.get("boundaries", []),
        "customs": L1.get("customs", []),
        "user_nick": (ps.active_persona() or {}).get("user_name", ""),
    })
    # 整包化：旗下角色改归属到新模板
    n = ps.repoint_world_scope(old_scope, tpl_id)
    if n:
        logger.info("世界「%s」存成模板 %s，%d 个角色已改归属到该模板",
                    _world_name, tpl_id, n)
    return tpl_id


def quick_create(name, summary, world_template_id="", mode="deepseek"):
    """一句话快速建对话（lite 档的快捷入口，UI 用）

    完整走一遍 AI 生成，不用逐项问答。产物跟引导一样：人设 + 一个可玩的对话。
    """
    draft_id = ps.save_draft(None, {"L3": {"name": name, "summary": summary}},
                             1, [], depth="lite",
                             world_template_id=world_template_id)
    return finalize(draft_id, mode=mode)


def abort(draft_id):
    ps.delete_draft(draft_id)
    return {"ok": True, "reply": "引导已取消。"}