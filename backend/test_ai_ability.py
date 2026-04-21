"""
测试 AI 决策能力：发言质量、CoT推理、夜间目标选择
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 模拟游戏环境
from main import (
    GameRoom, Player, ROLES, generate_id,
    _build_game_context, _generate_ai_speech, _llm_decide_night_target, _llm_decide_vote,
    USE_LLM
)

def make_room():
    room = GameRoom("test001", "p1")
    room.day = 2
    room.phase = "discussion"
    room.night_actions = {
        "kill_target": "小明",
        "seer_target": "深渊狼",
        "seer_result": "深渊狼 是 狼人",
        "witch_heal": None,
        "witch_poison": None,
    }

    players_data = [
        ("深渊狼", "werewolf", True),
        ("暗月狼", "werewolf", True),
        ("小红帽", "seer", True),
        ("调药师", "witch", True),
        ("村长伯伯", "villager", True),
        ("小明", "villager", False),  # 昨晚死亡
    ]

    for name, role, is_ai in players_data:
        p = Player(name=name, is_ai=is_ai)
        p.role = role
        p.alive = (name != "小明")
        room.players.append(p)

    # 模拟历史发言
    room.messages = [
        {"type": "system", "content": "第1天开始", "day": 1, "name": "系统", "role": None, "role_color": "", "role_name": ""},
        {"type": "speech", "content": "我是村民，昨晚什么都不知道，先听听大家的意见", "day": 1, "name": "村长伯伯", "role": "villager", "role_color": "#3498db", "role_name": "村民"},
        {"type": "speech", "content": "我觉得深渊狼发言很奇怪，东扯西扯不正面回答", "day": 1, "name": "小明", "role": "villager", "role_color": "#3498db", "role_name": "村民"},
        {"type": "speech", "content": "小明你别乱怀疑，我只是在分析局势", "day": 1, "name": "深渊狼", "role": "werewolf", "role_color": "#e74c3c", "role_name": "狼人"},
        {"type": "speech", "content": "调药师一直沉默，是不是有什么秘密", "day": 1, "name": "暗月狼", "role": "werewolf", "role_color": "#e74c3c", "role_name": "狼人"},
        {"type": "system", "content": "【投票】小明 被投票出局！等等，小明没有出局（昨晚狼人攻击）", "day": 1, "name": "系统"},
        {"type": "system", "content": "天亮了，小明 昨晚遇难", "day": 2, "name": "系统"},
    ]
    return room

def test_speech(role_name, role_key):
    print(f"\n{'='*50}")
    print(f"测试【{role_name}】发言能力")
    print('='*50)
    room = make_room()
    player = next(p for p in room.players if p.role == role_key and p.is_ai)
    alive_names = [p.name for p in room.get_alive_players() if p.id != player.id]
    speech = _generate_ai_speech(player, room, alive_names)
    print(f"玩家：{player.name}（{role_name}）")
    print(f"发言：{speech}")
    return speech

def test_night_target(role_name, role_key, action_desc, all_candidates=False):
    print(f"\n{'='*50}")
    print(f"测试【{role_name}】夜间目标选择 - {action_desc}")
    print('='*50)
    room = make_room()
    player = next(p for p in room.players if p.role == role_key and p.is_ai)
    if all_candidates:
        candidates = room.get_alive_players()
    else:
        candidates = [p for p in room.get_alive_players() if p.role != role_key]
    print(f"玩家：{player.name}（{role_name}）")
    print(f"可选目标：{[p.name for p in candidates]}")
    target = _llm_decide_night_target(player, room, candidates, action_desc)
    print(f"选择目标：{target or '（LLM失败，将随机选择）'}")
    return target

def test_vote(role_name, role_key):
    print(f"\n{'='*50}")
    print(f"测试【{role_name}】投票决策")
    print('='*50)
    room = make_room()
    player = next(p for p in room.players if p.role == role_key and p.is_ai)
    alive = room.alive_players_except(player.id)
    print(f"玩家：{player.name}（{role_name}）")
    print(f"存活玩家（可投）：{[p.name for p in alive]}")
    voted = _llm_decide_vote(player, room)
    print(f"投票目标：{voted or '（LLM失败，将随机选择）'}")
    return voted

if __name__ == "__main__":
    print("=" * 60)
    print("狼人杀 AI 决策能力测试")
    print(f"LLM状态：{'✅ DeepSeek 已启用' if USE_LLM else '❌ LLM未配置，使用模板'}")
    print("=" * 60)

    print("\n\n📢 ===== 第一部分：白天发言质量测试 =====")
    test_speech("狼人", "werewolf")
    test_speech("预言家", "seer")
    test_speech("女巫", "witch")
    test_speech("村民", "villager")

    print("\n\n🌙 ===== 第二部分：夜间目标选择测试 =====")
    # 狼人选杀人目标（允许自刀，所有人都是候选）
    test_night_target("狼人", "werewolf", "选择今晚击杀目标（可以击杀任何存活玩家，包括自己）", all_candidates=True)
    # 预言家选查验目标
    test_night_target("预言家", "seer", "选择今晚要查验身份的玩家", all_candidates=False)

    print("\n\n🗳️ ===== 第三部分：投票决策测试 =====")
    test_vote("狼人", "werewolf")
    test_vote("预言家", "seer")
    test_vote("村民", "villager")

    print("\n\n" + "=" * 60)
    print("✅ AI能力测试完成！")
    print("=" * 60)
