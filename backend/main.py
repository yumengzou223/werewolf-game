"""
AI 狼人杀 · 后端 (Flask + SocketIO 实时版)
修复版:严格按正常狼人杀流程实现,接入 DeepSeek LLM
"""
import os
import json
import uuid
import random
import time
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room

# ====================== 加载环境变量 ======================
try:
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
except Exception as _e:
    print(f"[ENV] 加载.env失败: {_e}")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
USE_LLM = bool(DEEPSEEK_API_KEY)
print(f"[LLM] DeepSeek {'已启用' if USE_LLM else '未配置,使用模板发言'}")

# LLM 调用(同步,在后台线程调用)
def call_deepseek(messages, max_tokens=200):
    """调用 DeepSeek API,返回文本;失败返回 None"""
    if not USE_LLM:
        return None
    try:
        import urllib.request
        payload = json.dumps({
            "model": "deepseek-chat",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.9,
        }).encode("utf-8")
        req = urllib.request.Request(
            DEEPSEEK_BASE_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[LLM] 调用失败: {e}")
        return None

# ====================== 配置 ======================
app = Flask(__name__, template_folder=None, static_folder=None)
CORS(app, resources={r"/*": {"origins": "*"}})
app.config["SECRET_KEY"] = "werewolf-secret-2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", allow_upgrades=True, ping_timeout=20, ping_interval=25)


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

# ====================== 角色定义 ======================
ROLES = {
    "werewolf":  {"team": "werewolf", "name": "狼人",    "color": "#e74c3c", "glow": "#ff4444"},
    "seer":      {"team": "good",     "name": "预言家",  "color": "#f39c12", "glow": "#ffd700"},
    "witch":     {"team": "good",     "name": "女巫",    "color": "#9b59b6", "glow": "#da70d6"},
    "villager":  {"team": "good",     "name": "村民",    "color": "#3498db", "glow": "#87ceeb"},
}

MAX_PLAYERS = 8
SPEAK_TIME = 120   # 发言时间秒
VOTE_TIME = 30    # 投票时间秒
NIGHT_WAIT = 60   # 夜间等待人类操作超时秒

# ====================== AI 人设预设 ======================
AI_PERSONAS = {
    "silent": {
        "name": "唐僧",
        "desc": "你以唐僧的身份参与狼人杀小游戏：性情温厚慈悲，言语儒雅和缓，秉持向善向正之心，循循善诱不疾不徐。遇事坚守本心、劝诫有度，略带温和絮语，常怀悲悯，重礼法守正道，耐心引导、言辞庄重，不躁不厉，始终以劝善明理为念。",
        "style": "以唐僧为核心人格"
    },
    "aggressive": {
        "name": "王熙凤",
        "desc": "你以王熙凤的身份参与狼人杀小游戏，表演型人格特质拉满：行事张扬爱出风头，言辞爽利浮夸，情绪外放极具感染力，八面玲珑擅造势控场，热衷彰显自我、渴求关注，言语鲜活带锋芒，既精明狡黠又擅长调动氛围，时刻保持强烈存在感。",
        "style": "以王熙凤为原型，表演型人格特质拉满"

    },
    "slime": {
        "name": "猪八戒",
        "desc": "以猪八戒的身份参与狼人杀小游戏，深谙世故圆滑，趋利避害、嘴甜会来事，遇事能躲则躲、擅长推诿，察言观色极准，爱耍小聪明偷懒，说话圆滑讨巧，不冒尖不担责，自带慵懒油滑感，分寸感极强，从不吃亏。",
        "style": "以猪八戒为原型"
    },
    "newbie": {
        "name": "薛杉杉",
        "desc": "以薛杉杉的身份参与狼人杀小游戏，性格软萌单纯，毫无心机城府，待人赤诚直白，略带小迷糊，情绪全写在脸上，说话软糯天真，善良心软没防备，心思简单通透，软和无害，自带天真烂漫的钝感。",
        "style": "以薛杉杉为原型"
    },
    "dramatic": {
        "name": "孙悟空",
        "desc": "以齐天大圣的身份参与狼人杀小游戏，性格激进刚烈，桀骜不驯、敢闯敢拼，行事果决凌厉，不畏强权、不服管束。言辞锋锐霸气，遇事主动出击绝不拖沓，脾气火爆直爽，杀伐果断，自带一往无前的冲劲，遇事绝不妥协退让。",
        "style": "以齐天大圣为原型"
    },
}

# ====================== 狼人杀专业提示词 ======================
# 通用结构：先【内心推理】（自己看），再【本轮发言】（给其他玩家看）
# 发言短但信息完整（30-100字），口语化，有轮次感
WEREWOLF_PROMPT_TEMPLATE = """你是国服狼人杀顶尖玩家，1000+场经验。严格从自己身份的第一视角，用严谨逻辑链分析场上局势，生成真实有说服力的真人发言。

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
【本轮发言】<给其他玩家看的发言，30-100字，口语化>"""

ROLE_PROMPTS = {
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
}

# ====================== 工具函数 ======================
def generate_id():
    return uuid.uuid4().hex[:8]

# ====================== 游戏房间 ======================
rooms = {}  # room_id -> GameRoom

class Player:
    def __init__(self, sid=None, name="", is_ai=False, is_human=False, persona=None):
        self.id = generate_id()
        self.sid = sid
        self.name = name
        self.role = None
        self.is_ai = is_ai
        self.is_human = is_human
        self.alive = True
        self.vote = None
        self.night_target = None
        self.witch_heal = True    # 解药是否可用
        self.witch_poison = True  # 毒药是否可用
        self.has_spoken = False
        self.pk_nominated = False
        self.persona = persona     # AI 人设预设 key,None=随机风格

    def to_dict(self, reveal_role=False):
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role if reveal_role else None,
            "role_name": ROLES.get(self.role, {}).get("name", "") if reveal_role else None,
            "role_color": ROLES.get(self.role, {}).get("color", "") if reveal_role else "",
            "alive": self.alive,
            "is_ai": self.is_ai,
            "persona": self.persona,
        }


class GameRoom:
    def __init__(self, room_id, owner_id):
        self.room_id = room_id
        self.owner_id = owner_id
        self.players = []
        self._speech_done = False  # 当前发言者是否已提交发言
        self._speech_ready = False  # 前端是否已准备好接收发言
        self.phase = "waiting"
        self.day = 0
        self.turn_index = 0
        self.speaker_list = []
        self.awaiting_speech_for = None
        self.votes = {}
        self.night_actions = {}
        self.winner = None
        self.messages = []
        self.pk_candidates = []
        self.current_role_turn = None
        # 用于防止投票/夜间阶段重复推进
        self._phase_token = None
        self._night_resolving = False  # 防止 _resolve_night 重复进入的幂等锁
        self.night_history = []  # [{day, kills:[], heals:[], poison, dead:[], seer_result}]

    def add_player(self, player):
        if len(self.players) >= MAX_PLAYERS:
            return False
        self.players.append(player)
        return True

    def get_player(self, pid):
        return next((p for p in self.players if p.id == pid), None)

    def get_player_by_sid(self, sid):
        return next((p for p in self.players if p.sid == sid), None)

    def get_player_by_name(self, name):
        return next((p for p in self.players if p.name == name), None)

    def get_alive_players(self):
        return [p for p in self.players if p.alive]

    def alive_names(self):
        return [p.name for p in self.get_alive_players()]

    def alive_players_except(self, exclude_id):
        return [p for p in self.get_alive_players() if p.id != exclude_id]

    def setup_game(self):
        n = len(self.players)
        if n == 6:
            roles_pool = ["werewolf", "werewolf", "seer", "witch", "villager", "villager"]
        elif n >= 7:
            roles_pool = ["werewolf", "werewolf", "werewolf", "seer", "witch", "villager", "villager", "villager"]
        else:
            roles_pool = ["werewolf", "seer", "witch", "villager"]
        random.shuffle(roles_pool)
        for i, p in enumerate(self.players):
            p.role = roles_pool[i % len(roles_pool)]
            p.alive = True
            p.vote = None
            p.night_target = None
            p.witch_heal = True
            p.witch_poison = True
            p.has_spoken = False
        self.phase = "night"
        self.day = 1
        self.messages = []
        self._phase_token = None

    def get_state(self, for_sid=None, reveal_all=False):
        player_objs = []
        for p in self.players:
            is_me = (for_sid is not None and p.sid == for_sid)
            show_role = reveal_all or is_me
            player_objs.append({
                "id": p.id,
                "name": p.name,
                "role": p.role if show_role else None,
                "role_name": ROLES.get(p.role, {}).get("name", "") if show_role else None,
                "role_color": ROLES.get(p.role, {}).get("color", "") if show_role else "",
                "alive": p.alive,
                "is_ai": p.is_ai,
                "is_me": is_me,
            })

        witch = self.get_role("witch")
        witch_info = {}
        if witch and witch.alive and self.phase in ("role_witch",):
            witch_info = {
                "knows_dead": self.night_actions.get("kill_target"),
                "can_heal": witch.witch_heal,
                "can_poison": witch.witch_poison,
            }

        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "day": self.day,
            "players": player_objs,
            "messages": self.messages[-60:],
            "votes": self.votes if self.phase in ["vote", "pk_vote"] else {},
            "winner": self.winner,
            "current_role_turn": self.current_role_turn,
            "speech_timer": SPEAK_TIME,
            "vote_timer": VOTE_TIME,
            "witch_info": witch_info,
            "turn_index": self.turn_index,
            "speaker_list": self.speaker_list,
            "pk_candidates": self.pk_candidates,
        }

    def get_role(self, role_name):
        for p in self.players:
            if p.role == role_name and p.alive:
                return p
        return None

    def get_werewolves(self):
        return [p for p in self.players if p.role == "werewolf" and p.alive]

    def check_win(self):
        wolves = [p for p in self.players if p.role == "werewolf" and p.alive]
        goods = [p for p in self.players if p.role != "werewolf" and p.alive]
        if len(wolves) == 0:
            self.winner = "good"
            self.phase = "end"
            return "good"
        if len(wolves) >= len(goods):
            self.winner = "werewolf"
            self.phase = "end"
            return "werewolf"
        return None

    def add_message(self, speaker_id, content, msg_type="speech"):
        player = self.get_player(speaker_id)
        name = player.name if player else "系统"
        role = player.role if player else None
        self.messages.append({
            "id": generate_id(),
            "speaker_id": speaker_id or "system",
            "name": name,
            "role": role,
            "role_name": ROLES.get(role, {}).get("name", "") if role else "",
            "role_color": ROLES.get(role, {}).get("color", "") if role else "",
            "content": content,
            "type": msg_type,
            "time": time.time(),
        })

    def add_sys_msg(self, content):
        self.messages.append({
            "id": generate_id(),
            "speaker_id": "system",
            "name": "系统",
            "role": None,
            "role_name": "",
            "role_color": "",
            "content": content,
            "type": "system",
            "time": time.time(),
        })

    def reset_votes(self):
        self.votes = {}
        for p in self.players:
            p.vote = None

    def reset_speech_state(self):
        self.turn_index = 0
        self.awaiting_speech_for = None
        self.speaker_list = []
        for p in self.players:
            p.has_spoken = False


# ====================== HTTP 路由 ======================
# Docker/ Railway 部署:main.py 在 /app/main.py,frontend 在 /app/frontend/
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))  # /app
_FRONTEND_DIR = os.path.join(_BACKEND_DIR, "frontend")       # /app/frontend
connected_sids = {}  # sid -> {room_id, player_id}

@app.route("/health")
def health():
    return "ok"

@app.route("/")
def index():
    return send_from_directory(_FRONTEND_DIR, "index.html")

@app.route("/game")
def serve_game():
    return send_from_directory(_FRONTEND_DIR, "game.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(os.path.join(_FRONTEND_DIR, "static"), filename)

@app.route("/api/room/create", methods=["POST"])
def api_create():
    body = request.get_json() or {}
    player_name = body.get("player_name", "房主")
    room_id = generate_id()
    room = GameRoom(room_id, owner_id=None)
    player = Player(name=player_name, is_human=True)
    room.owner_id = player.id
    room.add_player(player)
    rooms[room_id] = room
    return jsonify({"room_id": room_id, "player_id": player.id, "player": player.to_dict()})

@app.route("/api/room/<room_id>", methods=["GET"])
def api_get_room(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    reveal = room.phase in ["end", "waiting"]
    return jsonify(room.get_state(reveal_all=reveal))

@app.route("/api/debug/room/<room_id>", methods=["GET"])
def api_debug_room(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    return jsonify(room.get_state(reveal_all=True))

@app.route("/api/room/<room_id>/join", methods=["POST"])
def api_join(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    if room.phase != "waiting":
        return jsonify({"error": "游戏已开始"}), 400
    if len(room.players) >= MAX_PLAYERS:
        return jsonify({"error": "房间已满"}), 400
    body = request.get_json() or {}
    player_name = body.get("player_name", f"玩家{len(room.players)+1}")
    player = Player(name=player_name, is_human=True)
    room.add_player(player)
    socketio.emit("player_joined", room.get_state(), room=room_id)
    return jsonify({"player_id": player.id, "player": player.to_dict()})

@app.route("/api/room/<room_id>/add-ai", methods=["POST"])
def api_add_ai(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    if room.phase != "waiting":
        return jsonify({"error": "游戏已开始"}), 400
    if len(room.players) >= MAX_PLAYERS:
        return jsonify({"error": "房间已满"}), 400

    ai_name_pool = [
        "深渊狼", "暗月狼", "荒野狼", "占星师", "先知", "预言少女",
        "灵媒师", "调药师", "村长伯伯", "猎人老张", "小红帽", "三毛",
        "花花", "阿强", "牛牛", "小明", "大虎"
    ]
    used_names = {p.name for p in room.players}
    avail = [n for n in ai_name_pool if n not in used_names]
    ai_name = random.choice(avail) if avail else f"AI_{generate_id()[:4]}"
    # 随机分配人设
    persona_key = random.choice(list(AI_PERSONAS.keys()))
    player = Player(name=ai_name, is_ai=True, persona=persona_key)
    room.add_player(player)
    socketio.emit("player_joined", room.get_state(), room=room_id)
    return jsonify({"player_id": player.id, "player": player.to_dict()})


@app.route("/api/room/<room_id>/add-ai-preset", methods=["POST"])
def api_add_ai_preset(room_id):
    """邀请指定人设的AI玩家"""
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    if room.phase != "waiting":
        return jsonify({"error": "游戏已开始"}), 400
    if len(room.players) >= MAX_PLAYERS:
        return jsonify({"error": "房间已满"}), 400

    data = request.get_json() or {}
    persona_key = data.get("persona")
    if persona_key and persona_key not in AI_PERSONAS:
        return jsonify({"error": f"未知的人设:{persona_key}"}), 400

    ai_name_pool = [
        "深渊狼", "暗月狼", "荒野狼", "占星师", "先知", "预言少女",
        "灵媒师", "调药师", "村长伯伯", "猎人老张", "小红帽", "三毛",
        "花花", "阿强", "牛牛", "小明", "大虎"
    ]
    used_names = {p.name for p in room.players}
    avail = [n for n in ai_name_pool if n not in used_names]
    ai_name = random.choice(avail) if avail else f"AI_{generate_id()[:4]}"
    player = Player(name=ai_name, is_ai=True, persona=persona_key)
    room.add_player(player)
    socketio.emit("player_joined", room.get_state(), room=room_id)
    return jsonify({"player_id": player.id, "player": player.to_dict()})


@app.route("/api/personas", methods=["GET"])
def api_list_personas():
    """返回所有可用AI人设列表"""
    return jsonify({"personas": AI_PERSONAS})

@app.route("/api/room/<room_id>/start", methods=["POST"])
def api_start(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    if len(room.players) < 4:
        return jsonify({"error": "至少需要4人"}), 400

    room.setup_game()
    # 给每个玩家单独发送含自己角色的状态
    for p in room.players:
        if p.sid:
            socketio.emit("game_started", room.get_state(for_sid=p.sid), room=p.sid)
        else:
            # AI玩家没有sid,广播用全知状态
            pass
    # 也给房间广播一下(不含角色)
    socketio.emit("game_started", room.get_state(), room=room_id)
    socketio.start_background_task(_night_phase, room_id)
    return jsonify({"ok": True})


# ====================== 夜间流程 ======================

def _night_phase(room_id):
    """夜间主流程:狼人 → 预言家 → 女巫 → 结算"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    room.phase = "night"
    room.current_role_turn = None
    room.night_actions = {
        "kill_target": None,
        "seer_target": None,
        "seer_result": None,
        "witch_heal": False,   # False=未用,None=跳过
        "witch_poison": None,
    }
    # 重置夜间状态
    for p in room.players:
        p.night_target = None

    token = generate_id()
    room._phase_token = token

    socketio.emit("night_start", {"day": room.day, "state": room.get_state()}, room=room_id)
    socketio.sleep(2)

    # --- 狼人阶段 ---
    _phase_werewolf(room_id, token)


def _phase_werewolf(room_id, token):
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    wolves = room.get_werewolves()
    if not wolves:
        _phase_seer(room_id, token)
        return

    room.phase = "role_kill"
    room.current_role_turn = "werewolf"

    human_wolf = next((w for w in wolves if not w.is_ai), None)
    non_wolves = [p for p in room.get_alive_players() if p.role != "werewolf"]

    # 通知前端狼人阶段(只有狼人玩家会看到目标列表)
    for w in wolves:
        if w.sid:
            teammates = [{"id": x.id, "name": x.name} for x in wolves if x.id != w.id]
            socketio.emit("role_turn", {
                "role": "werewolf",
                "instruction": "狼人请选择今晚要击杀的目标",
                "teammates": teammates,
                "targets": [p.to_dict() for p in non_wolves],
                "state": room.get_state(for_sid=w.sid),
            }, room=w.sid)

    if not human_wolf:
        # 全AI狼人 - 优先使用LLM决策
        if non_wolves:
            llm_target = _llm_decide_night_target(wolves[0], room, non_wolves, "选择今晚击杀目标") if USE_LLM else None
            if llm_target:
                target = next((p for p in non_wolves if p.name == llm_target), random.choice(non_wolves))
            else:
                target = random.choice(non_wolves)
            room.night_actions["kill_target"] = target.name
        socketio.sleep(3)
        _phase_seer(room_id, token)
    else:
        # 有人类狼人,等待操作,超时自动随机
        socketio.sleep(NIGHT_WAIT)
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        if room.night_actions.get("kill_target") is None and non_wolves:
            # 超时自动随机
            target = random.choice(non_wolves)
            room.night_actions["kill_target"] = target.name
            room.add_sys_msg(f"狼人超时,自动击杀 {target.name}")
        _phase_seer(room_id, token)


def _phase_seer(room_id, token):
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    seer = room.get_role("seer")
    if not seer:
        _phase_witch(room_id, token)
        return

    room.phase = "role_seer"
    room.current_role_turn = "seer"
    targets = [p.to_dict() for p in room.alive_players_except(seer.id)]

    if seer.sid:
        socketio.emit("role_turn", {
            "role": "seer",
            "instruction": "预言家请选择今晚要查验的目标",
            "targets": targets,
            "state": room.get_state(for_sid=seer.sid),
        }, room=seer.sid)

    if seer.is_ai:
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
            room.night_actions["seer_result"] = f"{target.name} 是 {result}"
        socketio.sleep(2)
        _phase_witch(room_id, token)
    else:
        # 等待人类预言家操作
        socketio.sleep(NIGHT_WAIT)
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        if room.night_actions.get("seer_target") is None:
            alive = room.alive_players_except(seer.id)
            if alive:
                target = random.choice(alive)
                result = "狼人" if target.role == "werewolf" else "好人"
                room.night_actions["seer_target"] = target.name
                room.night_actions["seer_result"] = f"{target.name} 是 {result}"
        _phase_witch(room_id, token)


def _phase_witch(room_id, token):
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    witch = room.get_role("witch")
    if not witch:
        _resolve_night(room_id, token)
        return

    room.phase = "role_witch"
    room.current_role_turn = "witch"
    kill_target = room.night_actions.get("kill_target")

    if witch.is_ai:
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
                result = _call_deepseek(witch_prompt, f"{game_ctx}\n\n请做出决定:", max_tokens=200)
                if result:
                    import re as _re_witch
                    # 解析 heal
                    heal_match = _re_witch.search(r"heal[:：]\s*(.*?)(?:,|，|poison|$)", result, _re_witch.DOTALL)
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
                    poison_match = _re_witch.search(r"poison[:：]\s*(.*?)(?:$)", result, _re_witch.DOTALL)
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
                room.night_actions["witch_poison"] = None

        socketio.sleep(2)
        _resolve_night(room_id, token)
    else:
        # 人类女巫:发送事件让前端显示选择界面
        if witch.sid:
            socketio.emit("role_turn", {
                "role": "witch",
                "instruction": "女巫,请做出决定",
                "kill_target": kill_target,
                "can_heal": witch.witch_heal,
                "can_poison": witch.witch_poison,
                "targets": [p.to_dict() for p in room.get_alive_players()],
                "state": room.get_state(for_sid=witch.sid),
            }, room=witch.sid)

        # 等待女巫操作(她会通过socket发送 witch_action 事件)
        socketio.sleep(NIGHT_WAIT)
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        # 超时:直接结算（未使用的药=放弃）
        _resolve_night(room_id, token)


def _resolve_night(room_id, token):
    """夜间结算"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return
    # 【幂等锁】防止并发/重复调用导致结算逻辑执行多次
    if room._night_resolving:
        return
    room._night_resolving = True

    kill_t = room.night_actions.get("kill_target")
    heal_t = room.night_actions.get("witch_heal")   # None=跳过,玩家名=救了谁
    poison_t = room.night_actions.get("witch_poison")

    dead_names = []
    # 狼人击杀
    if kill_t and kill_t != heal_t:
        dead_names.append(kill_t)
    # 女巫毒杀
    if poison_t and poison_t not in dead_names:
        dead_names.append(poison_t)

    dead_players = []
    for name in dead_names:
        p = room.get_player_by_name(name)
        if p and p.alive:
            p.alive = False
            dead_players.append(p)

    # 记录夜间历史（结算前就记录，供AI次日开始使用）
    night_record = {
        "day": room.day,
        "kills": [kill_t] if kill_t else [],
        "heals": [heal_t] if heal_t else [],
        "poison": poison_t,
        "dead": [p.name for p in dead_players],
        "seer_result": room.night_actions.get("seer_result"),
    }
    room.night_history.append(night_record)

    room.phase = "night_result"

    # 私发预言家结果
    seer = room.get_role("seer")
    seer_result = room.night_actions.get("seer_result")
    if seer and seer.sid and seer_result:
        socketio.emit("seer_result_private", {
            "seer_result": seer_result,
        }, room=seer.sid)

    socketio.emit("night_result", {
        "day": room.day,
        "kill_target": kill_t,
        "healed": (kill_t is not None and kill_t == heal_t),
        "poison_target": poison_t,
        "dead": [p.name for p in dead_players],
        "dead_roles": {p.name: ROLES.get(p.role, {}).get("name", "") for p in dead_players},
        "state": room.get_state(reveal_all=False),
    }, room=room_id)

    socketio.sleep(1)

    # 【保护】重新验证:sleep 期间游戏可能已结束
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        room._night_resolving = False
        return

    winner = room.check_win()
    if winner:
        socketio.sleep(2)
        room._night_resolving = False
        _end_game(room_id)
        return

    # 【保护】进入遗言前再次验证
    socketio.sleep(3)
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        room._night_resolving = False
        return
    # 结算完成,释放幂等锁,然后启动遗言阶段
    room._night_resolving = False
    _run_last_words(room_id, token, dead_players)


def _run_last_words(room_id, token, dead_players):
    """遗言阶段:死亡玩家依次发言30秒"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    human_dead = [p for p in dead_players if not p.is_ai]

    if not human_dead:
        # 没有人类死亡玩家,直接进入白天
        socketio.start_background_task(_start_day, room_id, token)
        return

    room.phase = "last_words"

    for dp in human_dead:
        room.add_sys_msg(f"【遗言】{dp.name}({ROLES[dp.role]['name']})请发言(30秒)")
        room.awaiting_speech_for = dp.id

        socketio.emit("last_words_start", {
            "player_id": dp.id,
            "player_name": dp.name,
            "role": dp.role,
            "role_name": ROLES.get(dp.role, {}).get("name", ""),
            "role_color": ROLES.get(dp.role, {}).get("color", ""),
            "timer": 30,
            "state": room.get_state(),
        }, room=room_id)

        if dp.sid:
            socketio.emit("your_last_words", {
                "timer": 30,
            }, room=dp.sid)

        room._speech_done = False
        elapsed = 0
        while elapsed < 30:
            socketio.sleep(1)
            elapsed += 1
            room = rooms.get(room_id)
            if not room or room._phase_token != token or room.phase != "last_words":
                return
            if room._speech_done or room.awaiting_speech_for != dp.id:
                break
        room = rooms.get(room_id)
        if room:
            room.awaiting_speech_for = None

    # 【保护】遗言全部结束后再次验证再进入白天
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return
    socketio.start_background_task(_start_day, room_id, token)


def _start_day(room_id, token):
    """天亮"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    room.phase = "day_result"
    room.day += 1
    room.reset_speech_state()
    room.reset_votes()
    room.pk_candidates = []

    alive = room.get_alive_players()
    room.speaker_list = [p.id for p in alive]
    random.shuffle(room.speaker_list)

    socketio.emit("day_start", {
        "day": room.day,
        "state": room.get_state(),
    }, room=room_id)

    socketio.sleep(5)  # 给前端足够时间完成页面切换再开始发言
    _run_discussion(room_id, token)


def _run_discussion(room_id, token):
    """白天发言阶段"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    room.phase = "discussion"
    room.turn_index = 0
    room.awaiting_speech_for = None

    socketio.emit("discussion_start", {
        "speaker_list": room.speaker_list,
        "state": room.get_state(),
    }, room=room_id)

    _advance_speaker(room_id, token)


def _advance_speaker(room_id, token):
    """推进到下一个发言者"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    # 跳过已死亡玩家
    while room.turn_index < len(room.speaker_list):
        sid_id = room.speaker_list[room.turn_index]
        sp = room.get_player(sid_id)
        if sp and sp.alive:
            break
        room.turn_index += 1

    if room.turn_index >= len(room.speaker_list):
        # 所有人发完言,进入投票
        socketio.sleep(1)
        _run_vote(room_id, token)
        return

    speaker_id = room.speaker_list[room.turn_index]
    speaker = room.get_player(speaker_id)
    if not speaker or not speaker.alive:
        room.turn_index += 1
        _advance_speaker(room_id, token)
        return

    room.awaiting_speech_for = speaker.id

    # 广播给所有人:不含角色信息(防止身份泄露)
    socketio.emit("speaking_start", {
        "speaker_id": speaker.id,
        "speaker_name": speaker.name,
        "role": None,
        "role_name": "",
        "role_color": "",
        "timer": SPEAK_TIME,
        "turn_index": room.turn_index,
        "total": len(room.speaker_list),
        "state": room.get_state(),
    }, room=room_id)
    # 单独给发言者本人发送含角色信息的通知
    if speaker.sid:
        socketio.emit("speaking_start_self", {
            "speaker_id": speaker.id,
            "role": speaker.role,
            "role_name": ROLES.get(speaker.role, {}).get("name", ""),
            "role_color": ROLES.get(speaker.role, {}).get("color", ""),
        }, room=speaker.sid)

    if speaker.is_ai:
        socketio.start_background_task(_ai_speak, room_id, speaker.id, token)
    else:
        # 等待前端发 speech_ready 信号(最多等10秒),再开始计时
        room._speech_ready = False
        ready_wait = 0
        while ready_wait < 10:
            socketio.sleep(1)
            ready_wait += 1
            room = rooms.get(room_id)
            if not room or room._phase_token != token or room.phase not in ("discussion",):
                return
            if room._speech_ready:
                break

        # 前端已准备好,开始计时
        room._speech_done = False
        elapsed = 0
        while elapsed < SPEAK_TIME:
            socketio.sleep(1)
            elapsed += 1
            room = rooms.get(room_id)
            if not room or room._phase_token != token or room.phase not in ("discussion",):
                return
            if room._speech_done or room.awaiting_speech_for != speaker.id:
                return  # 已发言,on_speech 会继续推进
        # 真正超时(需同时验证:phase未变 + 玩家仍存活 + 仍轮到该玩家)
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        if room.phase not in ("discussion",) or not speaker.alive or room.awaiting_speech_for != speaker.id:
            return
        room.awaiting_speech_for = None
        room.turn_index += 1
        socketio.emit("speaking_end", {
            "speaker_id": speaker.id,
            "state": room.get_state(),
        }, room=room_id)
        socketio.sleep(1)
        _advance_speaker(room_id, token)


def _ai_speak(room_id, player_id, token):
    """AI玩家发言(模拟思考2-4秒后发言)"""
    socketio.sleep(random.uniform(2, 4))
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase not in ("discussion", "pk_discussion"):
        return
    player = room.get_player(player_id)
    if not player or not player.alive:
        return
    if room.awaiting_speech_for != player.id:
        return

    # 生成发言内容
    alive_names = [p.name for p in room.get_alive_players() if p.id != player.id]
    speech = _generate_ai_speech(player, room, alive_names)

    player.has_spoken = True
    room.add_message(player.id, speech, "speech")
    room.awaiting_speech_for = None

    socketio.emit("player_speech", {
        "player_id": player.id,
        "player_name": player.name,
        "role": player.role,
        "role_color": ROLES.get(player.role, {}).get("color", ""),
        "content": speech,
        "state": room.get_state(),
    }, room=room_id)

    room.turn_index += 1
    socketio.emit("speaking_end", {
        "speaker_id": player.id,
        "state": room.get_state(),
    }, room=room_id)
    socketio.sleep(1)

    if room.phase == "discussion":
        _advance_speaker(room_id, token)
    elif room.phase == "pk_discussion":
        _advance_pk_speaker(room_id, token)


def _call_deepseek(system_prompt, user_prompt, max_tokens=200):
    """调用 DeepSeek API,返回文本。失败返回 None。"""
    if not USE_LLM:
        return None
    try:
        import urllib.request
        payload = json.dumps({
            "model": "deepseek-chat",
            "max_tokens": max_tokens,
            "temperature": 0.9,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            DEEPSEEK_BASE_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[LLM] DeepSeek调用失败: {e}")
        return None


def _build_game_context(room, player):
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



def _llm_decide_night_target(player, room, candidates, action_desc):
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

    candidate_names = [p.name for p in candidates]
    role_name = ROLES.get(player.role, {}).get("name", player.role)
    game_ctx = _build_game_context(room, player)

    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_team = [w.name for w in wolves if w.id != player.id]
        sys_prompt = f"""你是狼人杀游戏中的【狼人】玩家"{player.name}"。
狼队友:{', '.join(wolf_team) if wolf_team else '无'}
你需要{action_desc}。
请先用<think>标签做简短推理(分析哪个目标最有价值),再在<answer>标签内只输出一个玩家名字。
格式:<think>推理过程</think><answer>玩家名</answer>"""
    elif player.role == "seer":
        sys_prompt = f"""你是狼人杀游戏中的【预言家】玩家"{player.name}"。
你需要{action_desc},优先查验你最怀疑是狼人的玩家。
请先用<think>标签做简短推理(分析谁最可疑),再在<answer>标签内只输出一个玩家名字。
格式:<think>推理过程</think><answer>玩家名</answer>"""
    else:
        sys_prompt = f"""你是狼人杀游戏中的【{role_name}】玩家"{player.name}"。
你需要{action_desc}。
请先用<think>标签做简短推理,再在<answer>标签内只输出一个玩家名字。
格式:<think>推理过程</think><answer>玩家名</answer>"""

    user_prompt = f"""{game_ctx}

可选目标:{', '.join(candidate_names)}
请做出决定:"""

    result = _call_deepseek(sys_prompt, user_prompt, max_tokens=200)
    if result:
        import re
        # 提取<answer>标签内容
        m = re.search(r"<answer>(.*?)</answer>", result, re.DOTALL)
        if m:
            answer = m.group(1).strip()
        else:
            answer = result.strip()
        # 匹配候选名
        for name in candidate_names:
            if name in answer:
                return name
    return None


def _generate_ai_speech(player, room, alive_names):
    """生成AI发言内容:先用狼人杀专业提示词调用LLM,失败则使用模板"""
    role_info = ROLES.get(player.role, {})
    role_name = role_info.get("name", player.role)

    # ===== 构建系统提示 =====
    sys_prompt = WEREWOLF_PROMPT_TEMPLATE

    role_key = player.role if player.role in ROLE_PROMPTS else "villager"
    role_block = ROLE_PROMPTS[role_key]

    # 注入狼人队友
    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_team = [w.name for w in wolves if w.id != player.id]
        role_block = role_block.replace("{{WOLF_TEAM}}", ", ".join(wolf_team) if wolf_team else "无队友")
    else:
        role_block = role_block.replace("{{WOLF_TEAM}}", "")

    # 注入人设风格
    if player.persona and player.persona in AI_PERSONAS:
        persona_info = AI_PERSONAS[player.persona]
        pname = persona_info["name"]
        pstyle = persona_info["style"]
        persona_block = f"\n\n【额外风格约束】--你扮演的是：{pname}。{pstyle}"
    else:
        persona_block = ""
    role_block_filled = role_block.replace("{{PERSONA_BLOCK}}", persona_block)
    sys_prompt += f"\n\n{role_block_filled}"

    # ===== 构建用户提示 =====
    game_ctx = _build_game_context(room, player)

    speaker_list = getattr(room, "speaker_list", [])
    turn_index = getattr(room, "turn_index", 0)
    spoken_names = []
    not_spoken_names = []
    if speaker_list:
        for i, sid in enumerate(speaker_list):
            sp = next((p for p in room.players if p.id == sid), None)
            if sp:
                if i < turn_index:
                    spoken_names.append(sp.name)
                else:
                    not_spoken_names.append(sp.name)
    else:
        not_spoken_names = list(alive_names)

    last_vote_info = getattr(room, "last_vote_info", "") or ""

    # 计算昨夜实际死亡（考虑女巫救人）
    kill_t = room.night_actions.get("kill_target")
    heal_t = room.night_actions.get("witch_heal")
    poison_t = room.night_actions.get("witch_poison")
    actual_dead = []
    if kill_t and kill_t != heal_t:
        actual_dead.append(kill_t)
    if poison_t:
        actual_dead.append(poison_t)
    night_info = f"昨夜死亡:{', '.join(actual_dead)}" if actual_dead else "昨夜死亡:无人"

    user_prompt = f"""【当前局势】
身份:{role_name} 名字:{player.name}
存活玩家:{', '.join(alive_names)}
已发言:{', '.join(spoken_names) if spoken_names else '无'}
未发言:{', '.join(not_spoken_names) if not_spoken_names else '无'}
{night_info}
{last_vote_info}

{game_ctx}

【发言任务】
你是{role_name},轮到你发言了。请严格按以下格式输出:

【内心推理】<简短推演:场上局势、站边、策略、投票目标,只给自己看>
【本轮发言】<给其他玩家看的发言,20-50字,口语化,有站边有理由>"""

    # 尝试调用 LLM
    llm_result = _call_deepseek(sys_prompt, user_prompt, max_tokens=400)
    if llm_result:
        import re as _re
        think_match = _re.search(r"【内心推理】(.*?)【本轮发言】", llm_result, _re.DOTALL)
        if think_match:
            print(f"\n[AI推理] {player.name}({role_name}):\n{think_match.group(1).strip()}")
        speech_match = _re.search(r"【本轮发言】(.*?)$", llm_result, _re.DOTALL)
        if speech_match:
            speech = speech_match.group(1).strip()
        else:
            speech = _re.sub(r"【.*?】", "", llm_result).strip()
        speech = _re.sub(r"^(发言：|我说：|过麦：)", "", speech).strip()
        if len(speech) > 200:
            speech = speech[:200] + "..."
        if speech:
            return speech

    # 降级到模板发言
    wolves = room.get_werewolves()
    seer_result = room.night_actions.get("seer_result", "")
    if player.role == "werewolf":
        others = [n for n in alive_names if n not in [w.name for w in wolves]]
        if others and random.random() > 0.5:
            target = random.choice(others)
            speeches = [
                f"感觉{target}有点可疑,大家注意一下。",
                f"{target}发言逻辑有问题,我怀疑是狼。",
                "没什么特别想法,先听听大家的。",
                "别老盯着我,我就是普通村民。",
            ]
        else:
            speeches = ["我是村民,大家可以信任我。", "场上信息还不够,先冷静分析。"]
    elif player.role == "seer":
        if seer_result and "狼人" in seer_result and random.random() > 0.3:
            parts = seer_result.split(" 是 ")
            if len(parts) == 2:
                return f"我是预言家,昨晚查了{parts[0]},【狼人】!大家投他!"
        speeches = [
            "我是预言家,昨晚查验结果暂时保密。",
            "狼人一定会悍跳,大家小心。",
        ]
    elif player.role == "witch":
        speeches = [
            "我是村民,大家一起分析。",
            "刚才那人说话方式很怪,感觉有点刻意。",
            "第一天乱投没意思,先看看。",
        ]
    else:
        if alive_names:
            suspect = random.choice(alive_names)
            speeches = [
                f"觉得{suspect}发言有问题,大家关注一下。",
                "先听听预言家的意见。",
                "不要乱投票,一定要有依据。",
            ]
        else:
            speeches = ["没什么特别信息,先观察。"]

    return random.choice(speeches)


def _llm_decide_vote(player, room):
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

    alive_names = [p.name for p in alive]
    role_name = ROLES.get(player.role, {}).get("name", player.role)
    wolves = room.get_werewolves()
    wolf_names = [w.name for w in wolves]

    if player.role == "werewolf":
        sys_prompt = f"""你是狼人杀中的狼人玩家"{player.name}"。你的狼人队友:{', '.join(n for n in wolf_names if n != player.name) or '无'}。
你需要投票给一个好人(非狼人)。请只回复一个玩家名字,不要加任何解释。"""
    else:
        sys_prompt = f"""你是狼人杀中的{role_name}玩家"{player.name}"。
你需要投票给你认为最可能是狼人的玩家。请只回复一个玩家名字,不要加任何解释。"""

    game_ctx = _build_game_context(room, player)
    user_prompt = f"""{game_ctx}

存活可投票玩家:{', '.join(alive_names)}
请选择你要投票的目标(只回复一个名字):"""

    result = _call_deepseek(sys_prompt, user_prompt, max_tokens=20)
    if result:
        # 从结果中提取玩家名
        for name in alive_names:
            if name in result:
                return name
    return None


# ====================== 投票流程 ======================

def _run_vote(room_id, token):
    """投票阶段"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    room.phase = "vote"
    room.reset_votes()

    socketio.emit("vote_start", {
        "timer": VOTE_TIME,
        "candidates": [p.to_dict() for p in room.get_alive_players()],
        "state": room.get_state(),
    }, room=room_id)

    # AI自动投票(在VOTE_TIME秒内随机投)
    for p in room.get_alive_players():
        if p.is_ai:
            socketio.start_background_task(_ai_vote, room_id, p.id, token)

    # 等待投票时间结束
    socketio.sleep(VOTE_TIME)

    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "vote":
        return

    # 超时未投票的玩家自动弃权(不投)
    _resolve_vote(room_id, token)


def _ai_vote(room_id, player_id, token):
    """AI投票(随机延迟,优先使用LLM决策)"""
    socketio.sleep(random.uniform(3, min(VOTE_TIME - 8, 15)))
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "vote":
        return
    player = room.get_player(player_id)
    if not player or not player.alive or player.vote:
        return

    alive = room.alive_players_except(player.id)
    if not alive:
        return

    # 优先 LLM 决策投票
    voted_name = _llm_decide_vote(player, room)
    if voted_name:
        voted_player = room.get_player_by_name(voted_name)
        if voted_player and voted_player.alive:
            voted = voted_player
        else:
            voted_name = None

    if not voted_name:
        # 降级:基于规则投票
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves]
        seer_result = room.night_actions.get("seer_result", "")
        seer_target = room.night_actions.get("seer_target", "")

        if player.role == "werewolf":
            targets = [a for a in alive if a.name not in wolf_names]
            voted = random.choice(targets) if targets else random.choice(alive)
        elif player.role == "seer" and seer_target and "狼人" in seer_result:
            seer_voted = next((a for a in alive if a.name == seer_target), None)
            voted = seer_voted if seer_voted else random.choice(alive)
        else:
            voted = random.choice(alive)

    # 二次检查 room 状态(LLM调用可能耗时)
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "vote":
        return
    if player.vote:  # 已经投票了(防重)
        return

    player.vote = voted.name
    room.votes[player.id] = voted.name

    socketio.emit("vote_cast", {
        "player_id": player.id,
        "player_name": player.name,
        "voted_name": voted.name,
        "votes": dict(room.votes),
        "state": room.get_state(),
    }, room=room_id)


def _resolve_vote(room_id, token, is_pk=False):
    """投票结算"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    from collections import Counter
    tally = Counter(room.votes.values())
    if not tally:
        # 无人投票,直接进入下一夜
        room.add_sys_msg("本轮无人投票,游戏继续。")
        socketio.emit("vote_result", {
            "result": "无人投票",
            "tally": {},
            "dead": [],
            "pk_candidates": [],
            "state": room.get_state(),
        }, room=room_id)
        socketio.sleep(3)
        socketio.start_background_task(_night_phase_new_round, room_id, token)
        return

    max_votes = max(tally.values())
    top_voted = [n for n, c in tally.items() if c == max_votes]

    dead_names = []
    if len(top_voted) == 1:
        p = room.get_player_by_name(top_voted[0])
        if p and p.alive:
            p.alive = False
            dead_names.append(top_voted[0])
            room.add_sys_msg(f"【投票】{top_voted[0]}({ROLES[p.role]['name']})被投票出局!")

    # 记录投票信息供AI次轮参考
    if dead_names:
        room.last_vote_info = f"上轮投票:{top_voted[0]}({ROLES[room.get_player_by_name(top_voted[0]).role]['name']})被投出"
    else:
        room.last_vote_info = f"上轮投票平票:{'、'.join(top_voted)}，无人出局"

    phase_name = "pk_result" if is_pk else "vote_result"
    room.phase = phase_name

    socketio.emit("vote_result", {
        "result": f"{top_voted[0]} 被投票出局" if len(top_voted) == 1 else f"平票:{'、'.join(top_voted)}",
        "tally": dict(tally),
        "dead": dead_names,
        "dead_roles": {name: ROLES[room.get_player_by_name(name).role]["name"] for name in dead_names if room.get_player_by_name(name)},
        "pk_candidates": top_voted if len(top_voted) > 1 else [],
        "is_pk": is_pk,
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # 死亡玩家遗言
    dead_human = [room.get_player_by_name(n) for n in dead_names if room.get_player_by_name(n) and not room.get_player_by_name(n).is_ai]

    socketio.sleep(2)

    winner = room.check_win()
    if winner:
        socketio.sleep(2)
        _end_game(room_id)
        return

    if dead_human:
        _run_vote_last_words(room_id, token, dead_human, top_voted if len(top_voted) > 1 else [])
    elif len(top_voted) > 1 and not is_pk:
        # 平票进入PK
        socketio.start_background_task(_run_pk_discussion, room_id, token, top_voted)
    else:
        # 进入下一夜
        socketio.start_background_task(_night_phase_new_round, room_id, token)


def _run_vote_last_words(room_id, token, dead_humans, pk_candidates):
    """投票死亡玩家遗言"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token:
        return

    room.phase = "last_words"
    for dp in dead_humans:
        room.add_sys_msg(f"【遗言】{dp.name}({ROLES[dp.role]['name']})请发言(30秒)")
        room.awaiting_speech_for = dp.id

        socketio.emit("last_words_start", {
            "player_id": dp.id,
            "player_name": dp.name,
            "role": dp.role,
            "role_name": ROLES.get(dp.role, {}).get("name", ""),
            "role_color": ROLES.get(dp.role, {}).get("color", ""),
            "timer": 30,
            "state": room.get_state(),
        }, room=room_id)

        if dp.sid:
            socketio.emit("your_last_words", {"timer": 30}, room=dp.sid)

        socketio.sleep(30)
        room.awaiting_speech_for = None

    if pk_candidates:
        socketio.start_background_task(_run_pk_discussion, room_id, token, pk_candidates)
    else:
        socketio.start_background_task(_night_phase_new_round, room_id, token)


def _run_pk_discussion(room_id, token, candidates):
    """PK发言阶段"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    room.phase = "pk_discussion"
    room.pk_candidates = candidates
    pk_ids = [room.get_player_by_name(n).id for n in candidates if room.get_player_by_name(n)]
    room.speaker_list = pk_ids
    room.turn_index = 0
    room.awaiting_speech_for = None
    room.reset_votes()

    socketio.emit("pk_discussion_start", {
        "candidates": candidates,
        "state": room.get_state(),
    }, room=room_id)

    socketio.sleep(1)
    _advance_pk_speaker(room_id, token)


def _advance_pk_speaker(room_id, token):
    """PK发言推进"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase not in ("pk_discussion",):
        return

    if room.turn_index >= len(room.speaker_list):
        socketio.sleep(1)
        _run_pk_vote(room_id, token)
        return

    speaker_id = room.speaker_list[room.turn_index]
    speaker = room.get_player(speaker_id)
    if not speaker or not speaker.alive:
        room.turn_index += 1
        _advance_pk_speaker(room_id, token)
        return

    room.awaiting_speech_for = speaker.id

    socketio.emit("speaking_start", {
        "speaker_id": speaker.id,
        "speaker_name": speaker.name,
        "timer": SPEAK_TIME,
        "is_pk": True,
        "state": room.get_state(),
    }, room=room_id)

    if speaker.is_ai:
        socketio.start_background_task(_ai_speak, room_id, speaker.id, token)
    else:
        room._speech_done = False
        elapsed = 0
        while elapsed < SPEAK_TIME:
            socketio.sleep(1)
            elapsed += 1
            room = rooms.get(room_id)
            if not room or room._phase_token != token or room.phase != "pk_discussion":
                return
            if room._speech_done or room.awaiting_speech_for != speaker.id:
                return
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        # 【保护】超时结算时也验证玩家仍存活
        if room.phase == "pk_discussion" and speaker.alive and room.awaiting_speech_for == speaker.id:
            room.awaiting_speech_for = None
            room.turn_index += 1
            socketio.emit("speaking_end", {"speaker_id": speaker.id, "state": room.get_state()}, room=room_id)
            socketio.sleep(1)
            _advance_pk_speaker(room_id, token)


def _run_pk_vote(room_id, token):
    """PK投票阶段"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    room.phase = "pk_vote"
    room.reset_votes()

    socketio.emit("pk_vote_start", {
        "candidates": room.pk_candidates,
        "timer": VOTE_TIME,
        "state": room.get_state(),
    }, room=room_id)

    # AI自动投票
    for p in room.get_alive_players():
        if p.is_ai and p.name not in room.pk_candidates:
            socketio.start_background_task(_ai_pk_vote, room_id, p.id, token)

    socketio.sleep(VOTE_TIME)
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "pk_vote":
        return
    _resolve_vote(room_id, token, is_pk=True)


def _ai_pk_vote(room_id, player_id, token):
    """AI PK投票"""
    socketio.sleep(random.uniform(2, VOTE_TIME - 3))
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "pk_vote":
        return
    player = room.get_player(player_id)
    if not player or not player.alive or room.votes.get(player.id):
        return

    voted = random.choice(room.pk_candidates)
    player.vote = voted
    room.votes[player.id] = voted

    socketio.emit("vote_cast", {
        "player_id": player.id,
        "player_name": player.name,
        "voted_name": voted,
        "votes": dict(room.votes),
        "state": room.get_state(),
    }, room=room_id)


def _night_phase_new_round(room_id, token):
    """开始新一轮夜间"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return
    # 更新token,进入新的夜间循环
    _night_phase(room_id)


def _end_game(room_id):
    room = rooms.get(room_id)
    if not room:
        return
    room.phase = "end"
    socketio.emit("game_end", {
        "winner": room.winner,
        "winner_name": "好人阵营" if room.winner == "good" else "狼人阵营",
        "state": room.get_state(reveal_all=True),
    }, room=room_id)


# ====================== Socket 事件处理 ======================

@socketio.on("connect")
def on_connect():
    print(f"[WS] 连接: {request.sid}")

@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    info = connected_sids.pop(sid, {})
    room_id = info.get("room_id")
    if room_id:
        room = rooms.get(room_id)
        if room:
            p = room.get_player_by_sid(sid)
            if p:
                room.add_sys_msg(f"{p.name} 离开了房间")
                socketio.emit("player_left", {"player_id": p.id, "state": room.get_state()}, room=room_id)

@socketio.on("ping")
def on_ping():
    pass

@socketio.on("join_room")
def on_join_room(data):
    room_id = data.get("room_id")
    player_id = data.get("player_id")
    room = rooms.get(room_id)
    if not room:
        emit("error", {"message": "房间不存在"})
        return
    p = room.get_player(player_id)
    if not p:
        emit("error", {"message": "玩家不存在"})
        return
    p.sid = request.sid
    connected_sids[request.sid] = {"room_id": room_id, "player_id": player_id}
    join_room(room_id)
    emit("room_state", room.get_state(for_sid=request.sid))
    socketio.emit("player_online", {"player_id": p.id, "state": room.get_state()}, room=room_id)


@socketio.on("night_action")
def on_night_action(data):
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room:
        emit("error", {"message": "房间不存在"})
        return
    player = room.get_player_by_sid(sid)
    if not player or not player.alive:
        return

    action = data.get("action")
    target_name = data.get("target")

    if room.phase == "role_kill" and player.role == "werewolf":
        room.night_actions["kill_target"] = target_name
        emit("action_confirmed", {"action": "kill", "target": target_name})
        # 通知所有狼人队友(包含操作者自己)
        wolves = room.get_werewolves()
        for w in wolves:
            if w.sid:
                socketio.emit("wolf_teammate_action", {
                    "player_name": player.name,
                    "target": target_name,
                }, room=w.sid)
        # 更新token,让原等待任务(NIGHT_WAIT超时)检查失败,避免双重推进
        new_token = generate_id()
        room._phase_token = new_token
        socketio.start_background_task(_phase_seer, room_id, new_token)

    elif room.phase == "role_seer" and player.role == "seer":
        target = room.get_player_by_name(target_name)
        if target:
            result = "狼人" if target.role == "werewolf" else "好人"
            room.night_actions["seer_target"] = target_name
            room.night_actions["seer_result"] = f"{target_name} 是 {result}"
            emit("seer_result_private", {"seer_result": room.night_actions["seer_result"]})
        # 更新token,让原等待任务失效
        new_token = generate_id()
        room._phase_token = new_token
        socketio.start_background_task(_phase_witch, room_id, new_token)

    elif room.phase == "role_witch" and player.role == "witch":
        witch = player
        if action == "heal" and witch.witch_heal:
            kt = room.night_actions.get("kill_target")
            if kt:
                room.night_actions["witch_heal"] = kt
                witch.witch_heal = False
                emit("action_confirmed", {"action": "heal", "target": kt})
            # 用了解药后不能再用毒药，直接结算
            room.night_actions["witch_poison"] = None
            token = room._phase_token
            socketio.start_background_task(_resolve_night, room_id, token)
            return
        elif action == "poison" and witch.witch_poison and target_name:
            # 毒药已选,立即结算
            room.night_actions["witch_poison"] = target_name
            witch.witch_poison = False
            emit("action_confirmed", {"action": "poison", "target": target_name})
        elif action == "skip":
            pass  # 跳过: witch_heal=None, witch_poison=None（未使用），无需额外操作

        # poison/skip/heal 所有分支最终都走这里结算
        token = room._phase_token
        socketio.start_background_task(_resolve_night, room_id, token)


@socketio.on("speech")
def on_speech(data):
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room:
        return

    player = room.get_player_by_sid(sid)
    if not player:
        return

    speech = data.get("content", "").strip()
    if not speech:
        speech = "(过)"

    # 遗言阶段
    if room.phase == "last_words" and room.awaiting_speech_for == player.id:
        room._speech_done = True
        room.add_message(player.id, f"【遗言】{speech}", "speech")
        room.awaiting_speech_for = None
        socketio.emit("player_speech", {
            "player_id": player.id,
            "player_name": player.name,
            "role_color": ROLES.get(player.role, {}).get("color", ""),
            "content": f"【遗言】{speech}",
            "state": room.get_state(),
        }, room=room_id)
        return

    # 白天发言阶段:必须同时满足:(1) phase正确 (2) 玩家存活 (3) 当前轮到该玩家
    if room.phase not in ("discussion", "pk_discussion"):
        return
    if not player.alive:
        return
    if room.awaiting_speech_for != player.id:
        return

    # 记录发言时的上下文(用于延迟推进时的二次验证)
    token = room._phase_token
    cur_phase = room.phase
    my_index = room.turn_index  # 捕获发言时的 turn_index

    room._speech_done = True
    player.has_spoken = True
    room.add_message(player.id, speech, "speech")
    room.awaiting_speech_for = None

    socketio.emit("player_speech", {
        "player_id": player.id,
        "player_name": player.name,
        "role_color": ROLES.get(player.role, {}).get("color", ""),
        "content": speech,
        "state": room.get_state(),
    }, room=room_id)

    room.turn_index += 1
    socketio.emit("speaking_end", {
        "speaker_id": player.id,
        "state": room.get_state(),
    }, room=room_id)

    def _delayed_advance():
        """延迟推进到下一个发言者(二次验证防止竞态)"""
        socketio.sleep(0.5)
        room = rooms.get(room_id)
        if not room:
            return
        # 二次验证:token 和 phase 必须与发言时一致,否则说明阶段已切换,不推进
        if room._phase_token != token or room.phase != cur_phase:
            return
        # turn_index 必须等于 my_index+1(说明中间没有其他玩家被处理),
        # 否则说明已有其他逻辑修改了 turn_index(避免重复推进)
        if room.turn_index != my_index + 1:
            return
        if cur_phase == "discussion":
            _advance_speaker(room_id, token)
        elif cur_phase == "pk_discussion":
            _advance_pk_speaker(room_id, token)

    socketio.start_background_task(_delayed_advance)

@socketio.on("speech_ready")
def on_speech_ready(data):
    sid = request.sid
    info = connected_sids.get(sid, {})
    room = rooms.get(info.get("room_id"))
    if not room:
        return
    player = room.get_player_by_sid(sid)
    if player and room.awaiting_speech_for == player.id:
        room._speech_ready = True


@socketio.on("vote")
def on_vote(data):
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room or room.phase not in ("vote", "pk_vote"):
        return
    player = room.get_player_by_sid(sid)
    if not player or not player.alive or player.vote:
        return

    voted_name = data.get("target")
    player.vote = voted_name
    room.votes[player.id] = voted_name

    socketio.emit("vote_cast", {
        "player_id": player.id,
        "player_name": player.name,
        "voted_name": voted_name,
        "votes": dict(room.votes),
        "state": room.get_state(),
    }, room=room_id)

    # 狼人队友之间互发投票情报(只有狼人能看到其他狼人的票)
    if player.role == "werewolf":
        wolves = room.get_werewolves()
        for w in wolves:
            if w.sid:
                socketio.emit("wolf_vote", {
                    "player_name": player.name,
                    "voted_name": voted_name,
                }, room=w.sid)


@socketio.on("wolf_chat")
def on_wolf_chat(data):
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room:
        return
    player = room.get_player_by_sid(sid)
    if not player or player.role != "werewolf" or not player.alive:
        return
    content = (data.get("content") or "").strip()
    if not content:
        return
    # 只发给所有存活狼人
    wolves = room.get_werewolves()
    for w in wolves:
        if w.sid:
            socketio.emit("wolf_chat", {
                "player_id": player.id,
                "player_name": player.name,
                "content": content,
            }, room=w.sid)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5003)), debug=False, log=False)
