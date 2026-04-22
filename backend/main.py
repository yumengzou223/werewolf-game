"""
AI 狼人杀 · 后端 (Flask + SocketIO 实时版)
修复版：严格按正常狼人杀流程实现，接入 DeepSeek LLM
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
print(f"[LLM] DeepSeek {'已启用' if USE_LLM else '未配置，使用模板发言'}")

# LLM 调用（同步，在后台线程调用）
def call_deepseek(messages, max_tokens=200):
    """调用 DeepSeek API，返回文本；失败返回 None"""
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

# ====================== 工具函数 ======================
def generate_id():
    return uuid.uuid4().hex[:8]

# ====================== 游戏房间 ======================
rooms = {}  # room_id -> GameRoom

class Player:
    def __init__(self, sid=None, name="", is_ai=False, is_human=False):
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

    def to_dict(self, reveal_role=False):
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role if reveal_role else None,
            "role_name": ROLES.get(self.role, {}).get("name", "") if reveal_role else None,
            "role_color": ROLES.get(self.role, {}).get("color", "") if reveal_role else "",
            "alive": self.alive,
            "is_ai": self.is_ai,
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
# Docker/ Railway 部署：main.py 在 /app/main.py，frontend 在 /app/frontend/
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
    player = Player(name=ai_name, is_ai=True)
    room.add_player(player)
    socketio.emit("player_joined", room.get_state(), room=room_id)
    return jsonify({"player_id": player.id, "player": player.to_dict()})

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
            # AI玩家没有sid，广播用全知状态
            pass
    # 也给房间广播一下（不含角色）
    socketio.emit("game_started", room.get_state(), room=room_id)
    socketio.start_background_task(_night_phase, room_id)
    return jsonify({"ok": True})


# ====================== 夜间流程 ======================

def _night_phase(room_id):
    """夜间主流程：狼人 → 预言家 → 女巫 → 结算"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    room.phase = "night"
    room.current_role_turn = None
    room.night_actions = {
        "kill_target": None,
        "seer_target": None,
        "seer_result": None,
        "witch_heal": False,   # False=未用，None=跳过
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

    # 通知前端狼人阶段（只有狼人玩家会看到目标列表）
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
        # 全AI狼人
        if non_wolves:
            target = random.choice(non_wolves)
            room.night_actions["kill_target"] = target.name
        socketio.sleep(3)
        _phase_seer(room_id, token)
    else:
        # 有人类狼人，等待操作，超时自动随机
        socketio.sleep(NIGHT_WAIT)
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        if room.night_actions.get("kill_target") is None and non_wolves:
            # 超时自动随机
            target = random.choice(non_wolves)
            room.night_actions["kill_target"] = target.name
            room.add_sys_msg(f"狼人超时，自动击杀 {target.name}")
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
        # AI女巫逻辑：随机决定是否救人/毒人
        if kill_target and witch.witch_heal and random.random() > 0.3:
            room.night_actions["witch_heal"] = kill_target
            witch.witch_heal = False
        else:
            room.night_actions["witch_heal"] = None  # 跳过解药

        if witch.witch_poison and random.random() > 0.6:
            alive_others = room.alive_players_except(witch.id)
            # 女巫不能在已救人的情况下毒同一个人（不同版本规则不同，这里简化）
            if alive_others:
                poison_target = random.choice(alive_others)
                room.night_actions["witch_poison"] = poison_target.name
                witch.witch_poison = False
        else:
            room.night_actions["witch_poison"] = None

        socketio.sleep(2)
        _resolve_night(room_id, token)
    else:
        # 人类女巫：发送事件让前端显示选择界面
        if witch.sid:
            socketio.emit("role_turn", {
                "role": "witch",
                "instruction": "女巫，请做出决定",
                "kill_target": kill_target,
                "can_heal": witch.witch_heal,
                "can_poison": witch.witch_poison,
                "targets": [p.to_dict() for p in room.get_alive_players()],
                "state": room.get_state(for_sid=witch.sid),
            }, room=witch.sid)

        # 等待女巫操作（她会通过socket发送 witch_action 事件）
        socketio.sleep(NIGHT_WAIT)
        room = rooms.get(room_id)
        if not room or room._phase_token != token:
            return
        # 超时：如果解药未决定，跳过
        if room.night_actions.get("witch_heal") is False:
            room.night_actions["witch_heal"] = None  # 跳过
        if room.night_actions.get("witch_poison") is False:
            room.night_actions["witch_poison"] = None  # 跳过
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
    heal_t = room.night_actions.get("witch_heal")   # None=跳过，玩家名=救了谁
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
        "state": room.get_state(reveal_all=False),
    }, room=room_id)

    socketio.sleep(1)

    # 【保护】重新验证：sleep 期间游戏可能已结束
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
    # 结算完成，释放幂等锁，然后启动遗言阶段
    room._night_resolving = False
    _run_last_words(room_id, token, dead_players)


def _run_last_words(room_id, token, dead_players):
    """遗言阶段：死亡玩家依次发言30秒"""
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase == "end":
        return

    human_dead = [p for p in dead_players if not p.is_ai]

    if not human_dead:
        # 没有人类死亡玩家，直接进入白天
        socketio.start_background_task(_start_day, room_id, token)
        return

    room.phase = "last_words"

    for dp in human_dead:
        room.add_sys_msg(f"【遗言】{dp.name}（{ROLES[dp.role]['name']}）请发言（30秒）")
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
        # 所有人发完言，进入投票
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

    # 广播给所有人：不含角色信息（防止身份泄露）
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
        # 等待前端发 speech_ready 信号（最多等10秒），再开始计时
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

        # 前端已准备好，开始计时
        room._speech_done = False
        elapsed = 0
        while elapsed < SPEAK_TIME:
            socketio.sleep(1)
            elapsed += 1
            room = rooms.get(room_id)
            if not room or room._phase_token != token or room.phase not in ("discussion",):
                return
            if room._speech_done or room.awaiting_speech_for != speaker.id:
                return  # 已发言，on_speech 会继续推进
        # 真正超时（需同时验证：phase未变 + 玩家仍存活 + 仍轮到该玩家）
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
    """AI玩家发言（模拟思考2-4秒后发言）"""
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
    """调用 DeepSeek API，返回文本。失败返回 None。"""
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
    """构建完整游戏上下文供LLM参考（含全局历史、死亡记录、查验记录）"""
    alive = room.get_alive_players()
    dead = [p for p in room.players if not p.alive]

    # 死亡记录（含角色）
    dead_lines = []
    for p in dead:
        dead_lines.append(f"{p.name}（{ROLES.get(p.role, {}).get('name', '?')}）")

    # 全部历史发言（不截断，让LLM有完整上下文）
    all_msgs = room.messages if room.messages else []
    msg_lines = []
    for m in all_msgs:
        if m.get("type") == "system":
            msg_lines.append(f"[系统] {m['content']}")
        else:
            msg_lines.append(f"第{m.get('day', room.day) if 'day' in m else '?'}天 {m['name']}：{m['content']}")

    # 预言家私有记录（只有预言家自己能用）
    seer_notes = ""
    if player.role == "seer":
        results = []
        for key in ("seer_result",):
            r = room.night_actions.get(key)
            if r:
                results.append(f"昨晚查验：{r}")
        if results:
            seer_notes = "\n【你的查验记录】\n" + "\n".join(results)

    ctx = f"""当前游戏状态：第 {room.day} 天
存活玩家（{len(alive)}人）：{', '.join(p.name for p in alive)}
已出局玩家：{', '.join(dead_lines) if dead_lines else '无'}
{seer_notes}
===历史发言===
{chr(10).join(msg_lines) if msg_lines else '（暂无）'}
===历史结束==="""
    return ctx


def _llm_decide_night_target(player, room, candidates, action_desc):
    """用LLM决定夜间行动目标，支持CoT推理"""
    if not USE_LLM:
        return None
    if not candidates:
        return None

    candidate_names = [p.name for p in candidates]
    role_name = ROLES.get(player.role, {}).get("name", player.role)
    game_ctx = _build_game_context(room, player)

    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_team = [w.name for w in wolves if w.id != player.id]
        sys_prompt = f"""你是狼人杀游戏中的【狼人】玩家"{player.name}"。
狼队友：{', '.join(wolf_team) if wolf_team else '无'}
你需要{action_desc}。
请先用<think>标签做简短推理（分析哪个目标最有价值），再在<answer>标签内只输出一个玩家名字。
格式：<think>推理过程</think><answer>玩家名</answer>"""
    elif player.role == "seer":
        sys_prompt = f"""你是狼人杀游戏中的【预言家】玩家"{player.name}"。
你需要{action_desc}，优先查验你最怀疑是狼人的玩家。
请先用<think>标签做简短推理（分析谁最可疑），再在<answer>标签内只输出一个玩家名字。
格式：<think>推理过程</think><answer>玩家名</answer>"""
    else:
        sys_prompt = f"""你是狼人杀游戏中的【{role_name}】玩家"{player.name}"。
你需要{action_desc}。
请先用<think>标签做简短推理，再在<answer>标签内只输出一个玩家名字。
格式：<think>推理过程</think><answer>玩家名</answer>"""

    user_prompt = f"""{game_ctx}

可选目标：{', '.join(candidate_names)}
请做出决定："""

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
    """生成AI发言内容：优先调用 DeepSeek LLM（带CoT+few-shot），失败则使用模板"""
    role_info = ROLES.get(player.role, {})
    role_name = role_info.get("name", player.role)

    # ===== 构建角色专属系统提示（含few-shot博弈示例）=====
    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_team = [w.name for w in wolves if w.id != player.id]
        sys_prompt = f"""你在玩狼人杀，你是【狼人】，名字"{player.name}"，队友：{', '.join(wolf_team) if wolf_team else '无'}。

你的任务：混入好人中，不被识破，推动好人投好人。

说话风格：像真实玩家一样，短句，口语，有情绪。禁止汇总信息、禁止说"确实"/"综上"/"总结"/"目前局势"这类词。

✅ 好发言（简短直接）：
- "感觉小王有点怪，一直在绕圈，说了半天没说到点上"
- "这个预言家跳得太突然了吧，我怀疑是假的"
- "我没什么特别想法，先听听大家的"
- "别老盯着我，我就是个普通村民"

❌ 坏发言（禁止这种）：
- "综合目前局势分析，XXX的发言存在逻辑漏洞，建议大家关注"
- "XXX确实可疑，但YYY的查杀力度也很大，我建议..."

字数：20-50字。只说一件事，不展开。"""

    elif player.role == "seer":
        seer_result = room.night_actions.get("seer_result", "")
        seer_target = room.night_actions.get("seer_target", "")
        wolves_found = "狼人" in seer_result if seer_result else False
        sys_prompt = f"""你在玩狼人杀，你是【预言家】，名字"{player.name}"。
昨晚查验结果：{seer_result if seer_result else '还没查验'}

说话风格：口语，简短，有时候用问句或感叹，像真人在聊天。禁止汇总信息。

✅ 好发言：
- "我出来说了——我是预言家，昨晚查了小明，狼人！投他！"
- "查到好人了，但现在不是跳的时机，等等看"
- "先不说，第一天信息太少"
- "刚才那个说话方式很怪，感觉在演"

❌ 坏发言（禁止）：
- "根据目前的信息综合分析，我认为XXX的发言逻辑存在问题..."

字数：20-50字。"""

    elif player.role == "witch":
        can_heal = player.witch_heal
        can_poison = player.witch_poison
        sys_prompt = f"""你在玩狼人杀，你是【女巫】，名字"{player.name}"，以村民身份隐藏。

说话风格：短句，口语，随意一点，不要暴露身份，不要说教。

✅ 好发言：
- "那个谁刚才说话我没太听懂，感觉有点刻意"
- "嗯……我倾向投小明，说不上来就是感觉怪"
- "先看看吧，第一天乱投没意思"
- "刚那个人替XXX洗白洗得也太积极了"

❌ 坏发言（禁止）：
- "综合场上信息，XXX的发言存在明显漏洞..."

字数：20-50字。"""

    else:  # villager
        sys_prompt = f"""你在玩狼人杀，你是【村民】，名字"{player.name}"。

说话风格：真实玩家口吻，短句口语，可以不确定、可以困惑、可以有情绪。禁止说"综上/确实/目前局势"等词。

✅ 好发言：
- "感觉小王一直在绕，说了好多但没一句有用的"
- "我也不知道投谁，信息太少了"
- "那个预言家说的有道理，跟了"
- "等等，刚才那人说话前后矛盾吧？"
- "我没啥怀疑，先观察"

❌ 坏发言（禁止）：
- "综合发言分析，XXX发言存在漏洞，建议重点关注..."

字数：15-45字。说一件事，不用展开。"""

    game_ctx = _build_game_context(room, player)

    # 构建本轮发言进度信息
    speaker_list = getattr(room, 'speaker_list', [])
    turn_index = getattr(room, 'turn_index', 0)
    spoken_names = []
    not_spoken_names = []
    if speaker_list:
        for i, sid in enumerate(speaker_list):
            sp = next((p for p in room.players if p.id == sid), None)
            if sp:
                if i < turn_index:
                    spoken_names.append(sp.name)
                elif sp.id != player.id:
                    not_spoken_names.append(sp.name)
    turn_info = ""
    if spoken_names:
        turn_info += f"\n本轮已发言玩家（可根据其发言内容评论）：{', '.join(spoken_names)}"
    if not_spoken_names:
        turn_info += f"\n本轮尚未发言玩家（不要评论他们，他们还没说话）：{', '.join(not_spoken_names)}"

    user_prompt = f"""{game_ctx}{turn_info}

现在轮到你（{player.name}，{role_name}）发言。
请先在<think>标签内做简短的内心分析（场上局势判断、策略选择），再直接输出发言内容。
格式：<think>内心分析</think>发言内容

重要约束：
- 只能评论【已发言玩家】的发言内容，不能评论还没发言的玩家
- 如果本轮你是第一个发言，不要提及任何具体玩家的发言表现
- 发言口语化，短句，20-50字，只说一件事，禁止汇总信息

注意：只有<think>之后的部分会被展示给其他玩家，<think>内的内容是你的私密思考。"""

    # 尝试调用 LLM
    llm_result = _call_deepseek(sys_prompt, user_prompt, max_tokens=300)
    if llm_result:
        import re
        # 打印AI的CoT推理过程（仅控制台可见）
        think_match = re.search(r"<think>(.*?)</think>", llm_result, re.DOTALL)
        if think_match:
            print(f"\n[AI思考] {player.name}（{role_name}）的内心推理：\n{think_match.group(1).strip()}\n")
        # 剥离<think>标签，只保留发言部分
        speech = re.sub(r"<think>.*?</think>", "", llm_result, flags=re.DOTALL).strip()
        # 去掉可能的多余前缀
        speech = re.sub(r"^(发言：|我说：|发言内容：)", "", speech).strip()
        if not speech:
            speech = llm_result.strip()
        # 截断过长发言
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
                f"我感觉 {target} 有点可疑，大家注意一下。",
                f"昨晚来看，{target} 发言逻辑混乱，我怀疑是狼。",
                "我是村民，大家可以信任我，一起找出狼人。",
                "现在还不能确定，先观察一轮。",
            ]
        else:
            speeches = ["我是村民，大家可以信任我。", "场上信息还不够，先冷静分析。"]
    elif player.role == "seer":
        if seer_result and "狼人" in seer_result and random.random() > 0.3:
            parts = seer_result.split(" 是 ")
            if len(parts) == 2:
                return f"我是预言家，昨晚查了 {parts[0]}，结果是【狼人】！大家投他！"
        speeches = [
            "我是预言家，昨晚的查验结果暂时保密，等时机合适再说。",
            "请大家信任神职，不要轻易投票好人。",
            "狼人一定会来跳神职混淆视听，大家小心。",
        ]
    elif player.role == "witch":
        speeches = [
            "我是村民，大家一起分析。",
            "昨晚的情况大家都清楚了，谁最可疑？",
            "发言顺序靠后的玩家要注意，有时候狼人喜欢跟风发言。",
        ]
    else:
        if alive_names:
            suspect = random.choice(alive_names)
            speeches = [
                f"我觉得 {suspect} 的发言有点问题，大家关注一下。",
                "先听听预言家的意见，再做决定。",
                "不要乱投票，一定要有依据。",
                "我没有特殊信息，跟随大家的判断。",
                f"场上还有狼人，我倾向于投 {suspect}。",
            ]
        else:
            speeches = ["我没有什么特别的信息，先观察一下。"]

    return random.choice(speeches)


def _llm_decide_vote(player, room):
    """用 LLM 决定投票目标，返回目标名字或 None（失败时）"""
    if not USE_LLM:
        return None
    alive = room.alive_players_except(player.id)
    if not alive:
        return None

    alive_names = [p.name for p in alive]
    role_name = ROLES.get(player.role, {}).get("name", player.role)
    wolves = room.get_werewolves()
    wolf_names = [w.name for w in wolves]

    if player.role == "werewolf":
        sys_prompt = f"""你是狼人杀中的狼人玩家"{player.name}"。你的狼人队友：{', '.join(n for n in wolf_names if n != player.name) or '无'}。
你需要投票给一个好人（非狼人）。请只回复一个玩家名字，不要加任何解释。"""
    else:
        sys_prompt = f"""你是狼人杀中的{role_name}玩家"{player.name}"。
你需要投票给你认为最可能是狼人的玩家。请只回复一个玩家名字，不要加任何解释。"""

    game_ctx = _build_game_context(room, player)
    user_prompt = f"""{game_ctx}

存活可投票玩家：{', '.join(alive_names)}
请选择你要投票的目标（只回复一个名字）："""

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

    # AI自动投票（在VOTE_TIME秒内随机投）
    for p in room.get_alive_players():
        if p.is_ai:
            socketio.start_background_task(_ai_vote, room_id, p.id, token)

    # 等待投票时间结束
    socketio.sleep(VOTE_TIME)

    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "vote":
        return

    # 超时未投票的玩家自动弃权（不投）
    _resolve_vote(room_id, token)


def _ai_vote(room_id, player_id, token):
    """AI投票（随机延迟，优先使用LLM决策）"""
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
        # 降级：基于规则投票
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves]
        seer_result = room.night_actions.get("seer_result", "")
        seer_target = room.night_actions.get("seer_target", "")

        if player.role == "werewolf":
            targets = [a for a in alive if a.name not in wolf_names]
            voted = random.choice(targets) if targets else random.choice(alive)
        elif player.role == "seer" and seer_target and "狼人" in seer_result:
            voted = next((a for a in alive if a.name == seer_target), random.choice(alive))
        else:
            voted = random.choice(alive)

    # 二次检查 room 状态（LLM调用可能耗时）
    room = rooms.get(room_id)
    if not room or room._phase_token != token or room.phase != "vote":
        return
    if player.vote:  # 已经投票了（防重）
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
        # 无人投票，直接进入下一夜
        room.add_sys_msg("本轮无人投票，游戏继续。")
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
            room.add_sys_msg(f"【投票】{top_voted[0]}（{ROLES[p.role]['name']}）被投票出局！")

    phase_name = "pk_result" if is_pk else "vote_result"
    room.phase = phase_name

    socketio.emit("vote_result", {
        "result": f"{top_voted[0]} 被投票出局" if len(top_voted) == 1 else f"平票：{'、'.join(top_voted)}",
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
        room.add_sys_msg(f"【遗言】{dp.name}（{ROLES[dp.role]['name']}）请发言（30秒）")
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
    # 更新token，进入新的夜间循环
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
        # 通知所有狼人队友（包含操作者自己）
        wolves = room.get_werewolves()
        for w in wolves:
            if w.sid:
                socketio.emit("wolf_teammate_action", {
                    "player_name": player.name,
                    "target": target_name,
                }, room=w.sid)
        # 更新token，让原等待任务（NIGHT_WAIT超时）检查失败，避免双重推进
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
        # 更新token，让原等待任务失效
        new_token = generate_id()
        room._phase_token = new_token
        socketio.start_background_task(_phase_witch, room_id, new_token)

    elif room.phase == "role_witch" and player.role == "witch":
        witch = player
        if action == "heal" and witch.witch_heal:
            # 只记录解药意向，不立即结算（等待confirm/skip再结算）
            kt = room.night_actions.get("kill_target")
            if kt:
                room.night_actions["witch_heal"] = kt
                witch.witch_heal = False
                emit("action_confirmed", {"action": "heal", "target": kt})
            # 标准规则：用了解药后不能再用毒药，直接结算
            room.night_actions["witch_poison"] = None  # 同夜不能既救人又毒人
            if room.night_actions.get("witch_heal") is False:
                room.night_actions["witch_heal"] = None
            token = room._phase_token
            socketio.start_background_task(_resolve_night, room_id, token)
            return
        elif action == "poison" and witch.witch_poison and target_name:
            # 毒药已选，立即结算
            room.night_actions["witch_poison"] = target_name
            witch.witch_poison = False
            emit("action_confirmed", {"action": "poison", "target": target_name})
        elif action == "skip":
            # 女巫确认跳过（不用毒药或者整体跳过）
            if room.night_actions.get("witch_heal") is False:
                room.night_actions["witch_heal"] = None
            if room.night_actions.get("witch_poison") is False:
                room.night_actions["witch_poison"] = None

        # poison/skip 触发结算，确保 False 状态清空
        if room.night_actions.get("witch_heal") is False:
            room.night_actions["witch_heal"] = None
        if room.night_actions.get("witch_poison") is False:
            room.night_actions["witch_poison"] = None

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
        speech = "（过）"

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

    # 白天发言阶段：必须同时满足：(1) phase正确 (2) 玩家存活 (3) 当前轮到该玩家
    if room.phase not in ("discussion", "pk_discussion"):
        return
    if not player.alive:
        return
    if room.awaiting_speech_for != player.id:
        return

    # 记录发言时的上下文（用于延迟推进时的二次验证）
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
        """延迟推进到下一个发言者（二次验证防止竞态）"""
        socketio.sleep(0.5)
        room = rooms.get(room_id)
        if not room:
            return
        # 二次验证：token 和 phase 必须与发言时一致，否则说明阶段已切换，不推进
        if room._phase_token != token or room.phase != cur_phase:
            return
        # turn_index 必须等于 my_index+1（说明中间没有其他玩家被处理），
        # 否则说明已有其他逻辑修改了 turn_index（避免重复推进）
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

    # 狼人队友之间互发投票情报（只有狼人能看到其他狼人的票）
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
