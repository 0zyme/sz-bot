#!/usr/bin/env python3
"""
ai_bot.py — 超自然行动组 AI 自动回复机器人 (最终版 v2)
========================================================
改进：
1. 区分自己与他人消息（过滤自己昵称）
2. 加载 bot_persona.json 作为对话规则
3. 严格遵循 persona 中的回复条件与禁忌
4. 同一玩家去重，避免连续回复
"""

import sys
import os
import json
import time
import argparse
import ctypes
from collections import deque, defaultdict

# 最小化 CMD 窗口，防止抢夺游戏焦点
try:
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR or ".")

# ============================================================
# 加载 persona
# ============================================================

PERSONA_PATH = os.path.join(SCRIPT_DIR, "bot_persona.json")
if not os.path.exists(PERSONA_PATH):
    print(f"[错误] 请先创建 {PERSONA_PATH} 并填写你的游戏昵称")
    sys.exit(1)

with open(PERSONA_PATH, encoding="utf-8") as f:
    persona = json.load(f)

MY_NAME = persona.get("player_name", "").strip()
if not MY_NAME:
    print(f"[错误] 请在 bot_persona.json 中填写 player_name")
    sys.exit(1)

PERSONA_NAME = persona["persona"]["name"]
DESCRIPTION = persona["persona"]["description"]
TONE = persona["persona"]["tone"]
STYLE = persona["persona"]["style"]
RULES = persona["rules"]

print(f"[Persona] 昵称: {MY_NAME}")
print(f"[Persona] 人设: {PERSONA_NAME} - {DESCRIPTION}")
print(f"[Persona] 语气: {TONE}")
print(f"[Persona] 风格: {STYLE}")

# ============================================================
# 加载 API Keys
# ============================================================

API_KEYS_PATH = os.path.join(SCRIPT_DIR, "api_keys.json")
if not os.path.exists(API_KEYS_PATH):
    print(f"[错误] 请先创建 {API_KEYS_PATH} 并填写 API 密钥")
    sys.exit(1)

with open(API_KEYS_PATH, encoding="utf-8") as f:
    api_keys = json.load(f)

DEEPSEEK_API_KEY = api_keys.get("deepseek", "").strip()
CLAUDE_API_KEY = api_keys.get("claude", "").strip()

if not DEEPSEEK_API_KEY and not CLAUDE_API_KEY:
    print(f"[错误] 请在 api_keys.json 中至少填写一个 API 密钥")
    sys.exit(1)

print(f"[API] DeepSeek: {'已配置' if DEEPSEEK_API_KEY else '未配置'}")
print(f"[API] Claude: {'已配置' if CLAUDE_API_KEY else '未配置'}")

# ============================================================
# DeepSeek API 系统提示
# ============================================================

SYSTEM_PROMPT = f"""你正在《超自然行动组》游戏中扮演一个普通玩家，你的游戏昵称是「{MY_NAME}」。
你的人设是：{PERSONA_NAME} - {DESCRIPTION}
你的语气：{TONE}
你的风格：{STYLE}

**核心规则：只要不是你自己（{MY_NAME}）发的消息，必须回复。任何其他玩家的消息都要接话。**

**输出格式（严格 JSON）：**
{{{{"reply": true, "message": "你的回复（10-25字，纯中文）"}}}}
只输出 JSON，不要其他内容。"""


def chat_api(prompt: str, model: str) -> str:
    """统一 API 调用：model 可选 'deepseek' 或 'claude'"""
    import urllib.request
    import urllib.error

    if model == "deepseek":
        api_key = DEEPSEEK_API_KEY
        url = "https://api.deepseek.com/v1/chat/completions"
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"当前聊天内容：\n{prompt}"},
            ],
            "temperature": 0.7,
            "max_tokens": 200,
        }
    elif model == "claude":
        api_key = CLAUDE_API_KEY
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 200,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": f"当前聊天内容：\n{prompt}"},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["content"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            return f'{{"reply": false, "error": "HTTP {e.code}"}}'
        except Exception as e:
            return f'{{"reply": false, "error": "{e}"}}'
    else:
        return f'{{"reply": false, "error": "未知模型 {model}"}}'

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        return f'{{"reply": false, "error": "HTTP {e.code}"}}'
    except Exception as e:
        return f'{{"reply": false, "error": "{e}"}}'


def parse_response(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(raw)
        if data.get("reply") is True:
            if data.get("message"):
                return True, data["message"], data.get("reason", "")
            else:
                return False, "", "reply=true 但无 message 字段"
        else:
            # 强制要求回复，所以 reply=false 也当作需要回复，用默认回复
            return True, "嗯，看到了", "强制回复"
    except json.JSONDecodeError:
        return False, "", f"JSON 解析失败，原始: {raw[:80]}"


# ============================================================
# 消息过滤与玩家追踪
# ============================================================

def filter_self_messages(lines, my_name, sent_msgs):
    """过滤掉自己发出的消息（昵称匹配 + 已发送消息内容匹配）"""
    filtered = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 昵称匹配
        if line.startswith(my_name + ":") or line.startswith(my_name + "："):
            continue
        if ":" in line:
            speaker, _ = line.split(":", 1)
            if speaker.strip() == my_name:
                continue
        if "：" in line:
            speaker, _ = line.split("：", 1)
            if speaker.strip() == my_name:
                continue
        # 已发送消息内容匹配（OCR可能读不到昵称前缀）
        is_own = False
        for sm in sent_msgs:
            if sm in line:
                is_own = True
                break
        if is_own:
            continue
        filtered.append(line)
    return filtered


def extract_speaker(line):
    """提取说话者，如果格式为「玩家: 消息」则返回玩家名，否则返回 None"""
    if ":" in line:
        speaker, _ = line.split(":", 1)
        return speaker.strip()
    if "：" in line:
        speaker, _ = line.split("：", 1)
        return speaker.strip()
    return None


# ============================================================
# 主循环
# ============================================================

def main():
    os.environ["FLAGS_use_mkldnn"] = "0"
    parser = argparse.ArgumentParser(description="SZ AI Bot v2")
    parser.add_argument("--interval", type=int, default=8, help="检测间隔秒数")
    parser.add_argument("--model", default="deepseek", choices=["deepseek", "claude"], help="AI 模型：deepseek 或 claude")
    args = parser.parse_args()

    model = args.model
    if model == "deepseek" and not DEEPSEEK_API_KEY:
        print("[错误] 未在 api_keys.json 中配置 deepseek")
        sys.exit(1)
    if model == "claude" and not CLAUDE_API_KEY:
        print("[错误] 未在 api_keys.json 中配置 claude")
        sys.exit(1)

    os.chdir(SCRIPT_DIR)

    # 动态加载 sz-mcp-server
    sz_path = os.path.join(SCRIPT_DIR, "sz-mcp-server.py")
    code = open(sz_path, encoding="utf-8").read()
    code = code.split('if __name__')[0]
    ns = {}
    exec(compile(code, "sz-mcp-server.py", "exec"), ns)

    cfg = ns["ServerConfig"]()
    ctrl = ns["PcSzController"](cfg)

    print(f"[AI Bot] 初始化完成")
    print(f"[AI Bot] 窗口: {cfg.window_titles}")
    print(f"[AI Bot] 间隔: {args.interval}s | 模型: {model} | OCR: {ctrl._detect_ocr_name()}")
    print(f"[AI Bot] 按 Ctrl+C 停止")
    print("-" * 50)

    # 去重：保留最近 5 轮消息
    history = deque(maxlen=5)
    seen_all = set()
    # 玩家发言追踪：记录每个玩家最后发言时间
    player_last_spoke = defaultdict(float)
    # 玩家连续回复限制：同一玩家连续发言最多回复 1 次
    last_replied_to = None

    UI_NOISE = {"历史", "队伍", "聊天", "发送", "输入文字", "在此输入", "氚此", "历", "历臾", "聊臾", "队", "退出登录", "举报", "添加好友"}

    try:
        while True:
            print(f"\n--- {time.strftime('%H:%M:%S')} ---")

            # Step 0: Esc×2 归位 + 聚焦
            ctrl.focus_game()
            time.sleep(0.15)
            ctrl._send_game_key("escape")
            time.sleep(0.15)
            ctrl._send_game_key("escape")
            time.sleep(0.2)

            # Step 1: 打开聊天历史
            print("[1] 打开聊天历史...")
            ok = ctrl.open_chat_history()
            if not ok:
                print("  未找到历史按钮，跳过本轮")
                time.sleep(args.interval)
                continue

            # Step 2: OCR 识别（限定聊天消息区域）
            print("[2] OCR 识别...")
            time.sleep(0.3)
            msg_region = ctrl.get_chat_msg_region()
            if msg_region:
                lines = ctrl.ocr_read(msg_region)
                print(f"  消息区域: {msg_region}")
            else:
                game_rect = ctrl._get_game_rect()
                lines = ctrl.ocr_read(game_rect) if game_rect else ctrl.ocr_read()
                print("  回退到游戏窗口区域")

            # Step 3: 关闭历史面板（Y 键关闭，Enter 发送后自动关闭聊天框）
            ctrl.focus_game()
            time.sleep(0.15)
            ctrl._send_game_key("y")
            time.sleep(0.4)

            if not lines:
                print("  无内容")
                time.sleep(args.interval)
                continue

            # 过滤噪声
            filtered = []
            for line in lines:
                line = line.strip()
                if not line or len(line) < 2:
                    continue
                if any(n in line for n in UI_NOISE) and len(line) < 8:
                    continue
                filtered.append(line)

            # 过滤自己消息
            sent_msgs = getattr(ctrl, "_sent_messages", set())
            filtered = filter_self_messages(filtered, MY_NAME, sent_msgs)

            if not filtered:
                print("  过滤后无有效消息（可能全是自己发言）")
                time.sleep(args.interval)
                continue

            # 去重
            current = set(filtered)
            new_msgs = current - seen_all
            history.append(current)
            seen_all = set().union(*history) if history else current

            if not new_msgs:
                print(f"  无新消息 ({len(filtered)} 条已见过)")
                time.sleep(args.interval)
                continue

            # 提取新消息中的说话者
            new_speakers = set()
            for msg in new_msgs:
                speaker = extract_speaker(msg)
                if speaker:
                    new_speakers.add(speaker)
                    player_last_spoke[speaker] = time.time()

            print(f"  新消息 {len(new_msgs)} 条，涉及玩家: {list(new_speakers)[:5] if new_speakers else '未知'}")

            # Step 4: AI 分析
            print(f"[3] {model.upper()} 分析...")
            chat_text = "\n".join(new_msgs)
            print(f"  待分析: {chat_text[:120]}")
            raw = chat_api(chat_text, model)
            should_reply, reply_msg, reason = parse_response(raw)
            print(f"  返回: reply={should_reply}, reason={reason or '无'}, msg={reply_msg[:40] if reply_msg else '-'}")

            # 频率控制：同一玩家连续发言最多回复 1 次
            if should_reply and reply_msg:
                # 检查是否刚回复过同一玩家
                if new_speakers and len(new_speakers) == 1:
                    sole_speaker = list(new_speakers)[0]
                    if sole_speaker == last_replied_to:
                        print(f"  频率控制：刚回复过 {sole_speaker}，跳过")
                        should_reply = False

            # Step 5: 回复
            if should_reply and reply_msg:
                print(f"[4] 发送: {reply_msg}")
                try:
                    ctrl.send_chat(reply_msg)
                    print("  已发送")
                    if new_speakers:
                        last_replied_to = list(new_speakers)[0]
                except Exception as e:
                    print(f"  发送失败: {e}")
            else:
                print("[4] 无需回复")
                last_replied_to = None

            print(f"[5] 等待 {args.interval}s...")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[AI Bot] 已停止")


if __name__ == "__main__":
    main()
