# 免费套餐 / 免费服务器自动续期

用 GitHub Actions 定时给免费资源续期，目前支持两个站点：

| 站点 | 脚本 | 续期方式 |
|---|---|---|
| [bot-hosting.net](https://bot-hosting.net) | `app.py` | 账单页点「续期免费套餐」，到期日 **+4 天** |
| [gaming4free.net](https://gaming4free.net) | `gaming4free_renew.py` | 公开投票续命，每票 **+30 分钟**，上限 48 小时 |

---

# 一、bot-hosting.net 免费套餐自动续期

## 站点机制(已实测确认)

| 环节 | 实现 |
|---|---|
| 登录 | **两步式**: 先填邮箱点「Continue with email」，再填密码 + Cloudflare Turnstile。（站点也支持 Discord/GitHub OAuth，脚本只走邮箱） |
| 续期入口 | `/a/billings` 页点「续期免费套餐」→ 弹窗 |
| 人机验证 | 弹窗内 Cloudflare Turnstile，通过后「续期 4 天」按钮才解锁，验证前是禁用状态 |
| 续期结果 | 到期日 **+4 天**（如 `2026/10/28` → `2026/10/30`，本次实测） |
| 免费额度 | 每 4 天到期，需手动续期 |
| **冷却期** | 每次续期后有约 **24 小时冷却**，按钮变为「23:46:57 后可续期」倒计时（禁用）。冷却中脚本自动跳过并 exit 0 |

## 配置(仓库 Settings → Secrets and variables → Actions)

| Secret | 必填 | 说明 |
|---|---|---|
| `BOT_HOSTING_EMAIL` | ✅ | 登录邮箱 |
| `BOT_HOSTING_PASSWORD` | ✅ | 登录密码 |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |
| `NODE_LINK` | ❌ | 代理节点(sing-box 步骤消费)，支持 `vless://` `vmess://` `trojan://` `tuic://` `anytls://` `hysteria2://` `socks5://`；不配则直连 |

## 说明

- 结果判定以**到期日变化**为准（不依赖 toast 文案），配合页面可见提示
- 冷却期识别: 解析按钮文案里的倒计时（`HH:MM:SS` / `X 小时` / `Xh`），命中则视为正常等待，exit 0
- 未到窗口、冷却期都不算失败；只有验证不过/到期日未变才 exit 1
- 失败时留下 `login_fail.png` / `renew_captcha_fail.png` / `renew_no_change.png` 等截图
- **不要**把 `.env` 或密码提交到公开仓库；凭证一律走 Secrets

---

# 二、gaming4free.net 免费游戏服务器自动续期

## 站点机制(已实测确认)

这个站**没有「续期按钮 / 续期 API」**。免费服务器的命脉是**公开投票**：任何访客在服务器公开页投一票，服务器就多活一段时间。

| 环节 | 实现 |
|---|---|
| 查询状态 | `GET /api/servers`、`GET /api/servers/{slug}` —— **公开免鉴权**，返回 `hours_remaining` / `cooldown_remaining` / `vote_count_today` |
| 投票接口 | `POST /api/servers/{slug}/vote`，body `{"cf-turnstile-response": <token>, "voter_name": <str\|null>, "ad_watched": "0"\|"1"}` → 成功 `{"success":true,"message":"30 minutes added! ..."}` |
| 付费通道 | `POST /api/servers/{slug}/donate`，body 加 `amount`(cents) → 返回 Stripe `redirect_url`。档位随服务器不同，例：`fun` 是 $2.25/+15天、$3.75/+25天、$7.50/+50天 |
| 人机验证 | Cloudflare Turnstile，sitekey `0x4AAAAAADWeWAriMB7Vsqya`，token 一次一用，缺失返回 422 |
| 票数收益 | 每票 **+30 分钟**（`minutes_per_vote`）；`ad_watched=1`（看完激励广告）前端显示 +90m |
| **冷却期** | 每个 IP 对同一台服务器有 **约 5 分钟冷却**，`cooldown_remaining` 是真实倒计时 |
| 上限 / 死亡 | 累积上限 **48 小时**；超过 48 小时无投票 → `is_suspended`（挂起），再久才 `deletes_at`（删除） |

## ⚠️ 两个必须知道的坑（都实测踩过）

1. `control.gaming4free.net` 有 Cloudflare WAF，**curl / python-requests 一律 403** —— 必须用真实浏览器网络栈发请求，所以脚本全程走浏览器。
2. **用 Playwright/patchright 直接 `launch()` 出来的浏览器过不了 Turnstile**：挑战链路（fo/pat/ci）全跑通，但挑战帧无限刷新、永不下发 token。
   脚本的解法是：启动一个**真实浏览器进程** + `--remote-debugging-port`，再用 `connect_over_cdp` 接管 —— 这样 `navigator.webdriver` 为 `False`，一次就过。
   （token 落在隐藏域 `input[name=cf-turnstile-response]`，读不到 `window.turnstile`，不影响结果，别在这浪费时间。）

## 配置(仓库 Settings → Secrets and variables → Actions)

| Secret | 必填 | 说明 |
|---|---|---|
| `G4F_SLUG` | ✅ | 服务器 slug，逗号分隔可多个。例：`fun` |
| `G4F_THRESHOLD` | ❌ | 剩余小时低于该值才投票，默认 `40`（配合 30 分钟一次的工作流稳在 48h 上限） |
| `G4F_MAX_VOTES` | ❌ | 单次运行最多投几票，默认 `3` |
| `G4F_VOTER_NAME` | ❌ | 投票者名字，默认 `renew-bot` |
| `G4F_DONATE_CENTS` | ❌ | 走付费通道的金额(cents)，配了就投票改捐赠 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | ❌ | Telegram 通知 |
| `NODE_LINK` | ❌ | 代理节点(sing-box 步骤消费)；GitHub Runner 的 IP 常被 Cloudflare 拦，配代理更稳 |

## 节奏怎么定

服务器 48 小时不投票就挂起，而每票只加 30 分钟：

- **想稳在 48 小时上限**：每 30 分钟跑一次、`G4F_THRESHOLD=40`（每轮大约投 1 票）
- **只想不死**：每 30 分钟跑一次、`G4F_THRESHOLD=12` 即可（剩余时长在 12h 附近震荡）
- 手动运行会等冷却结束再继续投（`G4F_WAIT_COOLDOWN`，默认等 8 分钟以内的冷却）

## 本地运行

```bash
pip install patchright requests
cp .env.example .env      # 填 G4F_SLUG=fun
python gaming4free_renew.py --list              # 看全部服务器状态
python gaming4free_renew.py --slug fun --threshold 40
```

---

## 仓库结构

```
app.py                            # bot-hosting.net 续期主脚本
gaming4free_renew.py             # gaming4free.net 投票续期主脚本
.github/workflows/renew.yml              # bot-hosting 定时工作流
.github/workflows/renew-gaming4free.yml  # gaming4free 定时工作流(每 30 分钟)
time.txt                          # 运行心跳(自动更新, 防止仓库闲置暂停)
.env.example                      # 本地配置模板
```

## 本地运行(bot-hosting)

```bash
pip install patchright requests
cp .env.example .env   # 填邮箱密码
python app.py
```
