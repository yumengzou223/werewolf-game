"""
AI 狼人杀 · 后端 (Flask + SocketIO 实时版)
"""
from gevent import monkey
monkey.patch_all()

import os
import json
import re
import uuid
import random
import asyncio
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

# ====================== 配置 ======================
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend"), static_folder=None)
CORS(app, resources={r"/*": {"origins": "*"}})
app.config["SECRET_KEY"] = "werewolf-secret-2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# ====================== 角色定义 ======================
ROLES = {
    "werewolf":  {"team": "werewolf", "name": "狼人",    "color": "#e74c3c", "glow": "#ff4444"},
    "seer":      {"team": "good",     "name": "预言家",  "color": "#f39c12", "glow": "#ffd700"},
    "witch":     {"team": "good",     "name": "女巫",    "color": "#9b59b6", "glow": "#da70d6"},
    "villager":  {"team": "good",     "name": "村民",    "color": "#3498db", "glow": "#87ceeb"},
}

PHASE_ORDER = ["waiting", "role_kill", "role_seer", "role_witch", "night_result", "day_result", "discussion", "vote", "pk_vote", "lynch_result", "night"]
SPEAK_TIME = 60  # 发言时间秒
VOTE_TIME = 30   # 投票时间秒

# ====================== 工具函数 ======================
def generate_id():
    return uuid.uuid4().hex[:8]

def safe_str(s):
    """处理Unicode中文显示"""
    if isinstance(s, str):
        return s.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
    return str(s)

# ====================== 游戏房间 ======================
rooms = {}  # room_id -> GameRoom

class Player:
    def __init__(self, sid=None, name="", is_ai=False, is_human=False):
        self.id = generate_id()
        self.sid = sid          # socket session id
        self.name = name
        self.role = None
        self.is_ai = is_ai
        self.is_human = is_human
        self.alive = True
        self.vote = None       # 投票目标名字
        self.night_target = None  # 夜间选择目标

        # 女巫状态
        self.witch_heal = True   # 解药是否可用
        self.witch_poison = True  # 毒药是否可用

        # AI状态
        self.ai_decided = False
        self.ai_action = None
        self.ai_speech = ""

        # 发言相关
        self.has_spoken = False
        self.eliminated_by_vote = False
        self.pk_nominated = False  # 是否在PK台上

    def to_dict(self, reveal_role=False):
        d = {
            "id": self.id,
            "name": self.name,
            "role": self.role if reveal_role else None,
            "role_name": ROLES.get(self.role, {}).get("name", "") if reveal_role else None,
            "role_color": ROLES.get(self.role, {}).get("color", "") if reveal_role else "",
            "alive": self.alive,
            "is_ai": self.is_ai,
        }
        return d


class GameRoom:
    def __init__(self, room_id, owner_id):
        self.room_id = room_id
        self.owner_id = owner_id
        self.players = []      # 所有Player对象列表
        self.phase = "waiting"  # waiting | night | role_kill | role_seer | role_witch | night_result | day_result | discussion | vote | vote_result | end
        self.day = 0
        self.turn_index = 0   # 当前发言索引
        self.speaker_list = []  # 存活玩家发言顺序
        self.votes = {}        # {player_id: voted_name}
        self.night_actions = {}  # 夜间结果暂存
        self.last_words_timer = None
        self.phase_timer = None
        self.ai_timers = {}
        self.current_role_turn = None  # 当前夜阶段角色
        self.winner = None
        self.messages = []     # 聊天消息历史
        self.pk_candidates = []  # PK台玩家名

        # AI自动决策计时器（秒后自动决策）
        self.ai_decision_delay = {
            "werewolf": 5,
            "seer": 4,
            "witch": 6,
        }

    def add_player(self, player):
        self.players.append(player)

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
        """分配角色并开始"""
        roles_pool = ["werewolf", "werewolf", "seer", "witch", "villager", "villager"]
        random.shuffle(roles_pool)
        for i, p in enumerate(self.players):
            p.role = roles_pool[i % len(roles_pool)]
            p.alive = True
            p.vote = None
            p.night_target = None
        self.phase = "night"
        self.day = 1
        self.messages = []

    def get_state(self, for_sid=None, reveal_all=False):
        """获取房间状态快照"""
        player_objs = []
        for p in self.players:
            is_me = (for_sid and p.sid == for_sid)
            player_objs.append({
                "id": p.id,
                "name": p.name,
                "role": p.role if (reveal_all or is_me) else None,
                "role_name": ROLES.get(p.role, {}).get("name", "") if (reveal_all or is_me) else None,
                "role_color": ROLES.get(p.role, {}).get("color", "") if (reveal_all or is_me) else "",
                "alive": p.alive,
                "is_ai": p.is_ai,
                "is_me": is_me,
            })

        # 女巫是否知道今晚死的是谁
        witch = self.get_role("witch")
        witch_info = {}
        if witch and self.night_actions.get("kill_target") and witch.alive:
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
            "messages": self.messages[-50:],  # 最近50条
            "votes": self.votes if self.phase in ["vote", "pk_vote"] else {},
            "winner": self.winner,
            "night_actions": self.night_actions if reveal_all else {},  # 完全公开
            "current_role_turn": self.current_role_turn,
            "speech_timer": SPEAK_TIME,
            "vote_timer": VOTE_TIME,
            "witch_info": witch_info,
            "turn_index": self.turn_index,
            "speaker_list": self.speaker_list,
            "pk_candidates": self.pk_candidates,
        }

    def get_role(self, role_name):
        """获取某个角色"""
        for p in self.players:
            if p.role == role_name and p.alive:
                return p
        return None

    def get_werewolves(self):
        return [p for p in self.players if p.role == "werewolf" and p.alive]

    def kill_player(self, player_name, reason="kill"):
        p = self.get_player_by_name(player_name)
        if p and p.alive:
            p.alive = False
            return p
        return None

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
            "speaker_id": speaker_id,
            "name": name,
            "role": role,
            "role_name": ROLES.get(role, {}).get("name", "") if role else "",
            "role_color": ROLES.get(role, {}).get("color", "") if role else "",
            "content": content,
            "type": msg_type,  # speech | system | whisper | vote_result
            "time": time.time(),
        })

    def reset_turn_state(self):
        """重置每轮状态"""
        self.votes = {}
        self.night_actions = {}
        self.turn_index = 0
        for p in self.players:
            p.vote = None
            p.night_target = None
            p.has_spoken = False
            p.ai_decided = False
            p.pk_nominated = False
        self.pk_candidates = []


# ====================== Socket.IO 事件 ======================
_FRONTEND_DIR = os.environ.get("WEREWOLF_FRONTEND", "/repo/frontend")
print(f"[Werewolf] _FRONTEND_DIR={_FRONTEND_DIR} cwd={os.getcwd()} __file__={__file__}", flush=True)
print(f"[Werewolf] frontend exists: {os.path.exists(_FRONTEND_DIR)} listing: {os.listdir(os.path.dirname(_FRONTEND_DIR)) if os.path.exists(os.path.dirname(_FRONTEND_DIR)) else 'N/A'}", flush=True)


connected_sids = {}  # sid -> {room_id, player_id}

@app.route("/")
def index():
    try:
        return send_from_directory(_FRONTEND_DIR, "index.html")
    except Exception as e:
        # Debug: list what's in the frontend directory
        import traceback
        debug_info = f"_FRONTEND_DIR={_FRONTEND_DIR} exists={os.path.exists(_FRONTEND_DIR)} "
        if os.path.exists(_FRONTEND_DIR):
            debug_info += f"files={os.listdir(_FRONTEND_DIR)} "
        return f"Frontend not found. {debug_info} Error: {e}", 500

@app.route("/debug")
def debug():
    import json
    info = {
        "FRONTEND_DIR": _FRONTEND_DIR,
        "FRONTEND_DIR_exists": os.path.exists(_FRONTEND_DIR),
        "cwd": os.getcwd(),
        "__file__": __file__,
    }
    if os.path.exists(_FRONTEND_DIR):
        info["frontend_files"] = os.listdir(_FRONTEND_DIR)
        fe_static = os.path.join(_FRONTEND_DIR, "static")
        if os.path.exists(fe_static):
            info["static_files"] = os.listdir(fe_static)
    parent = os.path.dirname(_FRONTEND_DIR)
    info["parent_exists"] = os.path.exists(parent)
    if os.path.exists(parent):
        info["parent_files"] = os.listdir(parent)
    return jsonify(info)

@app.route("/health")
def health():
    return "ok", 200

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
    return jsonify({
        "room_id": room_id,
        "player_id": player.id,
        "player": player.to_dict(),
    })

@app.route("/api/room/<room_id>", methods=["GET"])
def api_get_room(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    reveal = room.phase in ["end", "waiting"]
    return jsonify(room.get_state(reveal_all=reveal))

@app.route("/api/room/<room_id>/join", methods=["POST"])
def api_join(room_id):
    room = rooms.get(room_id)
    if not room:
        return jsonify({"error": "房间不存在"}), 404
    if room.phase != "waiting":
        return jsonify({"error": "游戏已开始"}), 400
    if len(room.players) >= 6:
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
    if len(room.players) >= 6:
        return jsonify({"error": "房间已满"}), 404

    body = request.get_json() or {}
    role = body.get("role")
    names = {
        "werewolf": ["深渊狼", "暗月狼", "荒野狼"],
        "seer": ["占星师", "先知", "预言少女"],
        "witch": ["灵媒师", "魔药師", "调药师"],
        "villager": ["村長", "獵人", "傻瓜"],
    }
    role_names = names.get(role, names["villager"])
    used_names = {p.name for p in room.players}
    avail = [n for ns in names.values() for n in ns if n not in used_names]
    ai_name = random.choice(avail) if avail else f"AI_{generate_id()[:4]}"

    player = Player(name=ai_name, is_ai=True)
    if role:
        player.role = role
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
    socketio.emit("game_started", room.get_state(reveal_all=True), room=room_id)
    # 客户端负责显示角色揭示动画（3秒），服务器延迟4秒后再开始夜间流程
    threading.Timer(4, start_night_phase, args=[room_id]).start()
    return jsonify({"ok": True})

# ====================== 游戏阶段推进 ======================

def start_night_phase(room_id):
    """开始夜晚"""
    room = rooms.get(room_id)
    print(f"[DEBUG] start_night_phase called for room {room_id}, phase={room.phase if room else 'N/A'}", flush=True)
    if not room or room.phase == "end":
        return

    room.reset_turn_state()
    room.phase = "night"
    room.current_role_turn = None
    room.night_actions = {
        "kill_target": None,
        "seer_target": None,
        "seer_result": None,
        "witch_heal": None,
        "witch_poison": None,
    }

    # 重置所有AI决策状态
    for p in room.players:
        p.ai_decided = False
        p.night_target = None

    socketio.emit("night_start", {
        "day": room.day,
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # 狼人阶段
    threading.Timer(3, start_role_kill, args=[room_id]).start()

def start_role_kill(room_id):
    room = rooms.get(room_id)
    print(f"[DEBUG] start_role_kill called for room {room_id}, phase={room.phase if room else 'N/A'}", flush=True)
    if not room or room.phase == "end":
        return
    # 防止重复触发（多个 client_ready 只会第一次生效）
    if room.phase != "night":
        print(f"[DEBUG] start_role_kill skipped, phase is already {room.phase}", flush=True)
        return
    wolves = room.get_werewolves()
    if not wolves:
        threading.Timer(1, start_role_seer, args=[room_id]).start()
        return

    room.phase = "role_kill"
    room.current_role_turn = "werewolf"
    socketio.emit("role_turn", {
        "role": "werewolf",
        "instruction": "狼人请选择今晚要击杀的目标",
        "targets": [p.to_dict() for p in room.get_alive_players()],
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # AI狼人自动决策
    def ai_wolf_decide():
        wolf = wolves[0]
        alive = room.alive_players_except(wolf.id)
        if not alive:
            threading.Timer(1, start_role_seer, args=[room_id]).start()
            return
        target = random.choice(alive)
        wolf.night_target = target.name
        room.night_actions["kill_target"] = target.name
        room.add_message(wolf.id, f"（狼人行动完成）", "system")
        socketio.emit("player_action_done", {
            "player_id": wolf.id,
            "action": "kill",
            "state": room.get_state(reveal_all=True),
        }, room=room_id)
        # 狼人完成后推进到预言家
        threading.Timer(1, start_role_seer, args=[room_id]).start()

    threading.Timer(5, ai_wolf_decide).start()

def start_role_seer(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return
    seer = room.get_role("seer")
    if not seer:
        threading.Timer(1, start_role_witch, args=[room_id]).start()
        return

    room.phase = "role_seer"
    room.current_role_turn = "seer"
    socketio.emit("role_turn", {
        "role": "seer",
        "instruction": "预言家请选择今晚要查验的目标",
        "targets": [p.to_dict() for p in room.alive_players_except(seer.id)],
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # AI预言家延迟决策（等狼人先完成）
    def ai_seer_decide():
        #  Guard: 只在 seer 阶段执行，否则跳过（防止重复触发）
        if room.phase != "role_seer":
            return
        alive = room.alive_players_except(seer.id)
        if not alive:
            threading.Timer(1, start_role_witch, args=[room_id]).start()
            return
        target = random.choice(alive)
        seer.night_target = target.name
        room.night_actions["seer_target"] = target.name
        result = "狼人" if target.role == "werewolf" else "好人"
        room.night_actions["seer_result"] = f"{target.name} 是 {result}"
        room.add_message(seer.id, f"（查验结果：{target.name} 是 {result}）", "system")
        socketio.emit("player_action_done", {
            "player_id": seer.id,
            "action": "check",
            "result": room.night_actions["seer_result"],
            "state": room.get_state(),
        }, room=room_id)
        threading.Timer(1, start_role_witch, args=[room_id]).start()

    threading.Timer(4, ai_seer_decide).start()  # 在 seer 阶段开始后4秒触发

def start_role_witch(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return
    witch = room.get_role("witch")
    if not witch:
        threading.Timer(1, resolve_night, args=[room_id]).start()
        return

    room.phase = "role_witch"
    room.current_role_turn = "witch"
    kill_target = room.night_actions.get("kill_target")
    socketio.emit("role_turn", {
        "role": "witch",
        "instruction": "女巫，今晚狼人杀的是" + (kill_target if kill_target else "无人") + "，你要用解药救吗？",
        "kill_target": kill_target,
        "can_heal": witch.witch_heal,
        "can_poison": witch.witch_poison,
        "targets": [p.to_dict() for p in room.get_alive_players()],
        "state": room.get_state(),
    }, room=room_id)

    def ai_witch_decide():
        if room.phase != "role_witch":
            return
        kill_t = room.night_actions.get("kill_target")
        # AI女巫：随机策略
        if kill_t and witch.witch_heal and random.random() > 0.3:
            witch.night_target = kill_t
            room.night_actions["witch_heal"] = kill_t
            room.add_message(witch.id, f"（女巫用解药救了{kill_t}）", "system")
        else:
            # 不救
            room.night_actions["witch_heal"] = None
        socketio.emit("player_action_done", {
            "player_id": witch.id,
            "action": "witch_done",
            "state": room.get_state(),
        }, room=room_id)
        # 毒药阶段延迟
        threading.Timer(2, ask_witch_poison, args=[room_id]).start()

    threading.Timer(4, ai_witch_decide).start()

def ask_witch_poison(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return
    witch = room.get_role("witch")
    if not witch or not witch.witch_poison:
        threading.Timer(1, resolve_night, args=[room_id]).start()
        return

    # 通知前端女巫选择毒药（跳过，简单处理）
    socketio.emit("witch_decision", {
        "can_poison": witch.witch_poison,
        "targets": [p.to_dict() for p in room.alive_players_except(witch.id)],
        "state": room.get_state(),
    }, room=room_id)

    def ai_witch_poison_decide():
        if room.phase != "role_witch":
            return
        alive = room.alive_players_except(witch.id)
        wolves = room.get_werewolves()
        # AI女巫有50%概率毒人，优先毒狼人
        if alive and random.random() > 0.5:
            if wolves:
                target = random.choice(wolves)
            else:
                target = random.choice(alive)
            room.night_actions["witch_poison"] = target.name
            room.add_message(witch.id, f"（女巫对{target.name}下毒）", "system")
        socketio.emit("player_action_done", {
            "player_id": witch.id,
            "action": "poison_done",
            "state": room.get_state(),
        }, room=room_id)
        threading.Timer(1, resolve_night, args=[room_id]).start()

    threading.Timer(3, ai_witch_poison_decide).start()

def resolve_night(room_id):
    """夜间结算"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    kill_t = room.night_actions.get("kill_target")
    heal_t = room.night_actions.get("witch_heal")
    poison_t = room.night_actions.get("witch_poison")

    dead_names = []

    # 狼人杀
    if kill_t and kill_t != heal_t:
        dead_names.append(kill_t)
    # 女巫毒
    if poison_t:
        dead_names.append(poison_t)

    dead_players = []
    for name in dead_names:
        p = room.get_player_by_name(name)
        if p and p.alive:
            p.alive = False
            dead_players.append(p)
            room.add_message(p.id, f"{p.name}（{ROLES[p.role]['name']}）死亡", "system")

    room.phase = "night_result"
    socketio.emit("night_result", {
        "kill_target": kill_t,
        "healed": kill_t == heal_t if kill_t else False,
        "poison_target": poison_t,
        "dead": [p.name for p in dead_players],
        "dead_roles": {p.name: ROLES[p.role]["name"] for p in dead_players},
        "seer_result": room.night_actions.get("seer_result"),
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # 检查胜负
    winner = room.check_win()
    if winner:
        threading.Timer(3, end_game, args=[room_id, winner]).start()
    else:
        threading.Timer(4, start_day, args=[room_id]).start()

def start_day(room_id):
    """白天开始"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    room.phase = "day_result"
    room.day += 1
    room.turn_index = 0

    # 构建发言顺序
    alive = room.get_alive_players()
    random.shuffle(alive)
    room.speaker_list = [p.id for p in alive]
    room.reset_turn_state()

    socketio.emit("day_start", {
        "day": room.day,
        "state": room.get_state(),
    }, room=room_id)

    # 开始发言阶段
    threading.Timer(3, start_discussion, args=[room_id]).start()

def start_discussion(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    room.phase = "discussion"
    room.speaker_list = [p.id for p in room.get_alive_players()]
    room.turn_index = 0
    room.reset_turn_state()

    # 依次发言
    def next_speaker():
        room = rooms.get(room_id)
        if not room or room.phase != "discussion":
            return

        alive = room.get_alive_players()
        if room.turn_index >= len(room.speaker_list) or not alive:
            # 发言结束，进入投票
            threading.Timer(1, start_vote, args=[room_id]).start()
            return

        speaker_id = room.speaker_list[room.turn_index]
        speaker = room.get_player(speaker_id)
        if not speaker or not speaker.alive:
            room.turn_index += 1
            threading.Timer(1, next_speaker).start()
            return

        room.current_role_turn = speaker.role
        socketio.emit("speaking_start", {
            "speaker_id": speaker.id,
            "speaker_name": speaker.name,
            "role": speaker.role if speaker.alive else None,
            "role_name": ROLES.get(speaker.role, {}).get("name", "") if speaker.role else None,
            "role_color": ROLES.get(speaker.role, {}).get("color", "") if speaker.role else "",
            "timer": SPEAK_TIME,
            "state": room.get_state(),
        }, room=room_id)

        # AI自动发言
        if speaker.is_ai:
            threading.Timer(2, ai_speak, args=[room_id, speaker.id]).start()

        # 发言计时
        def auto_next():
            room = rooms.get(room_id)
            if not room or room.phase != "discussion":
                return
            room.turn_index += 1
            socketio.emit("speaking_end", {
                "speaker_id": speaker.id,
                "next_speaker_id": room.speaker_list[room.turn_index] if room.turn_index < len(room.speaker_list) else None,
                "state": room.get_state(),
            }, room=room_id)
            threading.Timer(1, next_speaker).start()

        threading.Timer(SPEAK_TIME + 1, auto_next).start()

    threading.Timer(1, next_speaker).start()

def generate_llm_speech(player, room):
    """调用 DeepSeek LLM 生成狼人杀发言"""
    if not DEEPSEEK_API_KEY:
        return None

    role_prompts = {
        "werewolf": ("你是一个狼人杀游戏中的狼人。你知道其他狼人的身份。"
                     "你的目标是在不被发现的情况下带领村民投错人。"
                     "发言要自然，不能太明显暗示自己是狼人，也要适时甩锅给别人。"
                     "用30-50字简短发言，符合中国狼人杀风格。"),
        "seer": ("你是一个狼人杀游戏中的预言家。你已经查验了一些玩家的身份。"
                 "你可以选择报信息或者保持低调。发言要符合神职人员的身份，"
                 "逻辑清晰，不轻易暴露自己但该报信息时要报。"
                 "用30-50字简短发言。"),
        "witch": ("你是一个狼人杀游戏中的女巫。你有救药和毒药。"
                  "发言要显示出你在观察局势但不过分积极参与。"
                  "用30-50字简短发言。"),
        "villager": ("你是一个狼人杀游戏中的村民。你没有任何特殊能力。"
                     "你的目标是分析发言找出狼人。"
                     "发言要显示出你在认真听、认真分析，但不要过度分析显得刻意。"
                     "用30-50字简短发言。"),
    }

    # 收集近期发言上下文
    recent_msgs = []
    for m in room.messages[-8:]:
        recent_msgs.append(f"{m['name']}：{m['content']}")

    prompt_base = role_prompts.get(player.role, role_prompts["villager"])
    context = f"当前是第{room.day}天。"
    if recent_msgs:
        context += f"\n近期发言：\n" + "\n".join(recent_msgs)
    context += f"\n\n你的身份是{ROLES.get(player.role, {}).get('name', '村民')}，你是{'AI' if player.is_ai else '玩家'}。"
    context += "请用一句话发言（30字以内），直接输出发言内容，不需要加引号或其他标记。"

    try:
        import urllib.request, json
        req_data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": prompt_base},
                {"role": "user", "content": context}
            ],
            "max_tokens": 100,
            "temperature": 0.8,
        }
        req = urllib.request.Request(
            DEEPSEEK_API_URL,
            data=json.dumps(req_data).encode("utf-8"),
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            speech = result["choices"][0]["message"]["content"].strip()
            # 清理可能的引号
            speech = speech.strip('"').strip("'").strip()
            if speech and len(speech) <= 200:
                return speech
    except Exception as e:
        print(f"[LLM Error] {e}", flush=True)
    return None

def ai_speak(room_id, player_id):
    """AI玩家发言 - 根据身份生成策略性发言"""
    room = rooms.get(room_id)
    if not room or room.phase != "discussion":
        return
    player = room.get_player(player_id)
    if not player or not player.alive:
        return

    # 分析上下文
    recent_msgs = room.messages[-5:]
    msg_context = {m["name"]: m["content"] for m in recent_msgs}

    speeches = []

    if player.role == "werewolf":
        # 狼人策略：装村民、甩锅、质疑他人
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves if w.id != player.id]
        if wolf_names:
            speeches += [
                f"{wolf_names[0]}这个人的发言有点奇怪，大家有没有注意到？",
                f"{wolf_names[0]}刚才说的话有点矛盾，大家怎么看？",
            ]
        speeches += [
            "我是村民，大家可以相信我的分析。",
            "场上信息还不够清晰，我们再观察一下。",
            "大家不要被狼人带节奏，要看发言逻辑。",
            "我觉得目前最难判断的是场上沉默的人。",
            "我们按顺序来分析，每个人的发言都值得关注。",
        ]

    elif player.role == "seer":
        # 预言家策略：可选择报信息或保持低调
        seer_result = room.night_actions.get("seer_result", "") if hasattr(room, "night_actions") else ""
        if seer_result and "狼人" in seer_result:
            # 查验到狼人，可以选择报或不报（概率性）
            if random.random() > 0.4:
                target = seer_result.split(" 是 ")[0] if " 是 " in seer_result else ""
                speeches += [
                    f"我上一晚查了 {target}，他是狼人！大家记住！",
                    f"我有重要信息：{target} 是狼人，请大家相信我。",
                ]
        elif seer_result:
            if random.random() > 0.6:
                target = seer_result.split(" 是 ")[0] if " 是 " in seer_result else ""
                speeches += [
                    f"我查了 {target}，他是好人，大家可以信任他。",
                ]
        speeches += [
            "大家要注意听发言，逻辑才是关键。",
            "现在信息还不够，我们再观察一轮。",
            "预言家需要空间，请大家给我一点时间。",
            "狼人一定会跳神职，大家要小心区分。",
        ]

    elif player.role == "witch":
        speeches += [
            "我这轮注意到了几个可疑的人，大家来讨论一下。",
            "女巫应该保持隐匿，我在观察场上的局势。",
            "现在场上最可疑的是发言最少的人，大家怎么看？",
            "我们可以先投一个最可疑的人试试水。",
        ]

    else:  # villager
        speeches += [
            "我觉得应该相信预言家的判断，大家不要盲目投票。",
            "到目前为止逻辑还不清晰，我们再听听发言。",
            "场上信息不多，先观察一轮再说。",
            "我认为投票要给明确的理由，不能随便投。",
            "狼人的发言往往很极端，我们要学会分辨。",
            "各位慢的说，我在听，在分析。",
        ]
        # 尝试分析已有消息
        if msg_context:
            suspicious = [name for name, content in msg_context.items()
                          if any(kw in content for kw in ["村民", "好人", "相信", "逻辑"]) and random.random() > 0.5]
            if suspicious:
                speeches.append(f"我注意到 {suspicious[0]} 的发言很实在，我比较信任。")

    # 优先尝试 LLM 生成发言
    llm_speech = generate_llm_speech(player, room)
    if llm_speech:
        speech = llm_speech
        print(f"[LLM Speech] {player.name}({player.role}): {speech}", flush=True)
    else:
        speech = random.choice(speeches) if speeches else "我觉得这轮可以再观察一下。"
    player.ai_speech = speech
    player.has_spoken = True
    room.add_message(player.id, speech, "speech")

    socketio.emit("ai_speech", {
        "player_id": player.id,
        "player_name": player.name,
        "role": player.role,
        "role_color": ROLES.get(player.role, {}).get("color", ""),
        "speech": speech,
        "state": room.get_state(),
    }, room=room_id)

def start_vote(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return
    room.phase = "vote"
    room.votes = {}
    room.reset_turn_state()

    socketio.emit("vote_start", {
        "timer": VOTE_TIME,
        "state": room.get_state(),
    }, room=room_id)

    # AI玩家投票 - 策略性投票
    def ai_vote_all():
        room = rooms.get(room_id)
        if not room or room.phase != "vote":
            return

        # 收集已知信息
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves]

        for p in room.get_alive_players():
            if p.is_ai and not p.vote:
                alive = room.alive_players_except(p.id)
                if not alive:
                    continue

                if p.role == "werewolf":
                    # 狼人：投非狼人，优先投神职或已暴露的预言家查验目标
                    targets = [a for a in alive if a.name not in wolf_names]
                    if targets:
                        # 优先投女巫/预言家（按存活顺序）
                        voted = targets[0] if len(targets) == 1 else random.choice(targets)
                    else:
                        voted = random.choice(alive)
                elif p.role == "seer":
                    # 预言家：投被查验为狼人的
                    seer_t = room.night_actions.get("seer_target")
                    seer_r = room.night_actions.get("seer_result", "")
                    if seer_t and "狼人" in seer_r:
                        voted = next((a for a in alive if a.name == seer_t), random.choice(alive))
                    else:
                        voted = random.choice(alive)
                else:
                    # 村民：随机投票（更好的策略需要记忆历史发言）
                    voted = random.choice(alive)

                p.vote = voted.name
                room.votes[p.id] = voted.name
                socketio.emit("vote_cast", {
                    "player_id": p.id,
                    "player_name": p.name,
                    "voted_name": voted.name,
                    "votes": dict(room.votes),
                    "state": room.get_state(),
                }, room=room_id)

        # 投票计时
        threading.Timer(VOTE_TIME, resolve_vote, args=[room_id]).start()

    threading.Timer(2, ai_vote_all).start()

def resolve_vote(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    from collections import Counter
    tally = Counter(room.votes.values())
    max_votes = max(tally.values()) if tally else 0
    top_voted = [n for n, c in tally.items() if c == max_votes]

    result_msg = ""
    dead_names = []

    if len(top_voted) == 1:
        name = top_voted[0]
        p = room.get_player_by_name(name)
        if p and p.alive:
            dead_names.append(name)
            room.add_message(p.id, f"{name} 被投票出局", "system")
            # 猎人可以带走一人（简化处理：自动结算）
            result_msg = f"{name}（{ROLES[p.role]['name']}）被投票出局！"
    else:
        result_msg = f"平票：{', '.join(top_voted)}，进入PK投票"

    room.phase = "vote_result"
    socketio.emit("vote_result", {
        "result": result_msg,
        "tally": dict(tally),
        "dead": dead_names,
        "pk_candidates": top_voted if len(top_voted) > 1 else [],
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # 处理死亡
    for name in dead_names:
        dp = room.get_player_by_name(name)
        if dp:
            dp.alive = False

    # 检查胜负
    winner = room.check_win()
    if winner:
        threading.Timer(3, end_game, args=[room_id, winner]).start()
    elif len(top_voted) > 1:
        # PK投票
        threading.Timer(3, start_pk_vote, args=[room_id, top_voted]).start()
    else:
        # 进入下一天
        threading.Timer(3, start_night_phase, args=[room_id]).start()

def start_pk_vote(room_id, candidates):
    room = rooms.get(room_id)
    if not room:
        return
    room.phase = "pk_vote"
    room.pk_candidates = candidates
    room.votes = {}

    socketio.emit("pk_vote_start", {
        "candidates": candidates,
        "timer": VOTE_TIME,
        "state": room.get_state(),
    }, room=room_id)

    # AI投票
    def ai_pk_vote():
        room = rooms.get(room_id)
        if not room or room.phase != "pk_vote":
            return
        for p in room.get_alive_players():
            if p.is_ai and not room.votes.get(p.id):
                if candidates:
                    voted = random.choice(candidates)
                    p.vote = voted
                    room.votes[p.id] = voted
                    socketio.emit("vote_cast", {
                        "player_id": p.id,
                        "player_name": p.name,
                        "voted_name": voted,
                        "votes": room.votes,
                        "state": room.get_state(),
                    }, room=room_id)
        threading.Timer(VOTE_TIME, resolve_pk_vote, args=[room_id]).start()

    threading.Timer(2, ai_pk_vote).start()

def resolve_pk_vote(room_id):
    room = rooms.get(room_id)
    if not room:
        return
    from collections import Counter
    tally = Counter(room.votes.values())
    max_votes = max(tally.values()) if tally else 0
    top_voted = [n for n, c in tally.items() if c == max_votes]

    dead_names = []
    if len(top_voted) == 1:
        p = room.get_player_by_name(top_voted[0])
        if p and p.alive:
            dead_names.append(top_voted[0])
            room.add_message(p.id, f"{top_voted[0]} 在PK中被票出局", "system")

    room.phase = "pk_result"
    socketio.emit("pk_result", {
        "tally": dict(tally),
        "dead": dead_names,
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    for name in dead_names:
        dp = room.get_player_by_name(name)
        if dp:
            dp.alive = False

    winner = room.check_win()
    if winner:
        threading.Timer(2, end_game, args=[room_id, winner]).start()
    else:
        threading.Timer(2, start_night_phase, args=[room_id]).start()

def end_game(room_id, winner):
    room = rooms.get(room_id)
    if not room:
        return
    room.phase = "end"
    room.winner = winner
    socketio.emit("game_end", {
        "winner": winner,
        "winner_name": "好人阵营" if winner == "good" else "狼人阵营",
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

# ====================== Socket 事件处理 ======================

@socketio.on("connect")
def on_connect():
    print(f"Client connected: {request.sid}")

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
                room.add_message(p.id, f"{p.name} 离开了房间", "system")
                socketio.emit("player_left", {
                    "player_id": p.id,
                    "state": room.get_state(),
                }, room=room_id)

@socketio.on("ping")
def on_ping():
    pass  # 保活心跳，无需响应

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

    # 发送当前状态
    emit("room_state", room.get_state(for_sid=request.sid))
    socketio.emit("player_online", {
        "player_id": p.id,
        "state": room.get_state(),
    }, room=room_id)

@socketio.on("night_action")
def on_night_action(data):
    """玩家夜间行动"""
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room:
        emit("error", {"message": "房间不存在"})
        return

    player = room.get_player_by_sid(sid)
    if not player or not player.alive:
        emit("error", {"message": "无效玩家"})
        return

    action = data.get("action")  # kill | check | heal | poison
    target_name = data.get("target")

    if room.phase == "role_kill" and player.role == "werewolf":
        player.night_target = target_name
        room.night_actions["kill_target"] = target_name
        room.add_message(player.id, f"（狼人选择击杀{target_name}）", "system")
        emit("action_confirmed", {"action": "kill", "target": target_name})

    elif room.phase == "role_seer" and player.role == "seer":
        target = room.get_player_by_name(target_name)
        if target:
            result = "狼人" if target.role == "werewolf" else "好人"
            room.night_actions["seer_result"] = f"{target_name} 是 {result}"
            emit("action_confirmed", {"action": "check", "result": room.night_actions["seer_result"]})

    elif room.phase == "role_witch" and player.role == "witch":
        witch = player
        if data.get("use_heal") and witch.witch_heal:
            kt = room.night_actions.get("kill_target")
            room.night_actions["witch_heal"] = kt
            witch.witch_heal = False
            room.add_message(player.id, f"（女巫救了{kt}）", "system")
        if target_name and witch.witch_poison:
            room.night_actions["witch_poison"] = target_name
            witch.witch_poison = False
            room.add_message(player.id, f"（女巫对{target_name}下毒）", "system")
        emit("action_confirmed", {"action": "witch_done"})

@socketio.on("vote")
def on_vote(data):
    """投票"""
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room or room.phase not in ["vote", "pk_vote"]:
        return

    player = room.get_player_by_sid(sid)
    if not player or not player.alive:
        return

    voted_name = data.get("target")
    player.vote = voted_name
    room.votes[player.id] = voted_name

    room.add_message(player.id, f"{player.name} 投给了 {voted_name}", "system")
    socketio.emit("vote_cast", {
        "player_id": player.id,
        "player_name": player.name,
        "voted_name": voted_name,
        "votes": room.votes,
        "state": room.get_state(),
    }, room=room_id)

@socketio.on("speech")
def on_speech(data):
    """玩家发言"""
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room or room.phase != "discussion":
        return

    player = room.get_player_by_sid(sid)
    if not player or not player.alive:
        return

    speech = data.get("content", "").strip()
    if speech:
        room.add_message(player.id, speech, "speech")
        player.has_spoken = True
        socketio.emit("player_speech", {
            "player_id": player.id,
            "player_name": player.name,
            "role_color": ROLES.get(player.role, {}).get("color", ""),
            "content": speech,
            "state": room.get_state(),
        }, room=room_id)


# ====================== 前端页面路由 ======================
@app.route("/game")
def serve_game():
    return send_from_directory(_FRONTEND_DIR, "game.html")

@app.route("/game.html")
def serve_game_html():
    return send_from_directory(_FRONTEND_DIR, "game.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(os.path.join(_FRONTEND_DIR, "static"), filename)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5003)), debug=False)
