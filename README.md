# SZ-Bot — 超自然行动组 AI 自动回复机器人

> 《超自然行动组》(Preternatural) PC 版游戏内聊天 AI 自动回复机器人。  
> OCR 读屏 + AI 分析 + 自动回复，全程驱动级按键注入，无需人工干预。

## 文件清单

| 文件 | 用途 |
|------|------|
| `sz-mcp-server.py` | 核心控制器：截图 / OCR / 按键 / 聊天 封装 |
| `ai_bot.py` | 主循环：定时检测 → OCR → AI 分析 → 自动回复 |
| `bot_persona.json` | 人设配置：昵称、性格、回复规则 |
| `api_keys.json` | API 密钥：DeepSeek / Claude |
| `启动.bat` | 一键启动脚本，交互菜单选择模型 |
| `requirements.txt` | Python 依赖 |
| `README.md` | 本文档 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置密钥

编辑 `api_keys.json`，至少填写一个模型的 API Key：

```json
{
  "deepseek": "sk-你的key",
  "claude": "sk-ant-你的key"
}
```

### 3. 配置人设

编辑 `bot_persona.json`，填写你的游戏内昵称：

```json
{
  "player_name": "你的游戏昵称",
  "persona": { ... },
  "rules": { ... }
}
```

### 4. 启动游戏

确保《超自然行动组》游戏窗口已打开，窗口标题包含 `Preternatural`。

### 5. 运行

```bash
# 使用 DeepSeek（免费）
python ai_bot.py --model deepseek

# 使用 Claude
python ai_bot.py --model claude

# 自定义检测间隔（默认 8 秒）
python ai_bot.py --model deepseek --interval 10
```

按 `Ctrl+C` 停止。

### 6. 一键启动（推荐）

双击 `启动.bat`，按交互菜单选择模型和参数，无需手动输入命令。选择后自动启动，按 `Ctrl+C` 停止。

## 工作机制

```
Step 0: Esc×2 归位
Step 1: Y 键打开聊天 → OCR 找"历史"按钮 → 点击 → 精确定位消息区域
Step 2: 截取消息区域 → 2x 放大 + 对比度增强 → EasyOCR 识别
Step 3: Y 键关闭历史面板
Step 4: 过滤自己消息 + 去重 → 发给 AI 分析 → 获取回复
Step 5: Y 键打开聊天 → 粘贴回复 → Enter 发送（自动关闭）
```

### OCR 区域校准

消息区域的窗口相对坐标硬编码为 `(985, 114, 227, 346)`，每轮自动加上游戏窗口位置转为绝对坐标。如果游戏分辨率变化，需重新校准。

### 关键设计

- **驱动级按键**：`keybd_event` 注入，兼容 DirectInput 游戏
- **EasyOCR**：唯一能正确识别游戏位图字体的本地 OCR 引擎
- **2x 预处理**：截图放大 + 对比度增强，解决小字识别乱码
- **消息合并**：自动将 OCR 换行拆开的消息拼接回一行
- **强制回复**：非自己昵称的消息全部回复，AI 返回 `reply=false` 时兜底回复
- **去重**：保留最近 5 轮消息集合，避免重复回复
- **频率控制**：同一玩家连续发言最多回复 1 次

## 常见问题

**Q: OCR 识别乱码？**  
A: EasyOCR CPU 模式对游戏字体有较好兼容性，已内置 2x 放大 + 对比度增强预处理。仍不行可调高 `ImageEnhance.Contrast` 的增强系数。

**Q: 聊天框关不掉/卡住？**  
A: Enter 发送后游戏自动关闭聊天框，无需额外按键。如果卡住，先手动 Esc×2 再重启 Bot。

**Q: 想换窗口位置？**  
A: 直接拖动游戏窗口即可。消息区域通过 `game_rect + 校准偏移` 动态计算。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
