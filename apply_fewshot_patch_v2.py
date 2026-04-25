"""
狼人杀 Few-shot CoT 提示词升级补丁 - V2（修复版）
运行方式: python apply_fewshot_patch_v2.py
"""

import re
import os
import shutil

MAIN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "main.py")
BACKUP = MAIN_PY + ".bak_before_fewshot"

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def apply_patch():
    # 备份
    if not os.path.exists(BACKUP):
        shutil.copy2(MAIN_PY, BACKUP)
        print(f"[OK] 备份到 {BACKUP}")
    
    code = read_file(MAIN_PY)
    
    # ===== 1. 替换 WEREWOLF_PROMPT_TEMPLATE =====
    new_template = '''WEREWOLF_PROMPT_TEMPLATE = """你是国服狼人杀顶尖玩家，1000+场经验。严格从自己身份的第一视角，用严谨逻辑链分析场上局势，生成真实有说服力的真人发言。

【核心原则】
1. 严格视角锁死：只知道自己身份和夜间信息，绝不开上帝视角
2. 逻辑链闭环：每个结论必须有对应论据，不能凭空踩/保人
3. 状态自然拟人：语气像真人，口语化，有轻微情绪波动
4. 轮次感清晰：只聊当前轮次最重要的事，不提前聊假设内容
5. 拉票导向明确：结尾清晰说出投票目标并给出理由

【绝对禁止】
- 开上帝视角直接说"XX是狼"
- 说"我是AI""根据规则"
- 贴脸/发誓/情绪绑架
- 发言超过100字

【思考框架·按此顺序推理】
STEP1 信息盘点：我有什么独家信息？（查验/银水/毒人/狼队友）
STEP2 局势判断：好人轮次领先还是狼人轮次领先？场上几狼几神？
STEP3 站边决策：目前信谁？为什么信？逻辑基点是什么？
STEP4 目标锁定：本轮应该推谁？推他的理由是否站得住？
STEP5 风险评估：我这么发言会不会暴露？有没有更安全的表达方式？

【Few-shot 示例】

示例1 - 预言家第一天发言（有查杀）：
【内心推理】STEP1:我昨晚查验3号，结果是狼人。这是铁查杀。STEP2:第1天，信息极少，我必须站出来报查验，否则查杀信息丢失。STEP3:我是唯一真预言家，必须报查验带队。STEP4:目标锁定3号，必须推他出局。STEP5:报查验时语气坚定，不留犹豫空间，否则被悍跳狼钻空子。
【本轮发言】我是预言家，昨晚查验3号，查杀！警徽流先验7后验5，3号不出我死不瞑目。大家跟票投3。

示例2 - 狼人第一天发言（隐藏身份）：
【内心推理】STEP1:我是狼人，队友是4号。昨晚刀了6号。STEP2:第1天，需要隐藏身份，假装闭眼好人分析。STEP3:站边方向：可以稍微偏一下怀疑真预言家，但不能太明显。STEP4:找一个发言有漏洞的好人，放大他的问题。STEP5:发言要像普通村民，不要太完美，也不要太激进。
【本轮发言】昨晚6号走了，感觉信息量不大。刚才2号发言逻辑有点跳，一会儿站这边一会儿站那边，我先怀疑2号，大家听听后面怎么说。

示例3 - 村民第二天发言（有信息后）：
【内心推理】STEP1:我是村民，没有任何夜间信息。STEP2:第2天，已经死了一个好人，轮次对我们不利。STEP3:5号昨天报了预言家查验4号金水，逻辑比较通顺，我暂时站5号。STEP4:7号昨天发言踩了5号但没给理由，行为偏狼。STEP5:我说话要有立场但保留余地，不能把话说死。
【本轮发言】我站5号这边，他昨天报查验逻辑自洽。7号你昨天踩5号是什么依据？光说"感觉不对"不是理由，你得给出你的逻辑线。这票我倾向投7。

示例4 - 女巫关键时刻起跳：
【内心推理】STEP1:我是女巫，第1夜救了6号（银水），第2夜毒了7号。STEP2:现在3号在悍跳预言家并且要推真预言家5号，轮次已经很危险了。STEP3:我必须起跳了，否则5号被推出去好人崩盘。STEP4:起跳后强势归票3号。STEP5:起跳要信息量爆炸，把银水和毒药信息全部报出，让人无法质疑。
【本轮发言】我才是真女巫！第1夜6号被刀我用的银水救了，第2夜我毒了7号。3号你悍跳预言家，5号才是真的，我银水6号可以作证。这票必须投3，不出3号好人没机会了！

【输出格式】
【内心推理】<按STEP1-5推理，私密推演，给自己看>
【本轮发言】<给其他玩家看的发言，30-100字，口语化>"""'''
    
    pattern = r'WEREWOLF_PROMPT_TEMPLATE\s*=\s*"""[\s\S]*?"""'
    code = re.sub(pattern, new_template, code, count=1)
    print("[OK] 1. WEREWOLF_PROMPT_TEMPLATE → Few-shot CoT 版本")
    
    # ===== 2. 替换 ROLE_PROMPTS =====
    new_role_prompts = '''ROLE_PROMPTS = {
    "werewolf": """【狼人专属】
你的任务：隐藏狼人身份，混淆好人视角，抗推好人。

{{PERSONA_BLOCK}}

📋 行为准则：
- 视角校准：用"闭眼民"逻辑分析局势，避免提及夜晚信息及队友关系
- 攻击策略：寻找好人的逻辑断层或前后矛盾，将其放大为"铁狼"行为
- 断臂求生：在队友身份岌岌可危且自己身份较高时，可果断卖掉队友做高自己
- 切忌聊爆：严禁说"我昨晚看到…""我们狼队…"
- 轮次意识：明确当前是"推人轮次"还是"找神轮次"，给出必推某人的逻辑基点

🧠 决策树（什么情况→怎么想→怎么做）：

IF 有人跳预言家且查杀你/你的队友：
  → 不能慌，质疑他的查验逻辑和警徽流
  → 话术："你验我的依据是什么？警徽流留了吗？不聊验人逻辑直接查杀，你不心虚吗？"

IF 场上无人跳预言家：
  → 你可以悍跳预言家，但必须准备好警徽流和验人逻辑
  → 话术："我是预言家，昨晚验了X号是金水，警徽流先Y后Z。"

IF 队友被查杀且即将被投出：
  → 评估：卖队友能换多少信任？如果自己身份不高就果断卖
  → 话术："X号确实有问题，我跟大家的判断，这票出X。"

IF 场上信息对你有利（好人内讧）：
  → 火上浇油，但不要太过明显
  → 话术："你们两个说法对不上，肯定有人在说谎，我先听听再说。"

💬 发言风格：逻辑严密但略带试探。要有站边、有拉票、理由听起来虽有道理但经不起深推。太完美的发言反而像开眼的神。""",

    "seer": """【预言家专属】
你的任务：报查验、留警徽流、点狼坑、带队好人。

{{PERSONA_BLOCK}}
📋 行为准则：
- 查验即答：首句必须干脆利落报清"昨晚查验X号，身份为金水/查杀"
- 心路交底：说明验人逻辑（如：摸X号定义边角位格局）
- 警徽流严谨：必须留两晚明确的警徽流，说清顺序及相应结果对应的飞警徽方式
- 攻击悍跳狼：重点打击悍跳狼的警徽流漏洞、视角缺失，避免情绪化贴脸
- 真诚拉票：强调自己是全场唯一真预言家，只有自己拥有真实的团队

🧠 决策树（什么情况→怎么想→怎么做）：

IF 第一天有查杀：
  → 必须第一时间起跳报查杀，这是你的铁证
  → 话术："我是预言家，昨晚验了X号，查杀！警徽流先Y后Z，X号不出我死不瞑目。"

IF 第一天验出金水：
  → 起跳报金水，金水是你的第一票仓
  → 话术："预言家，昨晚验X号金水。警徽流先Y后Z，目前场上信息不够，大家先报站边。"

IF 有人悍跳预言家：
  → 冷静对跳，不打感情牌，用逻辑碾压
  → 话术："你说你是预言家？你的警徽流呢？验人依据呢？我昨晚真验了X号，结果在这，大家看谁的逻辑链完整。"

【发言风格】
底气十足，节奏明快。报查验不犹豫，盘逻辑不摇摆""",

    "witch": """【女巫专属】
你的任务：隐身份盘逻辑，关键轮次跳身份带队。

{{PERSONA_BLOCK}}

📋 行为准则：
- 潜伏伪装：前两轮发言完全模拟普通村民，只聊逻辑不聊身份信息
- 起跳时机：仅在自己被推出局、发现同守同救奶穿、或明确找到了双药狼时才起跳
- 信息核爆：起跳后必须清晰报出：第几夜救了谁（银水）、第几夜毒了谁、为什么毒
- 强势归票：一旦起跳，不接受分票，必须指出明确的投票目标

🧠 决策树（什么情况→怎么想→怎么做）：

IF 第一夜有人被刀且你有解药：
  → 一般规则：第一夜尽量救人（除非被刀的人白天发言极度可疑）
  → 不报银水，默默记住，等需要时再起跳

IF 白天有人报了预言家且被悍跳狼攻击：
  → 你暂时不跳，但暗中站边真预言家
  → 话术："X号报查验的逻辑比Y号顺畅，我暂时站X。Y号你先解释一下你的警徽流。"

IF 自己被推到PK台/被当成狼：
  → 必须起跳自保，银水信息是铁证
  → 话术："我是女巫！第N夜我救了X号，这是铁银水。你们推我就是推神，好人亏轮次的。"

【发言风格】
女巫要有底气但不张扬，发言像有逻辑的好人，关键时刻果断跳身份带队。""",

    "villager": """【村民专属】
你的任务：找狼是唯一目标，发言要有逻辑、有站边。

{{PERSONA_BLOCK}}

📋 行为准则：
- 抓虫专家：专注抓取他人发言中的爆点和不连贯之处
- 软站边艺术：信息匮乏时可说"目前X号聊得偏像好人，但我还在听Y号的更新发言"
- 强制归票：每轮发言结尾必须有投票倾向或明确弃票理由
- 底线思维：承认自己的视角盲区，而不是硬装知晓一切

🧠 决策树（什么情况→怎么想→怎么做）：

IF 场上两个预言家对跳：
  → 分析谁的验人逻辑更合理、警徽流更严谨
  → 话术："X号和Y号对跳，X号警徽流清晰验人逻辑通顺，Y号只说查杀不聊依据，我站X。"

IF 某人发言前后矛盾：
  → 这是重要的找狼线索，要指出来
  → 话术："Z号你上一轮说站X，这轮突然改站Y，你的转变依据是什么？这不像好人的思维方式。"

IF 信息不够无法判断：
  → 诚实表态但给出观察方向
  → 话术："信息量太少我暂时没法锁狼，但A号和B号的发言我存疑，下轮更新发言再看。"

【发言风格】
村民的发言要像普通好人——有困惑、有疑虑、有判断。不要装得很厉害，也不要太弱。""",
}'''
    
    pattern = r'ROLE_PROMPTS\s*=\s*\{[\s\S]*?\n\}'
    code = re.sub(pattern, new_role_prompts, code, count=1)
    print("[OK] 2. ROLE_PROMPTS → 带决策树版本")
    
    # ===== 3. 在 ROLES 之前插入 NIGHT_ACTION_PROMPTS 和 VOTE_PROMPTS =====
    night_vote_prompts = '''
# ====================== 夜间行动 Few-shot 推理模板 ======================
NIGHT_ACTION_PROMPTS = {
    "werewolf": """你是狼人杀中的【狼人】"{name}"。狼队友: {wolf_team}。你需要选择今晚击杀的目标。
【思考框架】1.谁是真预言家？刀掉他能摧毁好人信息链 2.谁是女巫？刀掉她能剥夺好人两瓶药 3.如果有人跳了身份，优先刀高威胁神位
【Few-shot 示例】场景:第2夜,5号跳了预言家查杀了你队友3号。思考:5号是预言家且查杀了我队友,刀掉5号能阻断好人信息链。<answer>5号</answer>
请做出决定:""",

    "seer": """你是狼人杀中的【预言家】"{name}"。你需要选择今晚查验的目标。
【思考框架】1.谁最可疑？发言逻辑不连贯、站边摇摆的人优先验 2.边角位优先验 3.避免重复验
【Few-shot 示例】场景:第1夜,3号发言逻辑跳跃。思考:第1夜信息最少,3号发言逻辑跳跃很可疑,先验3号定义场上格局。<answer>3号</answer>
请做出决定:""",

    "witch": """你是狼人杀中的【女巫】"{name}"。今晚被刀的人是: {kill_target}。解药可用: {can_heal}。毒药可用: {can_poison}。
【思考框架·救人】第一夜尽量救人，后期判断被刀者身份价值。【思考框架·毒人】只有非常确定某人是狼才毒，毒错好人等于送狼一轮。
【Few-shot 示例】场景:第1夜,3号被刀,你有解药。思考:第1夜信息极少,3号身份未知但第一天就死太亏,用解药救人。不用毒药。<answer>heal:3号, poison:不毒</answer>
请做出决定:""",
}

# ====================== 投票决策 Few-shot 推理模板 ======================
VOTE_PROMPTS = {
    "werewolf": """你是狼人杀中的狼人"{name}"，狼队友:{wolf_team}。你需要投票。狼人策略：票投好人，绝不投队友。
【Few-shot 示例】场景:预言家5号查杀了你队友3号。思考:队友3号已经保不住了，如果我投3号能做高自己身份。<answer>3号</answer>
请选择投票目标:""",

    "seer": """你是狼人杀中的预言家"{name}"。你需要投票。你的票必须投给你查验出的狼人或最可疑的人。
【Few-shot 示例】场景:你昨晚查杀7号。思考:7号是铁狼，我的票必须投7号。<answer>7号</answer>
请选择投票目标:""",

    "good": """你是狼人杀中的好人"{name}"（{role_name}）。你需要投票给你认为最可能是狼人的玩家。
【投票思考框架】1.谁被预言家查杀了？→首选投查杀 2.谁的发言逻辑最不连贯？→摇摆、前后矛盾的人像狼 3.不要分票
【Few-shot 示例】场景:5号报预言家查杀7号。思考:5号报查验有逻辑有警徽流，7号只说"我也是"没给依据。投7号。<answer>7号</answer>
请选择投票目标:""",
}

'''
    
    pattern = r'(# ====================== 角色定义 ======================\nROLES = \{)'
    code = re.sub(pattern, night_vote_prompts + r'\1', code, count=1)
    print("[OK] 3. 插入 NIGHT_ACTION_PROMPTS 和 VOTE_PROMPTS")
    
    # ===== 4. 替换 _build_game_context =====
    # 使用 chr(10) 代替 \n 避免转义问题
    new_ctx = '''def _build_game_context(room, player):
    """构建游戏上下文供LLM参考 - V2: 增加轮次压力和投票历史"""
    alive = room.get_alive_players()
    dead = [p for p in room.players if not p.alive]
    dead_names_only = [p.name for p in dead]

    # 轮次分析
    alive_wolves = len([p for p in alive if p.role == "werewolf"])
    alive_goods = len(alive) - alive_wolves
    if alive_wolves == 0:
        round_pressure = "【轮次】好人已锁定胜局，狼人全部出局。"
    elif alive_wolves >= alive_goods:
        round_pressure = "【轮次】⚠️ 狼人已经追平或反超！好人阵营处于极度危险之中，必须立刻找狼！"
    elif alive_goods - alive_wolves <= 2:
        round_pressure = "【轮次】⚠️ 狼人只差一步翻盘！好人必须集中火力，不能分票！"
    else:
        round_pressure = "【轮次】好人目前还领先，但不能掉以轻心。"

    # 投票历史
    vote_history_lines = []
    if hasattr(room, 'last_vote_info') and room.last_vote_info:
        vote_history_lines.append(room.last_vote_info)
    for rec in room.night_history:
        d = rec.get("day", 0)
        dead_list = rec.get("dead", [])
        if dead_list:
            vote_history_lines.append(f"第{d}夜出局:{chr(12289).join(dead_list)}")
    vote_history = chr(10).join(vote_history_lines) if vote_history_lines else "（暂无投票历史）"

    # 今日讨论记录
    current_day_msgs = [m for m in (room.messages or []) if m.get("type") == "speech"]
    msg_lines = [f"{m['name']}:{m['content']}" for m in current_day_msgs[-20:]]

    # 预言家查验记录
    seer_notes = ""
    if player.role == "seer":
        seer_results = []
        for rec in room.night_history:
            r = rec.get("seer_result")
            if r:
                night_num = rec.get("day")
                seer_results.append(f"第{night_num}夜查验:{r}")
        if seer_results:
            seer_notes = chr(10) + "【你的查验记录】" + chr(10) + chr(10).join(seer_results)
        else:
            seer_notes = chr(10) + "【你的查验记录】" + chr(10) + "（尚未查验任何人）"
        current_seer = room.night_actions.get("seer_result")
        if current_seer and str(current_seer) not in seer_notes:
            seer_notes += f"{chr(10)}昨夜查验:{current_seer}"

    # 女巫专属信息
    witch_notes = ""
    if player.role == "witch":
        heal_history = []
        poison_history = []
        for rec in room.night_history:
            d = rec.get("day", 0)
            heals = rec.get("heals", [])
            poison = rec.get("poison")
            if heals:
                heal_history.append(f"第{d}夜救人:{chr(12289).join(heals)}")
            if poison:
                poison_history.append(f"第{d}夜毒人:{poison}")
        if heal_history:
            witch_notes += chr(10) + "【你的银水记录】" + chr(10) + chr(10).join(heal_history)
        if poison_history:
            witch_notes += chr(10) + "【你的毒药记录】" + chr(10) + chr(10).join(poison_history)
        witch_notes += f"{chr(10)}【当前状态】解药:{'可用' if player.witch_heal else '已用'} | 毒药:{'可用' if player.witch_poison else '已用'}"

    # 夜间历史
    night_history_lines = []
    for rec in room.night_history:
        d = rec["day"]
        dead_list = rec.get("dead", [])
        seer_rec = rec.get("seer_result", "")
        line = f"第{d}夜: {chr(12289).join(dead_list) if dead_list else '无人死亡'}"
        if seer_rec and player.role == "seer":
            line += f" | 你查验:{seer_rec}"
        night_history_lines.append(line)
    night_history_info = chr(10).join(night_history_lines) if night_history_lines else "（尚未有任何夜间记录）"

    ctx = f"""【游戏状态】
第 {room.day} 天
存活玩家({len(alive)}人):{', '.join(p.name for p in alive)}
昨夜出局:{', '.join(dead_names_only) if dead_names_only else '无人'}
{seer_notes}{witch_notes}

{round_pressure}

【历史夜间记录】
{night_history_info}

【投票/出局历史】
{vote_history}

【今日讨论记录】
{chr(10).join(msg_lines) if msg_lines else '(尚未有人发言)'}

【重要规则】你是{ROLES.get(player.role, {}).get('name', player.role)}，只有【存活玩家】才能发言和被投票。死人已经不能说话。"""
    return ctx
'''
    
    pattern = r'def _build_game_context\(room, player\):[\s\S]*?    return ctx\n'
    code = re.sub(pattern, new_ctx + '\n', code, count=1)
    print("[OK] 4. _build_game_context → V2（轮次压力+投票历史+女巫信息）")
    
    # ===== 5. 替换 _llm_decide_night_target =====
    new_night_func = '''def _llm_decide_night_target(player, room, candidates, action_desc):
    """用LLM决定夜间行动目标 - V2: Few-shot CoT推理"""
    if not USE_LLM:
        return None
    if not candidates:
        return None

    candidate_names = [p.name for p in candidates]
    game_ctx = _build_game_context(room, player)

    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_team = [w.name for w in wolves if w.id != player.id]
        template = NIGHT_ACTION_PROMPTS["werewolf"]
        sys_prompt = template.format(
            name=player.name,
            wolf_team=', '.join(wolf_team) if wolf_team else '无队友',
        )
    elif player.role == "seer":
        template = NIGHT_ACTION_PROMPTS["seer"]
        sys_prompt = template.format(name=player.name)
    else:
        role_name = ROLES.get(player.role, {}).get("name", player.role)
        sys_prompt = f"""你是狼人杀中的【{role_name}】玩家"{player.name}"。你需要{action_desc}。请先用<think>标签做简短推理,再在<answer>标签内只输出一个玩家名字。"""

    user_prompt = f"""{game_ctx}

可选目标:{', '.join(candidate_names)}
请做出决定:"""

    result = _call_deepseek(sys_prompt, user_prompt, max_tokens=300)
    if result:
        import re as _re_night
        m = _re_night.search(r"<answer>(.*?)</answer>", result, _re_night.DOTALL)
        if m:
            answer = m.group(1).strip()
        else:
            answer = result.strip()
        for name in candidate_names:
            if name in answer:
                return name
    return None

'''
    
    pattern = r'def _llm_decide_night_target\(player, room, candidates, action_desc\):[\s\S]*?    return None\n\n'
    code = re.sub(pattern, new_night_func, code, count=1)
    print("[OK] 5. _llm_decide_night_target → Few-shot CoT 版本")
    
    # ===== 6. 替换 _llm_decide_vote =====
    new_vote_func = '''def _llm_decide_vote(player, room):
    """用 LLM 决定投票目标 - V2: Few-shot CoT推理"""
    if not USE_LLM:
        return None
    alive = room.alive_players_except(player.id)
    if not alive:
        return None

    alive_names = [p.name for p in alive]
    game_ctx = _build_game_context(room, player)

    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves]
        template = VOTE_PROMPTS["werewolf"]
        sys_prompt = template.format(
            name=player.name,
            wolf_team=', '.join(n for n in wolf_names if n != player.name) or '无'
        )
    elif player.role == "seer":
        template = VOTE_PROMPTS["seer"]
        sys_prompt = template.format(name=player.name)
    else:
        role_name = ROLES.get(player.role, {}).get("name", player.role)
        template = VOTE_PROMPTS["good"]
        sys_prompt = template.format(name=player.name, role_name=role_name)

    user_prompt = f"""{game_ctx}

存活可投票玩家:{', '.join(alive_names)}
请选择投票目标:"""

    result = _call_deepseek(sys_prompt, user_prompt, max_tokens=150)
    if result:
        import re as _re_vote
        m = _re_vote.search(r"<answer>(.*?)</answer>", result, _re_vote.DOTALL)
        if m:
            answer = m.group(1).strip()
        else:
            answer = result.strip()
        for name in alive_names:
            if name in answer:
                return name
    return None

'''
    
    pattern = r'def _llm_decide_vote\(player, room\):[\s\S]*?    return None\n\n'
    code = re.sub(pattern, new_vote_func, code, count=1)
    print("[OK] 6. _llm_decide_vote → Few-shot CoT 版本")
    
    # ===== 7. AI狼人夜间行动 =====
    old_ai_wolf = '''    if not human_wolf:
        # 全AI狼人
        if non_wolves:
            target = random.choice(non_wolves)
            room.night_actions["kill_target"] = target.name'''
    new_ai_wolf = '''    if not human_wolf:
        # 全AI狼人 - 优先使用LLM决策
        if non_wolves:
            llm_target = _llm_decide_night_target(wolves[0], room, non_wolves, "选择今晚击杀目标") if USE_LLM else None
            if llm_target:
                target = next((p for p in non_wolves if p.name == llm_target), random.choice(non_wolves))
            else:
                target = random.choice(non_wolves)
            room.night_actions["kill_target"] = target.name'''
    code = code.replace(old_ai_wolf, new_ai_wolf, 1)
    print("[OK] 7. AI狼人夜间行动 → LLM决策优先")
    
    # ===== 8. AI预言家夜间行动 =====
    old_ai_seer = '''    if seer.is_ai:
        alive = room.alive_players_except(seer.id)
        if alive:
            target = random.choice(alive)
            result = "狼人" if target.role == "werewolf" else "好人"
            room.night_actions["seer_target"] = target.name
            room.night_actions["seer_result"] = f"{target.name} 是 {result}"'''
    new_ai_seer = '''    if seer.is_ai:
        alive = room.alive_players_except(seer.id)
        if alive:
            # 优先使用LLM决策
            llm_target = _llm_decide_night_target(seer, room, alive, "选择今晚查验目标") if USE_LLM else None
            if llm_target:
                target = next((p for p in alive if p.name == llm_target), random.choice(alive))
            else:
                target = random.choice(alive)
            result = "狼人" if target.role == "werewolf" else "好人"
            room.night_actions["seer_target"] = target.name
            room.night_actions["seer_result"] = f"{target.name} 是 {result}"'''
    code = code.replace(old_ai_seer, new_ai_seer, 1)
    print("[OK] 8. AI预言家夜间行动 → LLM决策优先")
    
    # ===== 9. AI女巫夜间行动 =====
    old_ai_witch = '''    if witch.is_ai:
        # AI女巫逻辑:随机决定是否救人/毒人
        if kill_target and witch.witch_heal and random.random() > 0.3:
            room.night_actions["witch_heal"] = kill_target
            witch.witch_heal = False
        else:
            room.night_actions["witch_heal"] = None  # 跳过解药

        if witch.witch_poison and random.random() > 0.6:
            alive_others = room.alive_players_except(witch.id)
            # 女巫不能在已救人的情况下毒同一个人(不同版本规则不同,这里简化)
            if alive_others:
                poison_target = random.choice(alive_others)
                room.night_actions["witch_poison"] = poison_target.name
                witch.witch_poison = False
        else:
            room.night_actions["witch_poison"] = None'''
    new_ai_witch = '''    if witch.is_ai:
        # AI女巫逻辑 - V2: 优先LLM决策，失败则随机降级
        llm_witch_done = False
        if USE_LLM:
            try:
                game_ctx = _build_game_context(room, witch)
                witch_prompt = NIGHT_ACTION_PROMPTS["witch"].format(
                    name=witch.name,
                    kill_target=kill_target or "无人",
                    can_heal="可用" if witch.witch_heal else "已用",
                    can_poison="可用" if witch.witch_poison else "已用",
                )
                result = _call_deepseek(witch_prompt, f"{game_ctx}\\n\\n请做出决定:", max_tokens=200)
                if result:
                    import re as _re_witch
                    # 解析 heal
                    heal_match = _re_witch.search(r"heal[:：]\\s*(.*?)(?:,|，|poison|$)", result, _re_witch.DOTALL)
                    if heal_match and witch.witch_heal:
                        heal_text = heal_match.group(1).strip()
                        if kill_target and kill_target in heal_text and "不救" not in heal_text:
                            room.night_actions["witch_heal"] = kill_target
                            witch.witch_heal = False
                        else:
                            room.night_actions["witch_heal"] = None
                    else:
                        room.night_actions["witch_heal"] = None

                    # 解析 poison
                    poison_match = _re_witch.search(r"poison[:：]\\s*(.*?)(?:$)", result, _re_witch.DOTALL)
                    if poison_match and witch.witch_poison:
                        poison_text = poison_match.group(1).strip()
                        if "不毒" not in poison_text:
                            alive_others = room.alive_players_except(witch.id)
                            for p in alive_others:
                                if p.name in poison_text:
                                    room.night_actions["witch_poison"] = p.name
                                    witch.witch_poison = False
                                    break
                            else:
                                room.night_actions["witch_poison"] = None
                        else:
                            room.night_actions["witch_poison"] = None
                    else:
                        room.night_actions["witch_poison"] = None
                    llm_witch_done = True
            except Exception as e:
                print(f"[LLM] 女巫决策失败,降级到随机: {e}")

        if not llm_witch_done:
            # 降级:随机规则
            if kill_target and witch.witch_heal and random.random() > 0.3:
                room.night_actions["witch_heal"] = kill_target
                witch.witch_heal = False
            else:
                room.night_actions["witch_heal"] = None

            if witch.witch_poison and random.random() > 0.6:
                alive_others = room.alive_players_except(witch.id)
                if alive_others:
                    poison_target = random.choice(alive_others)
                    room.night_actions["witch_poison"] = poison_target.name
                    witch.witch_poison = False
            else:
                room.night_actions["witch_poison"] = None'''
    code = code.replace(old_ai_witch, new_ai_witch, 1)
    print("[OK] 9. AI女巫夜间行动 → LLM决策优先，随机降级")
    
    # 写入修改后的代码
    write_file(MAIN_PY, code)
    
    print(f"\n[DONE] 补丁已应用到 {MAIN_PY}")
    print(f"[BACKUP] 原文件备份在 {BACKUP}")


if __name__ == "__main__":
    apply_patch()
