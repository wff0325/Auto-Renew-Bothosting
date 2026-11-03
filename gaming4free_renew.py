#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gaming4free.net 免费游戏服务器自动续期（投票续命）
参考架构: 与 bot-hosting 版 app.py 保持一致（patchright + 环境变量配置 + TG 通知）

站点机制(已实测确认):
  - 这个站**没有「续期按钮 / 续期 API」**, 免费服务器的命脉是**公开投票**:
    任何访客在服务器公开页投一票, 服务器就多活一段时间。
  - 公开接口(免鉴权, GET):
      https://control.gaming4free.net/api/servers            -> 全部服务器 + hours_remaining
      https://control.gaming4free.net/api/servers/{slug}      -> 单服详情 + recent_votes + cooldown_remaining
  - 唯一真正写入时长的端点(POST):
      https://control.gaming4free.net/api/servers/{slug}/vote
      body: {"cf-turnstile-response": <turnstile token>,
             "voter_name": <str|null>, "ad_watched": "0"|"1"}
      成功 -> {"success": true, "message": "30 minutes added! ..."}
      每票 **+30 分钟**(minutes_per_vote); ad_watched=1(看完激励广告)前端显示 +90m
      缺 token -> 422 "Captcha verification failed. Please try again."
  - 付费通道(POST, Stripe):
      https://control.gaming4free.net/api/servers/{slug}/donate
      body: {"cf-turnstile-response": <token>, "amount": <cents>} -> 返回 redirect_url
  - 规则:
      * 每票 +30 分钟, 每 IP 对同一台服务器有 **约 5 分钟冷却**(cooldown_remaining 是真实倒计时)
      * 累积上限 **48 小时**(hours_remaining 封顶 48)
      * 超过 48 小时无投票 -> is_suspended(挂起), 再久才 deletes_at(删除)

⚠️ 两个必须知道的坑(都实测踩过):
  1. control 域名有 Cloudflare WAF, curl / python-requests 一律 403,
     必须用真实浏览器网络栈发请求(所以本脚本全程走浏览器)。
  2. **用 Playwright/patchright 直接 launch 出来的浏览器过不了 Turnstile**:
     挑战链路(fo/pat/ci)全跑通, 但挑战帧无限刷新、永不下发 token。
     解法: 启动一个**真实浏览器进程** + --remote-debugging-port, 再用 connect_over_cdp 接管,
     这样 navigator.webdriver 为 False, 一次就过。
     token 落在隐藏域 input[name=cf-turnstile-response], 读不到 window.turnstile(不影响, 别在这浪费时间)。

用法:
    本地: .env 写 G4F_SLUG=fun, 然后 python gaming4free_renew.py
    Actions: Secrets 配 G4F_SLUG (+ 可选 NODE_LINK / TG_*)
    命令行: python gaming4free_renew.py --slug fun --threshold 40 --list

配置项(环境变量):
    G4F_SLUG             服务器 slug, 逗号分隔(必填), 例: fun
    G4F_THRESHOLD        剩余小时低于该值才投票(默认 40, 配合 30 分钟一次的工作流稳在 48h 上限)
    G4F_MAX_VOTES        单次运行最多投几票(默认 3)
    G4F_VOTER_NAME       投票者名字(可选, 默认 renew-bot)
    G4F_DONATE_CENTS     走付费通道的金额(cents), 不配则不投票
    G4F_WAIT_COOLDOWN    冷却剩余不超过该分钟数时, 等冷却结束继续投(默认 8)
    G4F_KEEP_ALIVE       置 1 则一直续命(轮询投票直到手动停止)
    G4F_BROWSER_PATH     指定浏览器可执行文件(可选, 默认自动探测)
    G4F_DEBUG_PORT       CDP 调试端口(默认 9222)
    TG_BOT_TOKEN         Telegram Bot Token (可选)
    TG_CHAT_ID           Telegram Chat ID   (可选)
    IS_PROXY             是否使用代理 (默认 false; workflow 代理步骤会写入)
    PROXY_SERVER         代理地址 (默认 http://127.0.0.1:1081)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

# ==================== 本地 .env 加载(不提交仓库) ====================
def _load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    except Exception:
        pass

_load_local_env()

# ==================== CONFIG ====================
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""
TG_CHAT_ID = os.environ.get('TG_CHAT_ID') or ""
IS_PROXY = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER = (os.environ.get('PROXY_SERVER') or "http://127.0.0.1:1081").strip()
BROWSER_PATH = os.environ.get('G4F_BROWSER_PATH') or ""
DEBUG_PORT = int(os.environ.get('G4F_DEBUG_PORT') or "9222")
VOTER_NAME = os.environ.get('G4F_VOTER_NAME') or "renew-bot"
# Turnstile 出 token 的耗时波动很大(实测 18s ~ 110s)，超时给足，别卡在 90s
TS_TIMEOUT = int(os.environ.get('G4F_TS_TIMEOUT') or "180")
# 复用同一个 profile 目录: 全新 profile 会让 Turnstile 变慢很多
PROFILE_DIR = os.environ.get('G4F_PROFILE_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".g4f_profile")

try:
    from patchright.sync_api import sync_playwright
    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USING_PATCHRIGHT = False

import requests

REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

SITE = "https://gaming4free.net"
API = "https://control.gaming4free.net"
SITEKEY = "0x4AAAAAADWeWAriMB7Vsqya"


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


# ==================== 浏览器(真实进程 + CDP) ====================
def _detect_browser_path():
    """探测可用的浏览器可执行文件。"""
    if BROWSER_PATH and os.path.exists(BROWSER_PATH):
        return BROWSER_PATH
    cands = []
    if sys.platform == 'win32':
        cands = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
                     "microsoft-edge", "microsoft-edge-stable"):
            p = shutil.which(name)
            if p:
                cands.append(p)
    for c in cands:
        if os.path.exists(c):
            return c
    # 退回 patchright/playwright 自带的 chromium
    for root in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "",
                 os.path.expanduser("~/.cache/ms-playwright"),
                 os.path.expanduser("~/AppData/Local/ms-playwright")):
        if not root or not os.path.isdir(root):
            continue
        try:
            for d in sorted(os.listdir(root)):
                for rel in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe",
                            "chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
                    p = os.path.join(root, d, rel)
                    if os.path.exists(p):
                        return p
        except Exception:
            continue
    return ""


def launch_browser_cdp():
    """启动真实浏览器进程并返回 (proc, cdp_url)。CDP 接管是关键: 规避自动化指纹。

    踩过的坑:
      - 每次开全新临时 profile 时 Turnstile 会更慢(18s -> 100s+), 所以默认复用
        项目目录下 .g4f_profile 目录, 让 clearance cookie 留下来。
      - 必须关掉 Edge 的 Tracking Prevention, 否则 challenges.cloudflare.com
        拿不到 storage, 挑战帧反复重载、永不发 token。
      - --remote-debugging-port 必须是非 0 端口, 否则 navigator.webdriver 变 True。
    """
    exe = _detect_browser_path()
    if not exe:
        raise RuntimeError("找不到可用的浏览器可执行文件")
    profile = PROFILE_DIR
    try:
        os.makedirs(profile, exist_ok=True)
    except Exception:
        profile = tempfile.mkdtemp(prefix="g4f_profile_")
    args = [
        exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # 关掉 Edge 的跟踪保护: 否则 challenges.cloudflare.com 被拦 storage, 永远拿不到 token
        "--disable-features=TrackingPrevention,msEdgeTrackingPrevention,msEdgeThirdPartyStoragePartitioning",
        "--disable-sync",
        "--disable-background-networking",
        "--window-size=1440,900",
        "about:blank",
    ]
    if IS_PROXY:
        args.insert(-1, f"--proxy-server={PROXY_SERVER}")
    if sys.platform != 'win32' and hasattr(os, 'geteuid') and os.geteuid() == 0:
        args.insert(-1, "--no-sandbox")  # 容器内 root 才需要
    log(f"启动真实浏览器进程: {os.path.basename(exe)} (CDP :{DEBUG_PORT})"
        f"{' | 代理 ' + PROXY_SERVER if IS_PROXY else ''}")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(45):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=2).read()
            return proc, f"http://127.0.0.1:{DEBUG_PORT}"
        except Exception:
            continue
    try:
        proc.terminate()
    except Exception:
        pass
    raise RuntimeError("浏览器调试端口未就绪")


# ==================== 接口封装(全部在页面上下文里发, 绕过 WAF) ====================
def api_get(page, path):
    return page.evaluate(
        """async ([url]) => {
            try {
                const r = await fetch(url, {headers:{'Accept':'application/json'}, credentials:'omit'});
                const t = await r.text();
                let j = null; try { j = JSON.parse(t); } catch(e) {}
                return {status: r.status, json: j, text: t.slice(0, 300)};
            } catch(e) { return {error: String(e)}; }
        }""",
        [API + path],
    )


def server_info(page, slug):
    r = api_get(page, "/api/servers/" + slug)
    if r.get("status") != 200 or not r.get("json"):
        return None
    d = r["json"]
    return {"server": d.get("server", d), "cooldown": d.get("cooldown_remaining"),
            "recent_votes": d.get("recent_votes", [])}


def list_servers(page):
    r = api_get(page, "/api/servers")
    if r.get("status") != 200 or not r.get("json"):
        return []
    return (r["json"].get("data") or r["json"].get("servers") or [])


# ==================== Turnstile ====================
def wait_turnstile_token(page, timeout=90):
    """等 Turnstile 下发 token; 出现交互式勾选框就点一下。

    token 落在隐藏域 input[name=cf-turnstile-response]（读不到 window.turnstile，别在这浪费时间）。
    站点在 iframe 里渲染 Turnstile（challenges.cloudflare.com），Playwright 的 page.frames 能拿到，
    但 iframe 里的 DOM 要用 frame.evaluate 去找，光靠主文档 querySelector 找不到。
    """
    t0 = time.time()
    last_log = 0
    while time.time() - t0 < timeout:
        # 主文档里找（widget 是主文档渲染的，隐藏域也应在主文档）
        token = page.evaluate(
            """() => {
                const els = document.querySelectorAll('input[name="cf-turnstile-response"]');
                for (const el of els) { if (el.value) return el.value; }
                return '';
            }""")
        if token:
            return token
        # 有些情况下隐藏域在 iframe 里，也扫一遍
        for fr in page.frames:
            if "challenges.cloudflare.com" not in (fr.url or ""):
                continue
            try:
                token = fr.evaluate(
                    """() => {
                        const els = document.querySelectorAll('input[name="cf-turnstile-response"]');
                        for (const el of els) { if (el.value) return el.value; }
                        return '';
                    }""")
            except Exception:
                token = ""
            if token:
                return token
            for sel in ('input[type="checkbox"]', 'label', '.cb-lb'):
                try:
                    loc = fr.locator(sel)
                    for j in range(min(loc.count(), 3)):
                        el = loc.nth(j)
                        if el.is_visible():
                            el.click(timeout=3000)
                except Exception:
                    pass
        if time.time() - last_log > 20:
            last_log = time.time()
            log(f"  等待 Turnstile token… ({int(time.time() - t0)}s)")
        time.sleep(2)
    return ""


def _fetch_vote_response(page, slug, token, voter_name, ad_watched="0"):
    """自己发 POST /vote, 拿到服务端真实 JSON（不走站点按钮，才能看到 json.error）。"""
    return page.evaluate(
        """async ([url, token, name, ad]) => {
            try {
                const r = await fetch(url, {method:'POST',
                    headers:{'Content-Type':'application/json','Accept':'application/json'},
                    credentials:'omit',
                    body: JSON.stringify({
                        'cf-turnstile-response': token,
                        'voter_name': name || null,
                        'ad_watched': ad
                    })});
                const t = await r.text();
                let j = null; try { j = JSON.parse(t); } catch(e) {}
                return {status: r.status, json: j, text: t.slice(0, 500)};
            } catch(e) { return {error: String(e)}; }
        }""",
        [f"{API}/api/servers/{slug}/vote", token, voter_name, ad_watched],
    )


# ==================== 投票 / 捐赠 ====================
def do_vote(page, slug, voter_name=VOTER_NAME, donate_cents=None, ad_watched="0"):
    """走站点原生投票流程。返回 dict。"""
    before = server_info(page, slug)
    if before is None:
        return {"ok": False, "error": "无法读取服务器信息"}
    h_before = (before.get("server") or {}).get("hours_remaining")

    page.goto(f"{SITE}/servers/{slug}", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_selector("#sd-vote-btn:not([disabled])", timeout=60000)

    if donate_cents:
        return _do_donate(page, slug, donate_cents, h_before)

    # 点投票按钮 -> 弹出 modal -> 渲染 Turnstile
    page.click("#sd-vote-btn")
    page.wait_for_timeout(2500)

    # 激励广告遮罩: 等它自己结束(最多 45s); 广告没加载出来时会直接跳过
    for _ in range(9):
        disp = page.evaluate("document.getElementById('ad-loading-overlay')?.style.display || ''")
        if disp != "flex":
            break
        page.wait_for_timeout(5000)

    token = wait_turnstile_token(page, timeout=TS_TIMEOUT)
    if not token:
        try:
            page.screenshot(path="g4f_captcha_fail.png", timeout=15000, animations="disabled")
        except Exception:
            pass
        return {"ok": False, "error": f"Turnstile 验证未通过（{TS_TIMEOUT}s 内没拿到 token）", "before": h_before}

    # 拿到 token 后自己发 POST, 这样能拿到服务端真实错误(json.error), 而不是只看到 DOM 提示
    resp = _fetch_vote_response(page, slug, token, voter_name, ad_watched)
    data = resp.get("json") or {}
    ok = bool(resp.get("status") == 200 and data.get("success"))

    after = server_info(page, slug) or {}
    h_after = (after.get("server") or {}).get("hours_remaining")
    reason = ""
    if not ok:
        reason = (data.get("error") or data.get("message")
                  or resp.get("text") or f"HTTP {resp.get('status')}")
    return {
        "ok": ok,
        "slug": slug,
        "message": data.get("message") or "",
        "error": "" if ok else reason,
        "before": h_before,
        "after": h_after,
        "delta": round(h_after - h_before, 2) if (h_before is not None and h_after is not None) else None,
        "cooldown": after.get("cooldown"),
        "http_status": resp.get("status"),
    }


def _do_donate(page, slug, cents, h_before):
    """付费通道: 走一次 Turnstile 拿 token, POST /donate 拿 Stripe 链接。"""
    token = page.evaluate(
        """async ([sitekey]) => {
            const host = document.createElement('div');
            host.style.cssText = 'position:fixed;left:10px;bottom:10px;z-index:99999;background:#fff;padding:6px';
            document.body.appendChild(host);
            const t = await new Promise((resolve) => {
                const render = () => {
                    if (!window.turnstile) { setTimeout(render, 300); return; }
                    try {
                        window.turnstile.render(host, {sitekey: sitekey, theme: 'light',
                            callback: (tk) => resolve(tk), 'error-callback': () => resolve(null)});
                    } catch (e) { resolve(null); }
                };
                render();
                setTimeout(() => resolve(null), 60000);
            });
            host.remove();
            return t || '';
        }""",
        [SITEKEY],
    )
    if not token:
        return {"ok": False, "error": "Turnstile 验证未通过", "before": h_before}
    res = page.evaluate(
        """async ([url, token, amount]) => {
            const r = await fetch(url, {method:'POST',
                headers:{'Content-Type':'application/json','Accept':'application/json'},
                credentials:'omit',
                body: JSON.stringify({'cf-turnstile-response': token, 'amount': amount})});
            const t = await r.text();
            let j = null; try { j = JSON.parse(t); } catch(e) {}
            return {status: r.status, json: j, text: t.slice(0, 300)};
        }""",
        [f"{API}/api/servers/{slug}/donate", token, cents],
    )
    data = res.get("json") or {}
    return {"ok": bool(data.get("redirect_url")), "slug": slug, "donate": data or res.get("text"),
            "before": h_before}


# ==================== Telegram 通知 ====================
def get_current_ip():
    try:
        r = requests.get("https://api.ipify.org", timeout=10, proxies=REQUESTS_PROXIES)
        return r.text.strip()
    except Exception:
        return "未知"


def build_notification_text(slug, status, before, after, current_ip="未知"):
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    if "成功" in status:
        icon, title = "✅", "投票续期成功"
    elif "冷却" in status:
        icon, title = "⏳", "投票冷却中"
    elif "无需" in status or "阈值" in status:
        icon, title = "🕐", "无需续期"
    else:
        icon, title = "❌", "续期失败"

    lines = [
        f"<b>{icon} Gaming4Free 续期通知</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>状态</b>　　{title}",
        f"<b>服务器</b>　<code>{slug}</code>",
        "",
        "<b>⏳ 剩余时长</b>",
        f"　投票前　{before} 小时",
        f"　投票后　<b>{after}</b> 小时",
        "",
        f"<b>📌 结果</b>　{status}",
        "",
        "<b>🌐 运行环境</b>",
        f"　出口 IP　<code>{current_ip}</code>",
        f"　时间　　{now} (UTC+8)",
    ]
    return "\n".join(lines)


def send_telegram_notification(slug, status, before, after, current_ip="未知"):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("Telegram 未配置，跳过通知")
        return
    text = build_notification_text(slug, status, before, after, current_ip)
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT_ID, "text": text,
                                "parse_mode": "HTML", "disable_web_page_preview": True},
                          timeout=10, proxies=REQUESTS_PROXIES)
        if r.status_code == 200:
            log("Telegram 通知发送成功")
        else:
            log(f"Telegram HTML 通知失败({r.status_code})，回退纯文本")
            plain = re.sub(r'<[^>]+>', '', text)
            requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT_ID, "text": plain},
                          timeout=10, proxies=REQUESTS_PROXIES)
    except Exception as e:
        log(f"Telegram 通知异常: {e}")


# ==================== 主流程 ====================
def _cli_args():
    import argparse
    ap = argparse.ArgumentParser(description="Gaming4Free 自动投票续期")
    ap.add_argument("--slug", action="append", default=[], help="服务器 slug, 可重复")
    ap.add_argument("--list", action="store_true", help="只列出服务器状态")
    ap.add_argument("--threshold", type=float, default=None, help="剩余小时低于该值才投票")
    ap.add_argument("--max-votes", type=int, default=None, help="单次最多投几票")
    ap.add_argument("--voter-name", default=None)
    ap.add_argument("--donate", type=int, default=None, help="付费通道金额(cents)")
    ap.add_argument("--keep-alive", action="store_true")
    return ap.parse_known_args()[0]


def main():
    args = _cli_args()

    slugs = args.slug or [s.strip() for s in (os.environ.get('G4F_SLUG') or "").split(",") if s.strip()]
    threshold = args.threshold if args.threshold is not None else float(os.environ.get('G4F_THRESHOLD') or "40")
    max_votes = args.max_votes if args.max_votes is not None else int(os.environ.get('G4F_MAX_VOTES') or "3")
    voter_name = args.voter_name or VOTER_NAME
    donate_cents = args.donate if args.donate is not None else (
        int(os.environ['G4F_DONATE_CENTS']) if os.environ.get('G4F_DONATE_CENTS') else None)
    keep_alive = args.keep_alive or os.environ.get('G4F_KEEP_ALIVE', '').lower() in ('1', 'true', 'yes')
    wait_cooldown = int(os.environ.get('G4F_WAIT_COOLDOWN') or "8")

    if not slugs and not args.list:
        log("缺少 G4F_SLUG（服务器 slug），请在 .env / Secrets 中配置")
        sys.exit(1)

    proc = None
    pw = browser = ctx = page = None
    exit_code = 0
    try:
        proc, cdp = launch_browser_cdp()
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)

        page.goto(SITE + "/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(1500)

        if args.list:
            servers = list_servers(page)
            log(f"共 {len(servers)} 台服务器")
            for s in sorted(servers, key=lambda x: (x.get('hours_remaining') is None, x.get('hours_remaining'))):
                flag = " [已挂起]" if s.get('is_suspended') else ""
                log(f"  {s.get('slug'):<28} {str(s.get('hours_remaining')):>6}h  "
                    f"today={s.get('vote_count_today'):<4} {s.get('ip')}{flag}")
            sys.exit(0)

        ip = get_current_ip()
        for slug in slugs:
            log(f"===== 处理服务器 {slug} =====")
            votes = 0
            attempts = 0
            status, before, after = "", None, None
            failed = False
            while True:
                info = server_info(page, slug)
                if info is None:
                    log("读取服务器信息失败，重试一次…")
                    time.sleep(3)
                    info = server_info(page, slug)
                    if info is None:
                        status, before, after = "读取服务器信息失败", None, None
                        failed = True
                        break
                srv = info.get("server") or {}
                h = srv.get("hours_remaining")
                cd = info.get("cooldown")
                before = h if before is None else before
                after = h
                log(f"剩余 {h}h | 冷却 {cd} 分钟 | 今日票 {srv.get('vote_count_today')}")

                if not keep_alive and h is not None and h >= threshold:
                    status = f"无需续期（已达阈值 {threshold}h）"
                    log(status)
                    break
                if votes >= max_votes:
                    status = f"已达本次上限 {max_votes} 票"
                    log(status)
                    break
                if cd:
                    if cd <= wait_cooldown:
                        log(f"冷却剩 {cd} 分钟，等待后重试…")
                        time.sleep(cd * 60 + 15)
                        continue
                    status = f"冷却中（{cd} 分钟）"
                    log(status)
                    break

                res = do_vote(page, slug, voter_name, donate_cents)
                if res.get("ok"):
                    votes += 1
                    after = res.get("after")
                    status = f"投票续期成功：{res.get('message')}"
                    log(f"✓ {res.get('before')}h -> {res.get('after')}h  {res.get('message')}")
                    if donate_cents:
                        log(f"支付链接: {(res.get('donate') or {}).get('redirect_url')}")
                        break
                else:
                    reason = res.get("error") or "未知原因"
                    log(f"✗ 投票失败: {reason}  (HTTP {res.get('http_status')})")
                    # 冷却类错误不用重试（等下一轮 cron 就行）
                    if "cooldown" in str(reason).lower() or "wait" in str(reason).lower():
                        status = f"投票续期失败（冷却中）：{reason}"
                        failed = True
                        break
                    # Turnstile 偶发失败很常见，重试（最多 max_votes 次机会用完）
                    if votes + attempts < max_votes + 2:
                        attempts += 1
                        log(f"重试（第 {attempts} 次）…")
                        time.sleep(10)
                        continue
                    status = f"投票续期失败：{reason}"
                    failed = True
                    break

                if keep_alive:
                    cd = res.get("cooldown")
                    if cd:
                        log(f"等待冷却 {cd} 分钟…")
                        time.sleep(min(cd, 6) * 60)

            send_telegram_notification(slug, status, before, after, ip)
            if failed:
                exit_code = 1

    except Exception as e:
        log(f"运行异常: {e}")
        try:
            if page:
                page.screenshot(path="g4f_error.png", timeout=15000, animations="disabled")
        except Exception:
            pass
        exit_code = 1
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass

    log("任务结束")
    # 失败必须返回非 0, 否则 GitHub Actions 会显示绿色但其实没投上票
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
