# 踩过的坑

这份文档记录的不是"最佳实践"，是**实测推翻的直觉**。

## Live2D 参数三层覆盖

模型有 509 个参数，看起来都能写。实际上：

### 第一层：physics

`physics3.json` 里 131 条设置，其中 **292 个参数是输出**：

```json
设置 0:  输入 AngleX     → 输出 ParamAngleX  (权重 100, scale 28)
设置 2:  输入 AngleZ     → 输出 ParamAngleZ
设置 3:  输入 PositionX  → 输出 ParamBodyAngleX
```

写 `ParamAngleZ = 35`，物理每帧算一遍，冲回 0。**头部转动、耳朵、手、身体全部锁死。**

### 第二层：breath

`internalModel.breath` 会让角色自动呼吸——它也在写 `ParamAngleX/Y/Z`。

### 第三层：coreModel.update()

最隐蔽的一层。把 hook 挂在 `updateNaturalMovements` 里写参数，**手动调用有效，自动运行无效**：

```python
# 手动：
setParameterValueById('ParamAngleX', 20)  # 读回 20 ✓
# 下一帧：
# coreModel.update() 把没有 motion 驱动的参数重置回默认
# 读回 0
```

**必须 hook 到 `internalModel.update()` 之后。**

### 代价

关掉 physics 换来完全控制，但头发、裙子不再自然飘动。需要用代码模拟惯性：

```javascript
headHist.push({x: hx, y: hy})                    // 存头部历史
const ref = headHist[length - HEAD_LAG - i]      // 取 N 帧前
core.setParameterValueById('FrontHairX' + i, ref.x * k)
```

## 口型

`lipSync` 独占 `ParamMouthOpenY`——写 0.9，读回 0。

**必须 `internalModel.lipSync = null`**，然后按音频 RMS 包络自己推：

```python
rms = sqrt(mean(x² for x in frame))
v = (rms / 32768) ** 0.6 * 135    # 开方压缩，让小音量也张口
```

而且**张嘴和闭嘴要用不同速率**：

```javascript
const ease = target > current ? 0.55 : 0.12   // 张快闭慢
```

两边同速率的话：快了闭合像抽筋，慢了跟不上音节。

## Electron 帧率

网上都说监听 `visibilitychange`。**实测不成立**：

| 状态 | visibilityState | hidden | focused |
|---|---|---|---|
| 她没焦点 | visible | false | false |
| **被浏览器盖住** | visible | false | false |
| 点回前台 | visible | false | true |

**"被遮挡"根本检测不到**，只有最小化才会 hidden。

能用的只有三样：`document.hasFocus()` / `document.hidden` / 空闲时长。

```javascript
const ease = // ...
app.ticker.maxFPS = 60   // 有焦点
app.ticker.maxFPS = 20   // 失焦
app.ticker.maxFPS = 8    // 空闲 30 秒
```

## ASR 同音词

faster-whisper 有 `hotwords` 参数，看文档像是正解。**实测对中文专名基本无效**：

| 配置 | 识别结果 |
|---|---|
| 无 hotwords | **林**叔 |
| hotwords="灵枢" | **凌**叔 |
| hotwords + initial_prompt | **凌**叔 |

三种全错。**最终只能靠别名表后处理**：

```json
{
  "灵枢": ["林叔", "凌叔", "您叔", "灵书"]
}
```

## 其他

**CUDA float16 崩溃** — faster-whisper 在 RTX 4090 上 `encode` 阶段崩，
换 CPU int8 稳定（5.76 秒音频 1.4 秒识别）。

**麦克风增益** — USB 麦说话峰值只有 0.0099（正常 0.05~0.3），
必须录音后归一化，否则 whisper 识别不出。

**Daemon 线程** — 记忆写入放后台线程，进程退出时被杀。
一次性模式必须 `flush()` 等队列清空，否则显示"写入成功"但库里没有。

**MdCG 非线程安全** — 主循环和网页会并发写，必须单线程队列串行。
