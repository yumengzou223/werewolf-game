"""
AI 狼人杀 · 后端 (Flask + SocketIO 实时版)
"""
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
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "werewolf", "frontend"), static_folder=None)
CORS(app, resources={r"/*": {"origins": "*"}})
app.config["SECRET_KEY"] = "werewolf-secret-2024"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

DEEPSEEK_API_KEY = ""
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# ====================== 角色定义 ======================
ROLES = {
    "werewolf":  {"team": "werewolf", "name": "狼人",    "color": "#e74c3c", "glow": "#ff4444"},
    "seer":      {"team": "good",     "name": "预言家",  "color": "#f39c12", "glow": "#ffd700"},
    "witch":     {"team": "good",     "name": "女巫",    "color": "#9b59b6", "glow": "#da70d6"},
    "villager":  {"team": "good",     "name": "村民",    "color": "#3498db", "glow": "#87ceeb"},
}

PHASE_ORDER = ["waiting", "role_kill", "role_seer", "role_witch", "night_result", "day_result", "discussion", "vote", "pk_vote", "lynch_result", "night"]
MAX_PLAYERS = 8
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
        self.awaiting_speech_for = None  # 当前等待发言的玩家ID
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
        """分配角色并开始"""
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
        self.awaiting_speech_for = None
        self._current_turn_spoken = None
        for p in self.players:
            p.vote = None
            p.night_target = None
            p.has_spoken = False
            p.ai_decided = False
            p.pk_nominated = False
        self.pk_candidates = []


# ====================== Socket.IO 事件 ======================
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

connected_sids = {}  # sid -> {room_id, player_id}

@app.route("/")
def index():
    return send_from_directory(_FRONTEND_DIR, "index.html")

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

@app.route("/api/debug/room/<room_id>", methods=["GET"])
def api_debug_room(room_id):
    """调试端点：强制揭露所有角色信息（仅用于测试）"""
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
        return jsonify({"error": "房间已满（最多8人）"}), 400

    body = request.get_json() or {}
    player_name = body.get("player_name", f"玩家{len(room.players)+1}")
    player = Player(name=player_name, is_human=True)
    if not room.add_player(player):
        return jsonify({"error": "房间已满（最多8人）"}), 400
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
        return jsonify({"error": "房间已满（最多8人）"}), 404

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
    # 单独给每个玩家发送能看到自己身份的游戏状态
    for p in room.players:
        if p.sid:
            socketio.emit("game_started", room.get_state(for_sid=p.sid), room=p.sid)
    # 开始夜晚（在后台绿色线程中运行，避免阻塞HTTP响应）
    socketio.start_background_task(start_night_phase, room_id)
    return jsonify({"ok": True})

# ====================== 游戏阶段推进 ======================

def start_night_phase(room_id):
    """开始夜晚（后台运行，使用eventlet原生延迟）"""
    room = rooms.get(room_id)
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

    # 3秒后进入狼人阶段（eventlet.sleep = 让出控制权，不阻塞）
    socketio.sleep(3)
    _run_role_kill(room_id)

def _run_role_kill(room_id):
    """执行狼人阶段（同步执行，无Timer依赖）"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    wolves = room.get_werewolves()
    if not wolves:
        socketio.sleep(1)
        _run_role_seer(room_id)
        return

    room.phase = "role_kill"

    # 检查是否有人类狼人需要操作
    human_wolf = next((w for w in wolves if not w.is_ai), None)

    if human_wolf:
        # 有人类狼人，等待其操作
        room.current_role_turn = "werewolf"
        wolf_teammates = [{"id": w.id, "name": w.name} for w in wolves if w.id != human_wolf.id]
        kill_targets = [p.to_dict() for p in room.get_alive_players() if p.role != "werewolf"]
        socketio.emit("role_turn", {
            "role": "werewolf",
            "instruction": "狼人请选择今晚要击杀的目标",
            "teammates": wolf_teammates,
            "targets": kill_targets,
            "state": room.get_state(reveal_all=True),
        }, room=room_id)
        # 人类狼人发 night_action 后端会处理
    else:
        # 全是AI狼人，立即决策
        room.current_role_turn = "werewolf"
        non_wolf_alive = [p for p in room.get_alive_players() if p.role != "werewolf"]
        if non_wolf_alive:
            target = random.choice(non_wolf_alive)
            wolves[0].night_target = target.name
            room.night_actions["kill_target"] = target.name
            room.add_message(wolves[0].id, f"（狼人行动完成）", "system")
            socketio.emit("player_action_done", {
                "player_id": wolves[0].id,
                "action": "kill",
                "state": room.get_state(reveal_all=True),
            }, room=room_id)
        socketio.sleep(1)
        _run_role_seer(room_id)

def _run_role_seer(room_id):
    """执行预言家阶段（同步执行）"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    seer = room.get_role("seer")
    if not seer:
        socketio.sleep(1)
        _run_role_witch(room_id)
        return

    room.phase = "role_seer"
    room.current_role_turn = "seer"
    socketio.emit("role_turn", {
        "role": "seer",
        "instruction": "预言家请选择今晚要查验的目标",
        "targets": [p.to_dict() for p in room.alive_players_except(seer.id)],
        "state": room.get_state(),
    }, room=room_id)

    # AI预言家立即决策（同步）—— 仅AI玩家自动决策
    if seer.is_ai:
        alive = room.alive_players_except(seer.id)
        if alive:
            target = random.choice(alive)
            seer.night_target = target.name
            room.night_actions["seer_target"] = target.name
            result = "狼人" if target.role == "werewolf" else "好人"
            room.night_actions["seer_result"] = f"{target.name} 是 {result}"
            if seer.sid:
                socketio.emit("player_action_done", {
                    "player_id": seer.id,
                    "action": "check",
                    "result": room.night_actions["seer_result"],
                    "state": room.get_state(for_sid=seer.sid),
                }, room=seer.sid)

    # 2秒后进入女巫阶段
    socketio.sleep(2)
    _run_role_witch(room_id)

def _run_role_witch(room_id):
    """执行女巫阶段（同步执行）"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    witch = room.get_role("witch")
    if not witch:
        socketio.sleep(1)
        _resolve_night(room_id)
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

    # 仅AI女巫自动决策解药
    if witch.is_ai:
        kill_t = room.night_actions.get("kill_target")
        if kill_t and witch.witch_heal and random.random() > 0.3:
            witch.night_target = kill_t
            room.night_actions["witch_heal"] = kill_t
            room.add_message(witch.id, f"（女巫用解药救了{kill_t}）", "system")
            socketio.emit("player_action_done", {
                "player_id": witch.id,
                "action": "witch_done",
                "state": room.get_state(),
            }, room=room_id)
        else:
            room.night_actions["witch_heal"] = None
        # AI女巫立即进入毒药阶段
        socketio.sleep(1)
        _run_witch_poison(room_id)
    # 人类女巫：等待 on_night_action 处理

def _run_witch_poison(room_id):
    """女巫毒药阶段"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    witch = room.get_role("witch")
    if not witch or not witch.witch_poison:
        socketio.sleep(1)
        _resolve_night(room_id)
        return

    if not witch.is_ai:
        # 人类女巫：通知前端显示毒药选择
        room.current_role_turn = "witch_poison"
        socketio.emit("role_turn", {
            "role": "witch_poison",
            "instruction": "女巫请选择要毒的玩家，或跳过",
            "targets": [p.to_dict() for p in room.alive_players_except(witch.id)],
            "state": room.get_state(),
        }, room=room_id)
    else:
        # AI女巫毒药决策（50%概率毒狼人）
        alive = room.alive_players_except(witch.id)
        wolves = room.get_werewolves()
        if alive and random.random() > 0.5:
            target = random.choice(wolves) if wolves else random.choice(alive)
            room.night_actions["witch_poison"] = target.name
            room.add_message(witch.id, f"（女巫对{target.name}下毒）", "system")
        else:
            room.night_actions["witch_poison"] = None

        socketio.emit("player_action_done", {
            "player_id": witch.id,
            "action": "poison_done",
            "state": room.get_state(),
        }, room=room_id)
        # AI女巫立即结算
        socketio.sleep(1)
        _resolve_night(room_id)

def _resolve_night(room_id):
    """夜间结算（同步执行）"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    kill_t = room.night_actions.get("kill_target")
    heal_t = room.night_actions.get("witch_heal")
    poison_t = room.night_actions.get("witch_poison")



def _resolve_night(room_id):
    """夜间结算（同步执行）"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    kill_t = room.night_actions.get("kill_target")
    heal_t = room.night_actions.get("witch_heal")
    poison_t = room.night_actions.get("witch_poison")

    dead_names = []
    if kill_t and kill_t != heal_t:
        dead_names.append(kill_t)
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

    # 广播夜间结算给所有人（不含预言家查验结果）
    socketio.emit("night_result", {
        "kill_target": kill_t,
        "healed": kill_t == heal_t if kill_t else False,
        "poison_target": poison_t,
        "dead": [p.name for p in dead_players],
        "dead_roles": {p.name: ROLES[p.role]["name"] for p in dead_players},
        "seer_result": None,  # 不在广播中泄露，由后端单独发给预言家
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    # 单独把预言家查验结果发给预言家本人（其他玩家看不到）
    seer = room.get_role("seer")
    seer_result = room.night_actions.get("seer_result")
    if seer and seer.sid and seer_result:
        socketio.emit("seer_result_private", {
            "seer_result": seer_result,
            "state": room.get_state(for_sid=seer.sid),
        }, room=seer.sid)

    winner = room.check_win()
    if winner:
        socketio.sleep(3)
        _end_game(room_id, winner)
    else:
        socketio.sleep(4)
        socketio.start_background_task(_run_start_day, room_id)

def _run_start_day(room_id):
    """天亮（后台运行）"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return

    room.phase = "day_result"
    room.day += 1
    room.turn_index = 0
    alive = room.get_alive_players()
    random.shuffle(alive)
    room.speaker_list = [p.id for p in alive]
    room.reset_turn_state()

    socketio.emit("day_start", {
        "day": room.day,
        "state": room.get_state(),
    }, room=room_id)

    socketio.sleep(3)
    socketio.start_background_task(_run_discussion, room_id)

def _advance_discussion(room_id):
    """进入下一个发言者（讨论轮次内）"""
    room = rooms.get(room_id)
    if not room or room.phase != "discussion":
        return
    alive = room.get_alive_players()
    if room.turn_index >= len(room.speaker_list) or not alive:
        socketio.sleep(1)
        socketio.start_background_task(_run_vote, room_id)
        return
    speaker_id = room.speaker_list[room.turn_index]
    speaker = room.get_player(speaker_id)
    if not speaker or not speaker.alive:
        room.turn_index += 1
        socketio.start_background_task(_advance_discussion, room_id)
        return

    room.current_role_turn = speaker.role
    room.awaiting_speech_for = speaker.id
    room._current_turn_spoken = None
    socketio.emit("speaking_start", {
        "speaker_id": speaker.id,
        "speaker_name": speaker.name,
        "role": speaker.role if speaker.alive else None,
        "role_name": ROLES.get(speaker.role, {}).get("name", "") if speaker.role else None,
        "role_color": ROLES.get(speaker.role, {}).get("color", "") if speaker.role else "",
        "timer": SPEAK_TIME,
        "state": room.get_state(),
    }, room=room_id)

    if speaker.is_ai:
        socketio.start_background_task(_ai_speak, room_id, speaker.id)

    def auto_next():
        room = rooms.get(room_id)
        if not room or room.phase != "discussion":
            return
        if room._current_turn_spoken == speaker.id:
            return
        if room.awaiting_speech_for == speaker.id:
            return
        room.turn_index += 1
        socketio.emit("speaking_end", {
            "speaker_id": speaker.id,
            "next_speaker_id": room.speaker_list[room.turn_index] if room.turn_index < len(room.speaker_list) else None,
            "state": room.get_state(),
        }, room=room_id)
        socketio.sleep(1)
        socketio.start_background_task(_advance_discussion, room_id)

    socketio.start_background_task(auto_next)

def _run_discussion(room_id):
    """讨论阶段"""
    room = rooms.get(room_id)
    if not room or room.phase == "end":
        return
    room.phase = "discussion"
    room.speaker_list = [p.id for p in room.get_alive_players()]
    room.turn_index = 0
    room.reset_turn_state()
    socketio.start_background_task(_advance_discussion, room_id)

def _ai_speak(room_id, player_id):
    """AI玩家发言"""
    socketio.sleep(2)
    room = rooms.get(room_id)
    if not room or room.phase not in ("discussion", "pk_discussion"):
        return
    player = room.get_player(player_id)
    if not player or not player.alive:
        return

    speeches = []
    if player.role == "werewolf":
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves if w.id != player.id]
        if wolf_names:
            speeches += [
                f"{wolf_names[0]}这个人的发言有点奇怪，大家有没有注意到？",
                "场上信息还不够清晰，我们再观察一下。",
            ]
        speeches += ["我是村民，大家可以相信我的分析。", "大家不要被狼人带节奏。"]
    elif player.role == "seer":
        seer_result = room.night_actions.get("seer_result", "")
        if seer_result and "狼人" in seer_result and random.random() > 0.4:
            parts = seer_result.split(" 是 ")
            if len(parts) == 2:
                speeches.append(f"我查了 {parts[0]}，他是狼人！大家记住！")
        speeches += ["预言家需要空间，请大家给我一点时间。", "狼人一定会跳神职，大家要小心。"]
    elif player.role == "witch":
        speeches += ["我这轮注意到了几个可疑的人，大家来讨论一下。", "女巫应该保持隐匿，我在观察场上局势。"]
    else:
        speeches += [
            "我觉得应该相信预言家的判断，大家不要盲目投票。",
            "狼人的发言往往很极端，我们要学会分辨。",
            "场上信息不多，先观察一轮再说。",
        ]

    speech = random.choice(speeches) if speeches else "这轮可以再观察一下。"
    player.ai_speech = speech
    player.has_spoken = True
    room.add_message(player.id, speech, "speech")
    room.awaiting_speech_for = None
    room._current_turn_spoken = player.id

    socketio.emit("ai_speech", {
        "player_id": player.id,
        "player_name": player.name,
        "role": player.role,
        "role_color": ROLES.get(player.role, {}).get("color", ""),
        "speech": speech,
        "state": room.get_state(),
    }, room=room_id)

    # AI发完言后，立即触发下家（不等auto_next定时器）
    room.turn_index += 1
    socketio.emit("speaking_end", {
        "speaker_id": player.id,
        "next_speaker_id": room.speaker_list[room.turn_index] if room.turn_index < len(room.speaker_list) else None,
        "state": room.get_state(),
    }, room=room_id)
    socketio.sleep(1)
    socketio.start_background_task(_advance_discussion, room_id)
    return

def _run_vote(room_id):
    """投票阶段"""
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

    def ai_vote_task():
        socketio.sleep(VOTE_TIME)
        room = rooms.get(room_id)
        if not room or room.phase != "vote":
            return
        wolves = room.get_werewolves()
        wolf_names = [w.name for w in wolves]
        seer_t = room.night_actions.get("seer_target")
        seer_r = room.night_actions.get("seer_result", "")

        for p in room.get_alive_players():
            if p.is_ai and not p.vote:
                alive = room.alive_players_except(p.id)
                if not alive:
                    continue
                if p.role == "werewolf":
                    targets = [a for a in alive if a.name not in wolf_names]
                    voted = targets[0] if len(targets) == 1 else (random.choice(targets) if targets else random.choice(alive))
                elif p.role == "seer" and seer_t and "狼人" in seer_r:
                    voted = next((a for a in alive if a.name == seer_t), random.choice(alive))
                else:
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

        socketio.sleep(VOTE_TIME + 1)
        _resolve_vote(room_id)

    socketio.start_background_task(ai_vote_task)

def _resolve_vote(room_id):
    room = rooms.get(room_id)
    if not room or room.phase == "end":
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
            room.add_message(p.id, f"{top_voted[0]} 被投票出局", "system")
    else:
        pass

    room.phase = "vote_result"
    socketio.emit("vote_result", {
        "result": f"{', '.join(top_voted)} 票死" if len(top_voted) == 1 else f"平票：{', '.join(top_voted)}",
        "tally": dict(tally),
        "dead": dead_names,
        "pk_candidates": top_voted if len(top_voted) > 1 else [],
        "state": room.get_state(reveal_all=True),
    }, room=room_id)

    for name in dead_names:
        dp = room.get_player_by_name(name)
        if dp:
            dp.alive = False

    winner = room.check_win()
    if winner:
        socketio.sleep(3)
        _end_game(room_id, winner)
    elif len(top_voted) > 1:
        socketio.sleep(3)
        socketio.start_background_task(_run_pk_discussion, room_id, top_voted)
    else:
        socketio.sleep(3)
        socketio.start_background_task(start_night_phase, room_id)

def _run_pk_discussion(room_id, candidates):
    """PK发言阶段：平票候选人依次发言"""
    room = rooms.get(room_id)
    if not room:
        return
    room.phase = "pk_discussion"
    room.pk_candidates = candidates
    # 只让平票的玩家发言
    pk_player_ids = [room.get_player_by_name(n).id for n in candidates if room.get_player_by_name(n)]
    room.speaker_list = pk_player_ids
    room.turn_index = 0
    room.reset_turn_state()

    socketio.emit("pk_discussion_start", {
        "candidates": candidates,
        "state": room.get_state(),
    }, room=room_id)
    socketio.sleep(1)
    socketio.start_background_task(_advance_pk_speaker, room_id)

def _advance_pk_speaker(room_id):
    """PK发言：轮到下一个候选人发言"""
    room = rooms.get(room_id)
    if not room or room.phase != "pk_discussion":
        return
    if room.turn_index >= len(room.speaker_list):
        # 所有候选人发言完毕，进入PK投票
        socketio.sleep(1)
        socketio.start_background_task(_run_pk_vote, room_id)
        return
    speaker_id = room.speaker_list[room.turn_index]
    speaker = room.get_player(speaker_id)
    if not speaker or not speaker.alive:
        room.turn_index += 1
        socketio.start_background_task(_advance_pk_speaker, room_id)
        return

    room.awaiting_speech_for = speaker.id
    socketio.emit("pk_speaking_start", {
        "speaker_id": speaker.id,
        "speaker_name": speaker.name,
        "role": speaker.role,
        "role_name": ROLES.get(speaker.role, {}).get("name", "") if speaker.role else None,
        "role_color": ROLES.get(speaker.role, {}).get("color", "") if speaker.role else "",
        "timer": SPEAK_TIME,
        "state": room.get_state(),
    }, room=room_id)

    if speaker.is_ai:
        socketio.start_background_task(_ai_speak, room_id, speaker.id)

    def pk_auto_next():
        room = rooms.get(room_id)
        if not room or room.phase != "pk_discussion":
            return
        if room.awaiting_speech_for == speaker.id:
            return  # 人类还没发完，等着
        room.turn_index += 1
        socketio.emit("pk_speaking_end", {
            "speaker_id": speaker.id,
            "state": room.get_state(),
        }, room=room_id)
        socketio.sleep(1)
        socketio.start_background_task(_advance_pk_speaker, room_id)

    socketio.start_background_task(pk_auto_next)

def _run_pk_vote(room_id):
    """PK投票阶段"""
    room = rooms.get(room_id)
    if not room:
        return
    room.phase = "pk_vote"
    room.votes = {}

    socketio.emit("pk_vote_start", {
        "candidates": room.pk_candidates,
        "timer": VOTE_TIME,
        "state": room.get_state(),
    }, room=room_id)

    def ai_pk_task():
        socketio.sleep(VOTE_TIME)
        room = rooms.get(room_id)
        if not room or room.phase != "pk_vote":
            return
        for p in room.get_alive_players():
            if p.is_ai and not room.votes.get(p.id):
                voted = random.choice(room.pk_candidates)
                p.vote = voted
                room.votes[p.id] = voted
                socketio.emit("vote_cast", {
                    "player_id": p.id,
                    "player_name": p.name,
                    "voted_name": voted,
                    "votes": room.votes,
                    "state": room.get_state(),
                }, room=room_id)
        socketio.sleep(VOTE_TIME + 1)
        _resolve_pk_vote(room_id)

    socketio.start_background_task(ai_pk_task)

def _resolve_pk_vote(room_id):
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
        socketio.sleep(2)
        _end_game(room_id, winner)
    else:
        socketio.sleep(2)
        socketio.start_background_task(start_night_phase, room_id)

def _end_game(room_id, winner):
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
    socketio.emit("player_online", {
        "player_id": p.id,
        "state": room.get_state(),
    }, room=room_id)

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
        emit("error", {"message": "无效玩家"})
        return
    action = data.get("action")
    target_name = data.get("target")

    if room.phase == "role_kill" and player.role == "werewolf":
        player.night_target = target_name
        room.night_actions["kill_target"] = target_name
        room.add_message(player.id, f"（狼人选择击杀{target_name}）", "system")
        emit("player_action_done", {"action": "kill", "target": target_name, "state": room.get_state()})
        # 人类狼人操作完成后，立即进入预言家阶段
        socketio.start_background_task(_run_role_seer, room_id)

    elif room.phase == "role_seer" and player.role == "seer":
        target = room.get_player_by_name(target_name)
        if target:
            result = "狼人" if target.role == "werewolf" else "好人"
            room.night_actions["seer_result"] = f"{target_name} 是 {result}"
            emit("player_action_done", {"action": "check", "result": room.night_actions["seer_result"], "state": room.get_state()})
        # 人类预言家操作完成后，立即进入女巫阶段
        socketio.start_background_task(_run_role_witch, room_id)

    elif room.phase == "role_witch" and player.role == "witch":
        witch = player
        action_type = data.get("action")

        if action_type == "heal" and witch.witch_heal:
            kt = room.night_actions.get("kill_target")
            if kt:
                room.night_actions["witch_heal"] = kt
                witch.witch_heal = False
                room.add_message(player.id, f"（女巫救了{kt}）", "system")
                emit("player_action_done", {
                    "player_id": player.id,
                    "action": "heal",
                    "target": kt,
                    "state": room.get_state(),
                })
            # 人类女巫用解药后，进入毒药阶段
            socketio.start_background_task(_run_witch_poison, room_id)
            return

        elif action_type == "poison" and witch.witch_poison:
            if target_name:
                room.night_actions["witch_poison"] = target_name
                witch.witch_poison = False
                room.add_message(player.id, f"（女巫对{target_name}下毒）", "system")
                emit("player_action_done", {
                    "player_id": player.id,
                    "action": "poison",
                    "target": target_name,
                    "state": room.get_state(),
                })
            # 人类女巫毒药决策完成，进入夜间结算
            socketio.start_background_task(_resolve_night, room_id)
            return

        elif action_type == "skip_poison":
            # 人类女巫选择不毒人
            room.night_actions["witch_poison"] = None
            socketio.start_background_task(_resolve_night, room_id)
            return

        emit("action_confirmed", {"action": "witch_done"})

@socketio.on("vote")
def on_vote(data):
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
    sid = request.sid
    info = connected_sids.get(sid, {})
    room_id = info.get("room_id")
    room = rooms.get(room_id)
    if not room or room.phase not in ("discussion", "pk_discussion"):
        return
    player = room.get_player_by_sid(sid)
    if not player or not player.alive:
        return
    speech = data.get("content", "").strip()
    if not speech:
        return

    player.has_spoken = True
    room.add_message(player.id, speech, "speech")
    socketio.emit("player_speech", {
        "player_id": player.id,
        "player_name": player.name,
        "role_color": ROLES.get(player.role, {}).get("color", ""),
        "content": speech,
        "state": room.get_state(),
    }, room=room_id)

    # 如果是人类正在等待发言，立即触发下家切换
    if room.awaiting_speech_for != player.id:
        return

    room.awaiting_speech_for = None
    room._current_turn_spoken = player.id
    if room.phase == "pk_discussion":
        # PK发言阶段：直接进入下一PK候选人
        room.turn_index += 1
        socketio.emit("pk_speaking_end", {
            "speaker_id": player.id,
            "state": room.get_state(),
        }, room=room_id)
        socketio.sleep(1)
        socketio.start_background_task(_advance_pk_speaker, room_id)
    else:
        # 正常讨论阶段
        room.turn_index += 1
        socketio.emit("speaking_end", {
            "speaker_id": player.id,
            "next_speaker_id": room.speaker_list[room.turn_index] if room.turn_index < len(room.speaker_list) else None,
            "state": room.get_state(),
        }, room=room_id)
        socketio.sleep(1)
        socketio.start_background_task(_advance_discussion, room_id)

@app.route("/game")
def serve_game():
    return send_from_directory(_FRONTEND_DIR, "game.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(os.path.join(_FRONTEND_DIR, "static"), filename)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5003)), debug=False, log=False)
