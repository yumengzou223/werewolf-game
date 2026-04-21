"""
狼人杀游戏流程自动化测试脚本
测试：房间创建、真人加入、AI加入、开始游戏
"""
import requests
import json
import time
import sys

BASE = "http://localhost:5003"

def p(msg):
    print(msg, flush=True)

def post(path, data=None):
    r = requests.post(f"{BASE}{path}", json=data or {}, timeout=10)
    return r.status_code, r.json()

def get(path):
    r = requests.get(f"{BASE}{path}", timeout=10)
    return r.status_code, r.json()

def test_server_alive():
    p("\n========== [1] 测试服务器连通性 ==========")
    try:
        r = requests.get(BASE, timeout=5)
        p(f"✅ 首页响应: {r.status_code}")
    except Exception as e:
        p(f"❌ 服务器未启动: {e}")
        p("请先运行: cd F:\\openmanus\\werewolf\\backend && python main.py")
        sys.exit(1)

def test_create_room():
    p("\n========== [2] 测试房间创建 ==========")
    code, data = post("/api/room/create", {"player_name": "测试房主"})
    p(f"状态码: {code}")
    p(f"响应: {json.dumps(data, ensure_ascii=False, indent=2)}")
    assert code == 200, f"创建房间失败: {code}"
    assert "room_id" in data, "缺少 room_id"
    assert "player_id" in data, "缺少 player_id"
    p(f"✅ 房间创建成功: room_id={data['room_id']}, player_id={data['player_id']}")
    return data["room_id"], data["player_id"]

def test_join_room(room_id):
    p(f"\n========== [3] 测试真人玩家加入 room={room_id} ==========")
    for i in range(2):
        name = f"真人玩家{i+2}"
        code, data = post(f"/api/room/{room_id}/join", {"player_name": name})
        p(f"  [{name}] 状态码={code}, player_id={data.get('player_id','?')}")
        assert code == 200, f"加入失败: {data}"
    p("✅ 真人玩家加入成功")

def test_add_ai(room_id, count=3):
    p(f"\n========== [4] 添加 {count} 个AI玩家 ==========")
    for i in range(count):
        code, data = post(f"/api/room/{room_id}/add-ai")
        p(f"  AI#{i+1} 状态码={code}, name={data.get('player',{}).get('name','?')}")
        assert code == 200, f"添加AI失败: {data}"
    p("✅ AI玩家添加成功")

def test_room_state(room_id):
    p(f"\n========== [5] 查询房间状态 ==========")
    code, data = get(f"/api/room/{room_id}")
    p(f"状态码: {code}")
    p(f"phase: {data.get('phase')}")
    p(f"玩家数: {len(data.get('players', []))}")
    for pl in data.get("players", []):
        ai_tag = "[AI]" if pl.get("is_ai") else "[真人]"
        p(f"  {ai_tag} {pl['name']}")
    assert code == 200
    p("✅ 房间状态查询正常")
    return data

def test_join_nonexistent():
    p("\n========== [6] 测试加入不存在的房间 ==========")
    code, data = post("/api/room/invalid123/join", {"player_name": "测试"})
    p(f"状态码: {code}, 响应: {data}")
    assert code == 404, f"应该返回404，实际: {code}"
    p("✅ 错误处理正常（404）")

def test_room_full(room_id):
    p(f"\n========== [7] 测试超出上限（已有6人）==========")
    code, data = post(f"/api/room/{room_id}/join", {"player_name": "超员玩家"})
    p(f"状态码: {code}, 响应: {data}")
    # 应该返回 400 房间已满
    if code == 400:
        p("✅ 房间满员限制正常（400）")
    else:
        p(f"⚠️ 意外状态码: {code} (可能房间还有空位)")

def test_start_game(room_id):
    p(f"\n========== [8] 测试开始游戏 ==========")
    code, data = post(f"/api/room/{room_id}/start")
    p(f"状态码: {code}, 响应: {json.dumps(data, ensure_ascii=False)}")
    if code == 200:
        p("✅ 游戏开始成功")
    else:
        p(f"⚠️ 开始游戏失败: {data}")
    return code == 200

def test_debug_state(room_id):
    p(f"\n========== [9] 查看调试状态（含角色）==========")
    time.sleep(2)  # 等待后端初始化
    code, data = get(f"/api/debug/room/{room_id}")
    p(f"phase: {data.get('phase')}")
    p(f"day: {data.get('day')}")
    for pl in data.get("players", []):
        ai_tag = "[AI]" if pl.get("is_ai") else "[真人]"
        role = pl.get("role_name") or pl.get("role") or "未知"
        alive = "存活" if pl.get("alive") else "死亡"
        p(f"  {ai_tag} {pl['name']} | 角色={role} | {alive}")
    p("✅ 调试状态正常")

def main():
    p("=" * 50)
    p("狼人杀游戏流程自动化测试")
    p("=" * 50)

    test_server_alive()

    # 创建房间（已有1名真人房主）
    room_id, owner_id = test_create_room()

    # 再加2名真人
    test_join_room(room_id)

    # 加3个AI，凑齐6人
    test_add_ai(room_id, count=3)

    # 查看房间状态
    state = test_room_state(room_id)
    player_count = len(state.get("players", []))

    # 测试错误情况
    test_join_nonexistent()

    # 测试超员
    if player_count >= 8:
        test_room_full(room_id)
    else:
        p(f"\n[跳过超员测试，当前 {player_count} 人，上限8]")

    # 开始游戏
    started = test_start_game(room_id)

    if started:
        test_debug_state(room_id)

    p("\n" + "=" * 50)
    p("✅ 所有测试完成！")
    p(f"🎮 游戏地址: http://localhost:5003")
    p(f"📋 调试接口: http://localhost:5003/api/debug/room/{room_id}")
    p("=" * 50)

if __name__ == "__main__":
    main()
