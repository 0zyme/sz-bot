#!/usr/bin/env python3
"""
sz-mcp-server — 超自然行动组 MCP 服务器
====================================================
项目概述：
    为 PC 版《超自然行动组》设计的 MCP（Model Context Protocol）服务器。
    将游戏窗口截图、OCR 读屏、键盘输入和聊天发送封装为 MCP/JSON-RPC 工具，
    供 AI 客户端在局域网内远程控制游戏。

核心思路：
    将游戏窗口抽象为一组可由 AI 调用的标准化工具，
    使 AI 能像人类一样"看"（截图+OCR）、"点"（键盘输入）、"说"（聊天发送）游戏界面。

架构分层：
    传输层 → 协议层 → 工具路由层 → 控制器层

版本：v0.3.0  |  代码规模：约 650 行 Python  |  目标应用：PC 版《超自然行动组》(Preternatural)
"""

import sys
import os
import io
import json
import base64
import ctypes
import time
import traceback
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dataclasses import dataclass, field
from PIL import ImageEnhance
from typing import Optional, Callable, Any


# ============================================================
# 一、ServerConfig — 配置数据类
# ============================================================

@dataclass
class ServerConfig:
    """集中管理所有运行参数，使用 dataclass 实现类型安全与默认值"""

    # --- HTTP 传输 ---
    host: str = "127.0.0.1"
    port: int = 9800
    token: str = ""  # Bearer Token，非本地地址时强制要求

    # --- 窗口匹配 ---
    window_titles: list = field(default_factory=lambda: [
        "Preternatural",
        "preternatural",
        "preternatural.exe",
        "超自然行动组",
    ])

    # --- 截图 ---
    monitor: int = 1  # mss 截图所用的显示器编号
    screenshot_scale: float = 1.0
    screenshot_max_width: int = 1920
    screenshot_max_height: int = 1080
    chat_region: tuple = (700, 70, 300, 350)  # (x, y, w, h) 聊天内容区域

    # --- 输入 ---
    input_backend: str = "pydirectinput"  # auto / pyautogui / pydirectinput

    # --- 聊天 ---
    chat_key: str = "y"  # 打开聊天输入框的按键（SZ 游戏用 Y 键）

    # --- 运行时 ---
    _window_hwnd: Any = None  # 缓存窗口句柄


# ============================================================
# 二、PcSzController — 核心控制器
# ============================================================

class PcSzController:
    """封装所有游戏交互能力，分为五大子系统：输入/窗口/截图/OCR/聊天"""

    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self._input_backend = None
        self._ocr_engine = None
        self._ocr_name = "none"
        self._window_hwnd = None

    # --------------------------------------------------
    # 2.1 依赖管理与优雅降级
    # --------------------------------------------------

    @property
    def pa(self):
        """延迟加载 pyautogui"""
        if self._input_backend is None:
            self._init_input_backend()
        import pyautogui
        return pyautogui

    def _init_input_backend(self):
        backend = self.cfg.input_backend
        if backend == "pydirectinput":
            try:
                import pydirectinput
                self._input_backend = pydirectinput
            except ImportError:
                backend = "auto"
        if backend in ("auto", "pyautogui"):
            import pyautogui
            self._input_backend = pyautogui

    def _get_input(self):
        """返回当前输入后端模块"""
        if self._input_backend is None:
            self._init_input_backend()
        return self._input_backend

    # --------------------------------------------------
    # 2.2 输入模拟子系统
    # --------------------------------------------------

    def _tap_key(self, key: str):
        """
        单键按压。自动识别组合键（如 ctrl+c 拆分为修饰键+主键），
        调用对应后端的 keyDown / keyUp。
        """
        inp = self._get_input()
        parts = key.lower().split("+")
        if len(parts) > 1:
            # 组合键：先按修饰键，按主键，再释放修饰键
            modifiers = parts[:-1]
            main = parts[-1]
            for mod in modifiers:
                inp.keyDown(mod.strip())
            inp.keyDown(main.strip())
            inp.keyUp(main.strip())
            for mod in reversed(modifiers):
                inp.keyUp(mod.strip())
        else:
            inp.keyDown(parts[0].strip())
            inp.keyUp(parts[0].strip())

    def _press_combo(self, keys: list):
        """多键组合：保证操作原子性"""
        inp = self._get_input()
        for k in keys:
            inp.keyDown(k.strip())
        for k in reversed(keys):
            inp.keyUp(k.strip())

    def _paste_text(self, text: str):
        """
        中文文本粘贴 —— 零副作用设计：
        1. 保存当前剪贴板
        2. 写入待发送文本
        3. Ctrl+V 粘贴
        4. 恢复原始剪贴板
        """
        import pyperclip

        old_clip = ""
        try:
            old_clip = pyperclip.paste()
        except Exception:
            pass

        pyperclip.copy(text)
        time.sleep(0.05)
        self._tap_key("ctrl+v")
        time.sleep(0.1)
        pyperclip.copy(old_clip)

    def press_key(self, key: str):
        """公开：按下指定按键"""
        self._tap_key(key)

    def type_text(self, text: str):
        """公开：粘贴文本到当前焦点位置"""
        self._paste_text(text)

    def _send_game_key(self, key_name: str) -> bool:
        """通过 keybd_event 驱动级注入按键，兼容 DirectInput 游戏"""
        import ctypes

        user32 = ctypes.windll.user32

        kn = key_name.lower()
        if kn == "y":
            vk = 0x59
        elif kn == "enter" or kn == "return":
            vk = 0x0D
        elif kn == "esc" or kn == "escape":
            vk = 0x1B
        elif kn == "ctrl":
            vk = 0x11
        elif kn == "v":
            vk = 0x56
        elif len(kn) == 1:
            vk = ord(kn.upper())
        else:
            return False

        user32.keybd_event(vk, 0, 0, 0)       # KEYDOWN
        time.sleep(0.03)
        user32.keybd_event(vk, 0, 2, 0)       # KEYUP
        return True

    # --------------------------------------------------
    # 2.3 窗口管理子系统
    # --------------------------------------------------

    def find_window(self) -> Optional[int]:
        """按标题列表模糊匹配游戏窗口，返回 hwnd 或 None"""
        try:
            import pygetwindow as gw
        except ImportError:
            return None

        for w in gw.getAllWindows():
            t = w.title
            if not t:
                continue
            for candidate in self.cfg.window_titles:
                if candidate.lower() in t.lower():
                    hwnd = w._hWnd
                    if hwnd:
                        self.cfg._window_hwnd = hwnd
                        self._window_hwnd = hwnd
                        return hwnd
        return None

    def focus_game(self) -> bool:
        """激活游戏窗口并强制置前，返回是否成功"""
        hwnd = self.find_window()
        if not hwnd:
            return False
        self._force_foreground_window(hwnd)
        time.sleep(0.15)
        self._click_game_window()
        return True

    def _click_game_window(self):
        """在游戏窗口安全区域点击一下，确保窗口获得真正的输入焦点"""
        rect = self._get_game_rect()
        if not rect:
            return
        import pyautogui
        left, top, width, height = rect
        # 点击窗口中央靠上区域（安全区，避免误触游戏按钮）
        cx = left + width // 2
        cy = top + height // 5
        pyautogui.moveTo(cx, cy)
        pyautogui.click()

    def _force_foreground_window(self, hwnd: int):
        """
        Win32 API 级别前台抢占，突破 Windows 前台锁定机制：
        1. SetWindowPos 将窗口置顶
        2. AttachThreadInput 附加输入线程
        3. 模拟按下 Alt 键（绕开前台限制）
        """
        user32 = ctypes.windll.user32

        # 如果窗口最小化则恢复
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        # Step 1: SetWindowPos 置顶
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        )

        # Step 2: AttachThreadInput
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_process_id = ctypes.c_ulong()
        target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_process_id))
        user32.AttachThreadInput(current_thread, target_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(current_thread, target_thread, False)

        # Step 3: 模拟 Alt 键激活菜单栏
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        time.sleep(0.02)
        user32.keybd_event(0x12, 0, 2, 0)  # Alt up

        # 取消 TOPMOST
        HWND_NOTOPMOST = -2
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)

    def get_window_info(self) -> dict:
        """返回窗口相关信息"""
        hwnd = self._window_hwnd or self.find_window()
        if hwnd:
            return {"hwnd": hwnd, "found": True}
        return {"found": False}

    # --------------------------------------------------
    # 2.4 截图子系统
    # --------------------------------------------------

    def screenshot_image(self, region: Optional[tuple] = None):
        """
        mss 高性能截图 → PIL.Image。
        region: (x, y, w, h) 裁剪区域，None 则全屏
        """
        import mss
        from PIL import Image

        with mss.mss() as sct:
            if region:
                x, y, w, h = region
                monitor = {"top": y, "left": x, "width": w, "height": h}
            else:
                monitor = sct.monitors[self.cfg.monitor]
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        # 缩放与限幅
        scale = self.cfg.screenshot_scale
        if scale != 1.0:
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        max_w = self.cfg.screenshot_max_width
        max_h = self.cfg.screenshot_max_height
        if img.width > max_w or img.height > max_h:
            img.thumbnail((max_w, max_h), Image.LANCZOS)

        return img

    def screenshot_base64(self, region: Optional[tuple] = None) -> str:
        """截图 → Base64 编码 PNG，供 AI 视觉模型直接消费"""
        img = self.screenshot_image(region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    # --------------------------------------------------
    # 2.5 OCR 子系统
    # --------------------------------------------------

    def _detect_ocr_name(self) -> str:
        """
        按优先级自动探测可用 OCR 引擎：
        EasyOCR（游戏字体优化，CPU 模式）→ PaddleOCR → 无 OCR
        """
        engines = [
            ("easyocr", self._check_easyocr),
        ]
        for name, checker in engines:
            if checker():
                return name
        return "none"

    def _check_easyocr(self) -> bool:
        try:
            import easyocr
            return True
        except ImportError:
            return False

    def _check_paddleocr(self) -> bool:
        try:
            import paddleocr
            return True
        except ImportError:
            return False

    def _init_ocr(self):
        """按需初始化 OCR 引擎"""
        if self._ocr_engine is not None:
            return
        self._ocr_name = self._detect_ocr_name()

        if self._ocr_name == "easyocr":
            import easyocr
            self._ocr_engine = easyocr.Reader(
                ["ch_sim", "en"], gpu=False
            )
        elif self._ocr_name == "paddleocr":
            import numpy as np
            from paddleocr import PaddleOCR
            self._ocr_engine = PaddleOCR(lang="ch")

    def ocr_read(self, region: Optional[tuple] = None) -> list:
        """
        对指定区域截图并 OCR，返回文本行列表。
        自动合并被拆分的消息行（如冒号后换行）。
        图像预处理：2x 放大 + 对比度增强，提升 EasyOCR 对游戏小字的识别率。
        """
        import numpy as np
        self._init_ocr()

        if region is None:
            region = self.cfg.chat_region

        img = self.screenshot_image(region)

        # 预处理：放大 2x + 对比度增强
        from PIL import Image as PILImage
        img = img.resize((img.width * 2, img.height * 2), PILImage.LANCZOS)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)

        img_arr = np.array(img)

        if self._ocr_name == "easyocr":
            results = self._ocr_engine.readtext(img_arr)
            raw_lines = [item[1] for item in results]
        elif self._ocr_name == "paddleocr":
            result = self._ocr_engine.ocr(img_arr)
            if result and result[0]:
                raw_lines = [line[1][0] for line in result[0]]
            else:
                raw_lines = []
        else:
            return ["OCR 不可用"]

        # 合并被拆分的消息行
        merged = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            # 如果当前行以冒号结尾或包含冒号且冒号前长度>1（可能是玩家名:消息）
            if ":" in line or "：" in line:
                merged.append(line)
            else:
                # 没有冒号，可能是消息的续行，合并到上一行
                if merged:
                    merged[-1] = merged[-1] + line
                else:
                    merged.append(line)
        return merged

    def ocr_find_text(self, target: str, region: Optional[tuple] = None) -> Optional[dict]:
        """
        在指定区域 OCR 并查找目标文本，返回匹配到的坐标信息
        {text, center_x, center_y, bounds: (x1,y1,x2,y2)} 或 None
        """
        self._init_ocr()

        if region is None:
            region = self.cfg.chat_region

        import numpy as np
        img = self.screenshot_image(region)
        img_arr = np.array(img)
        rx, ry, rw, rh = region

        if self._ocr_name == "easyocr":
            results = self._ocr_engine.readtext(img_arr)
            for (bbox, text, confidence) in results:
                if target in text:
                    (x1, y1), (x2, y2) = bbox[0], bbox[2]
                    return {
                        "text": text,
                        "center_x": rx + int((x1 + x2) / 2),
                        "center_y": ry + int((y1 + y2) / 2),
                        "bounds": (rx + int(x1), ry + int(y1), rx + int(x2), ry + int(y2)),
                    }
        elif self._ocr_name == "paddleocr":
            result = self._ocr_engine.ocr(img_arr)
            if result and result[0]:
                for line in result[0]:
                    bbox, (text, conf) = line
                    if target in text:
                        x1, y1 = bbox[0]
                        x2, y2 = bbox[2]
                        return {
                            "text": text,
                            "center_x": rx + int((x1 + x2) / 2),
                            "center_y": ry + int((y1 + y2) / 2),
                            "bounds": (rx + int(x1), ry + int(y1), rx + int(x2), ry + int(y2)),
                        }
        return None

    def read_screen(self) -> str:
        """截图 → OCR 识别屏幕文字并返回"""
        self.focus_game()
        lines = self.ocr_read()
        return "\n".join(lines)

    # --------------------------------------------------
    # 2.6 聊天子系统
    # --------------------------------------------------

    def open_chat(self):
        """按聊天键打开游戏内聊天输入框（SZ 游戏用 Y 键）"""
        self.focus_game()
        time.sleep(0.1)
        self._send_game_key(self.cfg.chat_key)
        time.sleep(0.3)

    def _get_game_rect(self) -> Optional[tuple]:
        """获取游戏窗口的屏幕坐标 (left, top, width, height)"""
        try:
            import pygetwindow as gw
            for t in self.cfg.window_titles:
                wins = gw.getWindowsWithTitle(t)
                for w in wins:
                    if w.title.strip():
                        return (w.left, w.top, w.width, w.height)
        except Exception:
            pass
        return None

    def open_chat_history(self) -> bool:
        """
        打开聊天历史面板，返回成功时同时设置 self._chat_msg_region。
        SZ 游戏中：按 Y 打开聊天 → 截图 → OCR 定位"历史"按钮 → 点击
        """
        self.focus_game()
        time.sleep(0.1)
        self._send_game_key(self.cfg.chat_key)
        time.sleep(0.5)

        import pyautogui
        game_rect = self._get_game_rect()
        targets = ["历史", "历臾", "历", "史"]
        for target in targets:
            result = self.ocr_find_text(target, region=game_rect)
            if result:
                pyautogui.click(result["center_x"], result["center_y"])
                time.sleep(0.4)
                gx, gy, gw, gh = game_rect
                # 使用校准的窗口相对坐标 (985, 114, 227, 346) → 转绝对坐标
                self._chat_msg_region = (
                    gx + 985,
                    gy + 114,
                    227,
                    346,
                )
                print(f"  消息区域(校准): {self._chat_msg_region}")
                return True
        return False

    def get_chat_msg_region(self):
        """返回上次 open_chat_history 计算的聊天消息区域"""
        return getattr(self, "_chat_msg_region", None)

    def send_chat(self, message: str):
        """
        发送聊天消息完整流程：
        聚焦游戏 → 打开聊天(Y) → 粘贴文本 → 按 Enter 发送（游戏自动关闭聊天框）
        """
        self.focus_game()
        time.sleep(0.1)
        self._send_game_key(self.cfg.chat_key)
        time.sleep(0.35)
        self._paste_text(message)
        time.sleep(0.15)
        self._send_game_key("enter")
        time.sleep(0.3)
        # 记录已发送消息，用于过滤自己的消息
        if not hasattr(self, "_sent_messages"):
            self._sent_messages = set()
        self._sent_messages.add(message.strip())


# ============================================================
# 三、工具定义 — MCP Tool Schema
# ============================================================

TOOLS = [
    {
        "name": "status",
        "description": "返回服务器状态、OCR 可用性、窗口句柄信息",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "focus_game",
        "description": "查找并激活游戏窗口，强制前台显示",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "press_key",
        "description": "按下指定按键，支持单键和组合键（如 'ctrl+c'）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "要按下的按键，如 'enter'/'esc'/'y'/'ctrl+v'",
                }
            },
            "required": ["key"],
        },
    },
    {
        "name": "open_chat",
        "description": "打开游戏内聊天输入框（按 Y 键）",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "send_chat",
        "description": "打开聊天 → 输入消息 → 按 Enter 发送",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要发送的聊天消息内容",
                }
            },
            "required": ["message"],
        },
    },
    {
        "name": "type_text",
        "description": "粘贴文本到当前焦点位置（剪贴板方式，支持中文）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要粘贴的文本内容",
                }
            },
            "required": ["text"],
        },
    },
    {
        "name": "read_screen",
        "description": "截图 → OCR 识别聊天区域文字并返回（使用 chat_region 过滤 UI）",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "take_screenshot",
        "description": "截图并返回 Base64 编码的 PNG 图片，供 AI 视觉模型使用",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "open_chat_history",
        "description": "打开聊天历史面板（OCR 定位'历史'按钮并点击）",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ============================================================
# 四、McpServer — JSON-RPC 2.0 协议层
# ============================================================

class McpServer:
    """实现 JSON-RPC 2.0 标准消息分发"""

    def __init__(self, controller: PcSzController):
        self.controller = controller
        self._initialized = False
        self._client_capabilities = {}
        self._tool_map = {
            "status": self._call_status,
            "focus_game": self._call_focus_game,
            "press_key": self._call_press_key,
            "open_chat": self._call_open_chat,
            "send_chat": self._call_send_chat,
            "type_text": self._call_type_text,
            "read_screen": self._call_read_screen,
            "take_screenshot": self._call_take_screenshot,
            "open_chat_history": self._call_open_chat_history,
        }

    # --- 工具路由 ---

    def handle_tool_call(self, name: str, arguments: dict) -> dict:
        """将 MCP tool call 路由到对应的控制器方法"""
        if name not in self._tool_map:
            return self._error(-32601, f"未知工具: {name}")
        try:
            return self._tool_map[name](arguments)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"执行错误: {e}\n{traceback.format_exc()}"}]}

    # --- 工具实现 ---

    def _call_status(self, args: dict) -> dict:
        info = self.controller.get_window_info()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "server": "sz-mcp-server",
                    "version": "0.3.0",
                    "ocr_engine": self.controller._ocr_name,
                    "window": info,
                    "chat_key": self.controller.cfg.chat_key,
                }, ensure_ascii=False, indent=2),
            }]
        }

    def _call_focus_game(self, args: dict) -> dict:
        ok = self.controller.focus_game()
        return {"content": [{"type": "text", "text": f"窗口聚焦{'成功' if ok else '失败，未找到游戏窗口'}"}]}

    def _call_press_key(self, args: dict) -> dict:
        self.controller.press_key(args["key"])
        return {"content": [{"type": "text", "text": f"按键 {args['key']} 已执行"}]}

    def _call_open_chat(self, args: dict) -> dict:
        self.controller.open_chat()
        return {"content": [{"type": "text", "text": "聊天输入框已打开"}]}

    def _call_send_chat(self, args: dict) -> dict:
        self.controller.send_chat(args["message"])
        return {"content": [{"type": "text", "text": f"消息已发送: {args['message']}"}]}

    def _call_type_text(self, args: dict) -> dict:
        self.controller.type_text(args["text"])
        return {"content": [{"type": "text", "text": f"文本已粘贴: {args['text']}"}]}

    def _call_read_screen(self, args: dict) -> dict:
        text = self.controller.read_screen()
        return {"content": [{"type": "text", "text": text if text else "（屏幕无识别文字）"}]}

    def _call_take_screenshot(self, args: dict) -> dict:
        self.controller.focus_game()
        b64 = self.controller.screenshot_base64()
        return {
            "content": [{
                "type": "image",
                "data": b64,
                "mimeType": "image/png",
            }]
        }

    def _call_open_chat_history(self, args: dict) -> dict:
        ok = self.controller.open_chat_history()
        return {"content": [{"type": "text", "text": f"聊天历史{'已打开' if ok else '打开失败，未找到历史按钮'}"}]}

    # --- JSON-RPC 2.0 消息分发 ---

    def handle(self, raw_message: str) -> Optional[str]:
        """解析并处理 JSON-RPC 2.0 消息，返回响应字符串或 None（通知）"""
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError:
            return json.dumps(self._make_error(None, -32700, "Parse error"))

        # 通知（无 id）不回复
        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            return self._handle_initialize(msg_id, params)
        elif method == "notifications/initialized":
            self._initialized = True
            return None
        elif method == "tools/list":
            return self._handle_tools_list(msg_id)
        elif method == "tools/call":
            return self._handle_tools_call(msg_id, params)
        elif method == "ping":
            return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            return json.dumps(self._make_error(msg_id, -32601, f"Method not found: {method}"))

    def _handle_initialize(self, msg_id, params: dict) -> str:
        self._client_capabilities = params.get("capabilities", {})
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "sz-mcp-server",
                "version": "0.3.0",
            },
        }
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _handle_tools_list(self, msg_id) -> str:
        result = {"tools": TOOLS}
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _handle_tools_call(self, msg_id, params: dict) -> str:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = self.handle_tool_call(tool_name, arguments)
        return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _make_error(self, msg_id, code: int, message: str) -> dict:
        err = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }
        return err

    def _error(self, code: int, message: str) -> dict:
        return {"content": [{"type": "text", "text": f"错误 [{code}]: {message}"}]}


# ============================================================
# 五、传输层
# ============================================================

def run_stdio(cfg: ServerConfig):
    """stdio 模式：stdin 读 JSON-RPC → 处理 → stdout 写 JSON-RPC"""
    controller = PcSzController(cfg)
    server = McpServer(controller)
    print("sz-mcp-server stdio mode started", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = server.handle(line)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


class _MCPHTTPHandler(BaseHTTPRequestHandler):
    """HTTP JSON-RPC 请求处理器"""
    server_instance: McpServer = None
    config: ServerConfig = None

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "server": "sz-mcp-server", "version": "0.3.0"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        # 鉴权
        if not self._check_auth():
            self._send_json(401, {"error": "unauthorized"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty body"})
            return

        body = self.rfile.read(content_length).decode("utf-8")
        response = self.server_instance.handle(body)

        if response is not None:
            self._send_json(200, json.loads(response))
        else:
            self._send_json(202, {})

    def _check_auth(self) -> bool:
        """非本地地址强制 Bearer Token 鉴权"""
        if not self.config.token:
            return True

        client_ip = self.client_address[0]
        if client_ip in ("127.0.0.1", "localhost", "::1"):
            return True

        auth_header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.config.token}"
        return auth_header == expected

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 静默日志


def run_http(cfg: ServerConfig):
    """HTTP 模式：启动 ThreadingHTTPServer，支持并发请求"""
    controller = PcSzController(cfg)
    server = McpServer(controller)

    _MCPHTTPHandler.server_instance = server
    _MCPHTTPHandler.config = cfg

    httpd = HTTPServer((cfg.host, cfg.port), _MCPHTTPHandler)
    print(f"sz-mcp-server HTTP mode: http://{cfg.host}:{cfg.port}", file=sys.stderr)
    print(f"Health check: http://{cfg.host}:{cfg.port}/health", file=sys.stderr)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


# ============================================================
# 六、入口
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="sz-mcp-server")
    parser.add_argument("--mode", choices=["stdio", "http"], default="http", help="传输模式")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=9800, help="HTTP 监听端口")
    parser.add_argument("--token", default="", help="Bearer Token")
    parser.add_argument("--monitor", type=int, default=1, help="显示器编号")
    parser.add_argument("--screenshot-scale", type=float, default=1.0, help="截图缩放比例")
    parser.add_argument("--input-backend", default="auto", choices=["auto", "pyautogui", "pydirectinput"])
    parser.add_argument("--chat-key", default="y", help="聊天按键")
    args = parser.parse_args()

    cfg = ServerConfig(
        host=args.host,
        port=args.port,
        token=args.token,
        monitor=args.monitor,
        screenshot_scale=args.screenshot_scale,
        input_backend=args.input_backend,
        chat_key=args.chat_key,
    )

    if args.mode == "stdio":
        run_stdio(cfg)
    else:
        run_http(cfg)


if __name__ == "__main__":
    main()
