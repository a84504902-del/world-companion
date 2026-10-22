# world-companion 项目长期记忆

## 人设生效源（v0.15.3 定死，最高频踩坑点）
- **DB `personas` 表是唯一生效源**，一切写入口（API/引导/保存）只写库
- `persona_local.json` / `persona_anchor.json` = 种子/备份基线，仅库空兜底读；
  **改 JSON 对已入库角色无效**（2026-10-20 实测栽过：改完 JSON 回复毫无变化）
- 改人设：API `POST /api/personas/{id}`（GET 完整对象→改→全量 POST，
  save_persona 是全量替换语义，漏字段会被默认值覆盖）
- 种子写进库：`POST /api/personas/import-seed`（自动先备份到 backups/）
- 查来源：`GET /api/personas/active`（source = db/file/none）
- 改人设必须三处同步的场景已消除（import-seed 后 JSON 只是种子）；
  但直接改 DB 时别忘了 prompts.reload_anchor() / bump_cache() 由 API 层自动做

## 提示词体系（v0.16.0 收束完成）
- **所有提示词只住 prompts/ 文件夹**，按域 5 个文件：functional（长度/摘要/检测/状态卡）、
  chat（群像/POV/钦点记忆）、tasks（场景/承诺/留言）、onboarding（引导五层+预览）、
  memory（记忆段标题），共 26 key，带域名前缀（chat./tasks./onboarding./memory.）
- 改提示词 = 改 prompts/ 对应文件，mtime 热重载秒生效，不用重启
- 代码里的同名常量只是 default 兜底（JSON 缺失时行为不变）；**不要再往代码里加提示词**
- 唯一例外：onboarding.py _GENERATE_PROMPT（人设生成，含 JSON 格式示例）留代码，改它要重启
- 占位符 {{NAME}} 由调用方传 kwargs；拼错会打 warning（不静默）；JSON 里的 JSON 示例
  写单大括号（get_prompt 清理循环会剥 {{...}}）
- 自检：tools/check_prompts.py（死配置/硬编码角色名/占位符未接线）
- 后台「提示词编辑页」未做（用户没提）

## 世界隔离（v0.17.5 定死：一个世界是一个独立包）
- **world_scope 是世界归属的总闸门**：personas 表列，取值 = 显式模板 id > 大世界名 > 题目；"" = 未归属世界（自成一批）
- 三条取数口径全部按它过滤，**新增取数必须带上**：
  - 视角切换候选：`persona_store.session_world_scope(session_id)` → `list_personas_by_scope(scope)`
  - 主角唯一：`save_persona(is_protagonist=1)` 只清同 scope 的；`get_protagonist(scope)` 取本世界主角
  - 生成上下文：`onboarding._collect_context(scope)` / `_draft_world_scope(draft)`
- **聊天链路人设一律走 session 维度**：`active_persona(session_id)`（会话绑定角色→本世界主角→全局主角→文件兜底）；
  chat.py 的 _get_protagonist_name / _my_uname / _pin_user / llm 群像规则 / proactive 留言全部已接 session_id
- 模板即整包：`repoint_world_scope(old, new)` 存模板时把该世界角色 scope 重指向 tpl_id；
  模板 cast = world_scope == tpl.id 的 personas（guide_home/templates 接口透出）
- 引导新伴侣 is_protagonist=1（本世界门面）；同世界再建会顶替门面，但老会话绑自己的角色不受影响

## 服务运维
- 启动：`./.venv/Scripts/python.exe app.py`（E:\bus 的 python 没 aiohttp；后台 Bash 默认 python 也没）
- 杀进程：PowerShell `Get-NetTCPConnection -LocalPort 8877` → OwningProcess → Stop-Process
- /chat 是 SSE 流式（`data: {"content": ...}`），不是普通 JSON
- 回归：`tests/test_1/4/8/9`（94 项）；`tools/check_prompts.py` 查提示词配置

## 已知机制
- save_draft 任何调用点漏传 world_template_id 会静默丢模板绑定（v0.15.0 教训）
- onboarding force_new：从模板开对话无视旧草稿（标记 done 不删）
- people 表是**全局名键**（session_id='' 的引导产物），关系过滤靠 persona_id 解析世界归属；
  悬空 persona_id 会被当未归属世界——清数据时顺手查 `SELECT id,name,persona_id FROM people WHERE persona_id != ''`
