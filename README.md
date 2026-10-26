# bot-hosting.net 免费套餐自动续期

使用 GitHub Actions 定时给 [bot-hosting.net](https://bot-hosting.net) 免费套餐续期。

## 站点机制(已实测确认)

| 环节 | 实现 |
|---|---|
| 登录 | **两步式**: 先填邮箱点「Continue with email」，再填密码 + Cloudflare Turnstile。（站点也支持 Discord/GitHub OAuth，脚本只走邮箱） |
| 续期入口 | `/a/billings` 页点「续期免费套餐」→ 弹窗 |
| 人机验证 | 弹窗内 Cloudflare Turnstile，通过后「续期 4 天」按钮才解锁，验证前是禁用状态 |
| 续期结果 | 到期日 **+4 天**（如 `2026/10/28` → `2026/10/30`，本次实测） |
| 免费额度 | 每 4 天到期，需手动续期 |
| **冷却期** | 每次续期后有约 **24 小时冷却**，按钮变为「23:46:57 后可续期」倒计时（禁用）。冷却中脚本自动跳过并 exit 0 |

## 仓库结构

```
app.py                        # 续期主脚本
.github/workflows/renew.yml   # 定时工作流(默认每 2 天)
time.txt                      # 运行心跳(自动更新, 防止仓库闲置暂停)
.env.example                  # 本地配置模板
```

## 配置(仓库 Settings → Secrets and variables → Actions)

| Secret | 必填 | 说明 |
|---|---|---|
| `BOT_HOSTING_EMAIL` | ✅ | 登录邮箱 |
| `BOT_HOSTING_PASSWORD` | ✅ | 登录密码 |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |
| `NODE_LINK` | ❌ | 代理节点(sing-box 步骤消费)，支持 `vless://` `vmess://` `trojan://` `tuic://` `anytls://` `hysteria2://` `socks5://`；不配则直连 |

## 使用

1. 建仓库并上传本目录内容
2. 配置上表 Secrets
3. Actions 手动触发 `workflow_dispatch` 测试
4. 默认每 2 天跑一次：冷却中自动跳过（不报错），解锁即续

## 本地运行

```bash
pip install patchright requests
cp .env.example .env   # 填邮箱密码
python app.py
```

## 说明

- 结果判定以**到期日变化**为准（不依赖 toast 文案），配合页面可见提示
- 冷却期识别: 解析按钮文案里的倒计时（`HH:MM:SS` / `X 小时` / `Xh`），命中则视为正常等待，exit 0
- 未到窗口、冷却期都不算失败；只有验证不过/到期日未变才 exit 1
- 失败时留下 `login_fail.png` / `renew_captcha_fail.png` / `renew_no_change.png` 等截图
- **不要**把 `.env` 或密码提交到公开仓库；凭证一律走 Secrets
