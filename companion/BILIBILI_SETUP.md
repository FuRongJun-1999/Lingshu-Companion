# B站弹幕接入 · 凭据配置

## 为什么需要 SESSDATA

B站自 2024 年起对 `getDanmuInfo` 接口做了风控（错误码 `-352`）。
匿名请求（uid=0）即使带 buvid3 也会被拒，**必须携带登录态 Cookie**。

## 怎么拿

1. 浏览器登录 <https://www.bilibili.com>
2. 打开任意直播页面，例如 `https://live.bilibili.com/<你的房间号>`
3. 按 **F12** → **Application（应用）** → **Cookies** → `https://www.bilibili.com`
4. 找到 **SESSDATA**，复制它的值（一长串）
5. 填进 `config.local.json`

## config.local.json 长这样

```json
{
  "bilibili": {
    "room_id": 12345678,
    "sessdata": "粘贴到这里"
  }
}
```

## 安全

- 该文件已在 `.gitignore` 中排除
- SESSDATA 等同登录凭据，**不要发给任何人、不要提交、不要贴到聊天里**
- 失效后重新复制即可（B站会定期轮换）

## 房间号

不是你的短号（如 `6`），而是直播间地址里的数字，例如
`https://live.bilibili.com/12345678` 的 `12345678`。
程序会自动用 `room_init` 换成内部真实房间号。
