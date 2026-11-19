# Lingshu-Companion

会记得你的 AI 桌宠。

不是那种"每次对话都从零开始"的聊天机器人——她说过的每一句话都写进长期记忆，
下次见到你时，她还记得上次聊到哪儿。

![demo](docs/demo.png)

## 她能做什么

- **直播弹幕对话** — 接 B 站直播间，弹幕进来 → 表情 + 台词 + 语音
- **语音输入** — 说话转文字，**先给你改字**，改完再发（不用全双工，因为专名总被听错）
- **桌面形象** — Live2D 模型，头部/耳朵/呆毛会动，说话有口型
- **长期记忆** — 写入灵枢大脑，跨会话记住每个观众

## 为什么不用全双工

试过。问题是**表音语义**：

```
灵枢  líng shū
林叔  lín shū     ← whisper 听不出区别
您叔  nín shū
```

声音里根本没有这个信息，模型只能靠上下文猜。而全双工没有纠错窗口——错了直接说出去，
LLM 还会基于错字继续往下聊，越聊越偏。

所以这里是**语音输入法模式**：

```
你说话 → ASR 转文字 → 显示在输入框 → 你改 → 发送 → LLM
                ↑
        人眼是最后一道闸
```

配套还有别名纠错表（`config/aliases.json`），把「林叔」「凌叔」「您叔」自动换成「灵枢」。

## 前提：灵枢大脑

记忆功能依赖 [灵枢](https://github.com/FuRongJun-1999/dsh-memory) 认知图。

```bash
# 1. 克隆灵枢大脑
git clone https://github.com/FuRongJun-1999/dsh-memory vendor/lingshu

# 2. 或者用环境变量指向已有安装
set LINGSHU_ROOT=D:\path\to\dsh-memory-main
```

没有灵枢也能跑，只是她不记事。

## 快速开始

### 1. 装依赖

```bash
pip install sounddevice numpy faster-whisper
cd pet && npm install
```

### 2. 配置

```bash
copy config.local.example.json config.local.json
```

填三个东西：

| 字段 | 说明 |
|---|---|
| `bilibili.room_id` | 直播间房间号 |
| `bilibili.sessdata` | B 站登录态（F12 → Application → Cookies） |
| `llm.api_key` | 或写进 `config/secrets.json` |

**`config.local.json` 和 `config/secrets.json` 已在 `.gitignore` 里，不会被提交。**

### 3. 启动

```bash
python companion/start_all.py
```

或者双击 `启动.bat`。

## 端口

| 端口 | 用途 |
|---|---|
| 8789 | 桥（Python ↔ 桌宠） |
| 8791 | 桌宠控制（齿轮/窗口） |
| 8792 | 对话记录 + 输入框 |
| 8793 | 服务配置 |
| 8795 | 模型控制（23 表情 / 30 姿态） |
| 8770 | 模型资源 |

## 结构

```
companion/          Python 大脑
├── main.py         主循环
├── perceive.py     感知（弹幕/控制台）
├── speak.py        说话层
├── persona.py      人设 + 记忆注入
├── asr.py          语音输入
├── body.py         TTS + 表情下发
├── lingshu.py      灵枢记忆读写
└── providers.py    服务商注册表

pet/                Electron 身体
├── main.js         双窗口管理
└── renderer/
    └── model.js    Live2D 渲染 + 姿态驱动

config/
├── services.json   服务商配置（可提交）
├── hotwords.txt    ASR 热词
└── aliases.json    同音纠错表
```

## 踩过的坑

做这个项目最难的**不是 AI，是让它在桌面上真正活起来**。文档在 [docs/pitfalls.md](docs/pitfalls.md)：

- Live2D 的 509 个参数里，**292 个是物理输出**，直接写会被覆盖
- Cubism 的 `lipSync` 会独占嘴部参数
- `coreModel.update()` 会把没被 motion 驱动的参数**重置回默认**
- Electron 的 `visibilitychange` **检测不到"被遮挡"**
- faster-whisper 的 `hotwords` **对中文专名基本无效**

## 许可

MIT
