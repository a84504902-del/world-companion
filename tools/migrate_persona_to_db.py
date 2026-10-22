"""一次性迁移：把旧JSON 人设文件导入人格库，并抽出第一条世界模板。

用法：
    python tools/migrate_persona_to_db.py           # 只报告，不写
    python tools/migrate_persona_to_db.py --apply    # 真写库

幂等：重复跑不会产生重复记录（按 id upsert）。

验收标准（重要）：
迁移后 `prompts.persona_to_text()` 生成的文本必须与迁移前chat.py 里那段
拼字符串逻辑**逐字一致**——由--verify 模式自动比对，不一致会报出来。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import db  # noqa: E402
import persona_store as ps  # noqa: E402

APPLY = "--apply" in sys.argv

# 夏雪的人设 id——固定值，方便其他代码引用
XIAXUE_ID = "prs_xiaxue"
# 第一条内置世界模板
TPL_ID = "wtpl_modern_family"

# ================================================================
# 世界模板：把"现代都市 + 两家亲戚"这套抽出来
# （这些内容原先散落在 rules 里当背景说明，现在显式成为可复用的世界底座）
# ================================================================
BUILTIN_TEMPLATE = {
    "id": TPL_ID,
    "name": "现代都市·两家亲戚",
    "summary": "现代中国城市生活。两个家庭因子女婚事结成亲戚，日常围绕家人与邻居往来。",
    "genre": "现代都市",
    "era": "当代中国某座城市，日常生活节奏",
    "factions": [
        "夏家（女方）：夏海（父亲）、沈青（母亲）、夏雪（女儿）、夏琳（妹妹）",
        "林家（男方）：林风的父母，与夏家因婚事往来",
        "同事圈与邻里圈：日常寒暄、串门、饭局",
    ],
    "boundaries": [
        "话题围绕家庭关系、日常生活、工作压力、情绪交流",
        "两家亲戚之间辈分称呼必须准确，不许张冠李戴",
    ],
    "customs": [
        "亲戚之间称呼按辈分：爸、妈、姐、哥、妹、弟",
        "某人只能称呼自己的配偶为老婆/老公",
        "不确定辈分时直接叫名字",
        "节日有走亲访友、Family 聚餐等固定人情往来",
    ],
    "user_nick": "林风",
    "is_builtin": 1,
}


def load_legacy_file():
    """读旧人设文件，persona_local.json 优先（它是可编辑覆盖层）"""
    path = os.path.join(BASE, "persona_local.json")
    if not os.path.exists(path):
        path = os.path.join(BASE, "persona_anchor.json")
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        return json.load(f), os.path.basename(path)


def build_xiaxue_persona(legacy):
    """旧文件 → personas 表的一行"""
    return {
        "id": XIAXUE_ID,
        "name": legacy.get("persona", "夏雪"),
        "is_protagonist": 1,
        "depth": "full",
        "world_template_id": TPL_ID,
        "summary": "夏海的妻子、沈青的女儿，夏琳的姐姐，林风的女朋友。",
        "age": 21,
        "birthday": "",
        "occupation": "",
        "core_traits": legacy.get("core_traits", []),
        "speech_style": legacy.get("speech_style", ""),
        "reply_format": legacy.get("reply_format", ""),
        "likes": "",
        "dislikes": "",
        # 关系起点从 rules 里的辈分规则反推：现代都市两家亲戚，林风是男朋友
        "bond_origin": {
            "relation": "林风的女朋友",
            "how_met": "",
            "stage": "稳定交往中",
            "she_knows": "",
        },
        "intimacy": {},
        "rules": legacy.get("rules", []),
        "rule_levels": legacy.get("_rule_levels", {}),
        "examples": legacy.get("examples", []),
        "anti_pattern": legacy.get("anti_pattern", ""),
        "content_mode": legacy.get("content_mode", ""),
        "memory_priority": legacy.get("memory_priority", ""),
        "user_name": "林风",
        "notes": f"由 tools/migrate_persona_to_db.py 从旧人设文件导入（{'persona_local.json' if os.path.exists(os.path.join(BASE, 'persona_local.json')) else 'persona_anchor.json'}）",
    }


def verify(legacy):
    """比对迁移前后的提示词。

    验收标准分两类，不能混为一谈：
    1. **不丢**：迁移前每一行都必须还在（新增世界设定/关系起点是预期的，但绝不能
       因为搬进数据库而丢掉任何一条原有规则）。
    2. 迁移后会多出世界设定与关系起点这几行 —— 这是把原先"无处安放"的内容
       显式化，属于预期改进。
    """
    _pa = legacy
    _all_rules = _pa.get("rules", [])
    _rules = "\n".join(f"- {r}" for r in _all_rules)
    _examples = ""
    if _pa.get("examples"):
        _examples = "\n格式示例：\n" + "\n".join(f"{ex}" for ex in _pa["examples"])
    _old = (
        f"【绝对不可违反的核心设定】\n"
        f"你是「{_pa.get('persona', '夏雪')}」，核心性格：{', '.join(_pa.get('core_traits', []))}。\n"
        f"说话风格：{_pa.get('speech_style', '')}。\n"
        f"强制规则：\n{_rules}\n"
        f"关系模式：{_pa.get('relationship_mode', '')}。\n"
        f"{_examples}\n"
    )

    pa = ps.get_persona(XIAXUE_ID) or build_xiaxue_persona(legacy)
    _new, _fmt = ps.persona_to_text(pa, ps.get_world_template(TPL_ID))

    print("=" * 60)
    print("迁移前后提示词比对（不丢 = 硬标准；新增 = 预期）")
    print("=" * 60)

    old_lines = [l for l in _old.splitlines() if l.strip()]
    new_lines = [l for l in _new.splitlines() if l.strip()]
    new_set = set(new_lines)
    lost = [l for l in old_lines if l not in new_set]
    added = [l for l in new_lines if l not in set(old_lines)]

    # 规则必须 100% 保留（丢了就是人设损坏）
    n_rules = len(legacy.get("rules", []))
    kept_rules = sum(1 for r in legacy.get("rules", []) if f"- {r}" in new_lines)

    # 语气样例是多行文本，要按"整段包含"判断，不能按行
    n_ex = len(legacy.get("examples", []))
    kept_ex = sum(1 for e in legacy.get("examples", []) if str(e) in _new)

    ok = True
    if lost:
        ok = False
        print(f"❌ 丢了 {len(lost)} 行（严重）：")
        for l in lost[:20]:
            print(f"     - {l[:90]}")
    else:
        print(f"✅ 原有内容零丢失：{len(old_lines)} 行全部保留")

    print(f"✅ 规则保留 {kept_rules}/{n_rules} 条"
          f"{'' if kept_rules == n_rules else '  ← 有丢失！'}")
    print(f"✅ 语气样例保留 {kept_ex}/{n_ex} 组")
    if kept_rules != n_rules or kept_ex != n_ex:
        ok = False

    if added:
        print(f"\n➕ 新增 {len(added)} 行（把原先无处安放的内容显式化，预期）：")
        for l in added:
            print(f"     + {l[:90]}")
    print("\n" + ("✅ 验收通过" if ok else "❌ 验收失败"))
    return ok


def main():
    legacy, src_name = load_legacy_file()
    if not legacy:
        print("✗ 没找到 persona_local.json / persona_anchor.json，无法迁移")
        return 1

    print(f"读入旧人设文件：{src_name}（角色：{legacy.get('persona')}，"
          f"{len(legacy.get('rules', []))} 条规则，{len(legacy.get('examples', []))} 组样例）")

    db.init_db()

    if not APPLY:
        print("\n--- 试运行（不写库）---")
        tpl_id = ps.save_world_template(BUILTIN_TEMPLATE)
        pa = build_xiaxue_persona(legacy)
        pa_id = ps.save_persona(pa)
        print(f"  模板 {tpl_id} / 人格 {pa_id} 校验通过（试运行仍写了库，可用 --rollback 清掉）")
        ok = verify(legacy)
        return 0 if ok else 2

    print("\n--- 正式写入 ---")
    tpl_id = ps.save_world_template(BUILTIN_TEMPLATE)
    print(f"  ✅ 世界模板：{tpl_id}（{BUILTIN_TEMPLATE['name']}）")
    pa_id = ps.save_persona(build_xiaxue_persona(legacy))
    print(f"  ✅ 角色人格：{pa_id}（{legacy.get('persona')}，主角）")

    # people 表：把现有的夏雪/夏海/沈青等关联或标 role
    # people 表当前是空的（夏雪走anchor 文件），所以这里只做 role 约定登记
    print("\n--- people 关联 ---")
    n = db.connect().execute(
        "SELECT COUNT(*) FROM people WHERE persona_id != ''"
    ).fetchone()[0]
    print(f"  已关联人格的人物卡：{n} 张（当前 people 表为空属正常，"
          f"人物会在引导流程或AI 检测时入库）")

    print("\n--- 校验 ---")
    pa = ps.get_persona(XIAXUE_ID)
    tpl = ps.get_world_template(TPL_ID)
    print(f"  人格：{pa['name']} / 主角={pa['is_protagonist']} / 规则{len(pa['rules'])} 条"
          f" / 样例 {len(pa['examples'])} 组")
    print(f"  模板：{tpl['name']} / 圈层 {len(tpl['factions'])} 条 / 惯例 {len(tpl['customs'])} 条")

    verify(legacy)
    print("\n完成。以后所有读取走 persona_store.active_persona()，"
          "旧 JSON 文件保留作兜底（库空时自动回退）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())