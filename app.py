#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import os, re, sys, time, random, shutil

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
EMAIL        = os.environ.get('BOT_HOSTING_EMAIL') or os.environ.get('EMAIL') or ""
PASSWORD     = os.environ.get('BOT_HOSTING_PASSWORD') or os.environ.get('PASSWORD') or ""
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""
IS_PROXY     = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER = (os.environ.get('PROXY_SERVER') or "http://127.0.0.1:1081").strip()
BROWSER_CHANNEL = os.environ.get('BROWSER_CHANNEL') or ""
SECURITY_TIMEOUT = int(os.environ.get('SECURITY_TIMEOUT') or "240")

REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

BASE_URL = "https://bot-hosting.net"
LOGIN_URL = f"{BASE_URL}/login"
BILLINGS_URL = f"{BASE_URL}/a/billings"

try:
    from patchright.sync_api import sync_playwright
    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USING_PATCHRIGHT = False

import requests

# ==================== 常量 ====================
CF_IFRAME_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'
SECURITY_TITLE_HINTS = ('security verification', 'just a moment', 'attention required',
                        'checking your browser', 'one more step')
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# 到期日(兼容中英文界面多种写法):
#   中文面板: "2026/10/28 到期" / "2026年10月28日"
#   英文面板: "Expires 10/28/2026"(月/日/年) / "Expires on Oct 28, 2026" / "2026-10-28"
DUE_DATE_RES = [
    re.compile(r'(?:expires?(?:\s+on)?|renew(?:al)?\s+date|due(?:\s+on)?|到期(?:时间|日)?)\s*:?\s*'
               r'([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4})', re.IGNORECASE),
    re.compile(r'(?:expires?(?:\s+on)?|renew(?:al)?\s+date|due(?:\s+on)?)\s*:?\s*'
               r'(\d{1,2}/\d{1,2}/\d{4})', re.IGNORECASE),
    re.compile(r'(?:expires?(?:\s+on)?|renew(?:al)?\s+date|due(?:\s+on)?)\s*:?\s*'
               r'(\d{4}-\d{1,2}-\d{1,2})', re.IGNORECASE),
    re.compile(r'(\d{4}/\d{1,2}/\d{1,2})\s*到期'),
    re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'),
    re.compile(r'(\d{4}/\d{1,2}/\d{1,2})'),
    re.compile(r'(\d{1,2}/\d{1,2}/\d{4})'),
    re.compile(r'(\d{4}-\d{2}-\d{2})'),
]
# 续期按钮(中/英界面)
RENEW_OPEN_TEXTS = ["续期免费套餐", "Renew free plan", "Renew free"]
RENEW_CONFIRM_TEXTS = ["续期 4 天", "Renew 4 days", "续期4天", "Renew 4 d"]
# 冷却期文案(中/英):
#   中文面板: "23:53:05 后可续期"
#   英文面板: "Renewal cooldown 23:07:37" / "Cooldown 5h 30m"
COOLDOWN_HINT_RE = re.compile(
    r'(\d{1,2}[:：]\d{2}[:：]\d{2})'
    r'|(\d+(?:\.\d+)?\s*(?:小时|分钟|秒|hours?|hrs?|minutes?|mins?|seconds?|secs?|h|m|s)\b)',
    re.IGNORECASE)
# 未到续期/冷却等提示(中英)
NOT_NOW_RE = re.compile(
    r"(已续期|无法续期|还不能续期|稍后再试|已达到|冷却|后可续期|"
    r"cooldown|too early|already renewed|can'?t renew|cannot renew|not yet|"
    r"available in|try again)", re.IGNORECASE)

MONTHS = {m: i + 1 for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}

def _norm_date(s):
    """把各种日期写法统一成 YYYY-MM-DD, 便于比较"""
    s = (s or "").strip().rstrip('.')
    if not s:
        return None
    m = re.match(r'([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})', s)   # Oct 28, 2026
    if m:
        mon = MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    m = re.match(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', s)            # YYYY/MM/DD
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)                  # MM/DD/YYYY 或 DD/MM/YYYY
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12:      # 首段>12 只能是日
            return f"{y}-{b:02d}-{a:02d}"
        return f"{y}-{a:02d}-{b:02d}"
    return None

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

def _parse_due(text):
    for rx in DUE_DATE_RES:
        m = rx.search(text)
        if m:
            if m.lastindex and m.lastindex >= 3 and '年' in rx.pattern:
                return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            norm = _norm_date(m.group(1))
            if norm:
                return norm
            return m.group(1).strip()
    return None

# ==================== IP / 通知 ====================
def get_current_ip():
    try:
        r = requests.get("https://api.ip.sb/ip", proxies=REQUESTS_PROXIES, timeout=15)
        if r.status_code == 200:
            return r.text.strip()
    except Exception as e:
        log(f"获取出口IP失败: {e}")
    return "获取失败"

def _fmt_date_cn(s):
    """YYYY-MM-DD -> 2026年10月29日 (周四)"""
    if not s or s == "未知":
        return "未知"
    try:
        dt = time.strptime(s, "%Y-%m-%d")
        return f"{dt.tm_year}年{dt.tm_mon:02d}月{dt.tm_mday:02d}日 (周{'一二三四五六日'[dt.tm_wday]})"
    except Exception:
        return s

def _days_left(s):
    if not s or s == "未知":
        return None
    try:
        return int(round((time.mktime(time.strptime(s, "%Y-%m-%d")) - time.time()) / 86400))
    except Exception:
        return None

def build_notification_text(status, old_due, new_due, current_ip="未知"):
    """构造 Telegram 通知文本(HTML)。独立成函数便于预览/测试。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        masked = f"{name[:2]}****{name[-2:]}@{domain}" if len(name) > 4 else f"{name}@{domain}"
    else:
        masked = (EMAIL[:2] + '****') if EMAIL else "未配置"

    if status.startswith("续期成功"):
        icon, title = "✅", "续期成功"
    elif "冷却" in status:
        icon, title = "⏳", "续期冷却中"
    elif "未到" in status:
        icon, title = "🕐", "未到续期时间"
    elif "失败" in status:
        icon, title = "❌", "续期失败"
    else:
        icon, title = "ℹ️", status

    left = _days_left(new_due)
    left_txt = f"（剩 {left} 天）" if left is not None else ""

    lines = [
        f"<b>{icon} Bot-Hosting 续期通知</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>状态</b>　　{title}",
        f"<b>账号</b>　　<code>{masked}</code>",
        "",
        "<b>📅 到期时间</b>",
        f"　续期前　{_fmt_date_cn(old_due)}",
        f"　续期后　<b>{_fmt_date_cn(new_due)}</b>{left_txt}",
    ]
    if "冷却" in status:
        lines.append(f"　等待　　{status}")
    lines += [
        "",
        "<b>🌐 运行环境</b>",
        f"　出口 IP　<code>{current_ip}</code>",
        f"　时间　　{now} (UTC+8)",
    ]
    return "\n".join(lines)

def send_telegram_notification(status, old_due, new_due, current_ip="未知"):
    """发送排版精美的 Telegram 通知 (HTML)"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("Telegram 未配置，跳过通知")
        return
    text = build_notification_text(status, old_due, new_due, current_ip)
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT_ID, "text": text,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True},
                          timeout=10, proxies=REQUESTS_PROXIES)
        if r.status_code == 200:
            log("Telegram 通知发送成功")
        else:
            log(f"Telegram HTML 通知失败({r.status_code})，回退纯文本: {r.text[:120]}")
            plain = re.sub(r'<[^>]+>', '', text)
            r2 = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                               json={"chat_id": TG_CHAT_ID, "text": plain},
                               timeout=10, proxies=REQUESTS_PROXIES)
            log("Telegram 通知发送成功(纯文本)" if r2.status_code == 200
                else f"Telegram 通知失败: {r2.text[:200]}")
    except Exception as e:
        log(f"Telegram 通知异常: {e}")

# ==================== Cloudflare 过盾 ====================
def _has_interstitial_iframe(page):
    try:
        for h in page.query_selector_all(CF_IFRAME_SELECTOR):
            if '/turnstile/' not in (h.get_attribute('src') or ''):
                return True
    except Exception:
        pass
    return False

def _is_security_check_page(page):
    try:
        title = (page.title() or '').lower()
        if any(h in title for h in SECURITY_TITLE_HINTS):
            return True
    except Exception:
        return True
    try:
        return page.evaluate("""() => {
            const t = document.body ? document.body.innerText.slice(0,5000).toLowerCase() : '';
            return t.includes('verify you are human') || t.includes('checking your browser')
                || t.includes('security verification');
        }""")
    except Exception:
        return True

def _find_visible_cf_frames(page):
    out = []
    try:
        for h in page.query_selector_all(CF_IFRAME_SELECTOR):
            try:
                b = h.bounding_box()
                if b and b['width'] > 10 and b['height'] > 10:
                    out.append(h)
            except Exception:
                continue
    except Exception:
        pass
    return out

def _click_cf_checkbox(page, frame_el=None):
    for frame in page.frames:
        if 'challenges.cloudflare.com' not in (frame.url or ''):
            continue
        try:
            frame.locator('input[type="checkbox"]').first.click(timeout=2500)
            return True
        except Exception:
            continue
    if frame_el is None:
        return False
    try:
        frame_el.scroll_into_view_if_needed(timeout=3000)
        box = frame_el.bounding_box()
        if not box:
            return False
        x = box['x'] + random.uniform(18, 34)
        y = box['y'] + box['height'] / 2 + random.uniform(-4, 4)
        page.mouse.move(x - random.uniform(30, 60), y + random.uniform(-8, 8))
        time.sleep(random.uniform(0.15, 0.4))
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.05, 0.2))
        page.mouse.down()
        time.sleep(random.uniform(0.04, 0.1))
        page.mouse.up()
        return True
    except Exception:
        return False

def _get_turnstile_token(page):
    try:
        return page.evaluate("""() => {
            if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                let t = null;
                try { t = window.turnstile.getResponse(); } catch(e) {}
                if (t && t.length > 30) return t;
                for (let i = 0; i < 5; i++) {
                    try { t = window.turnstile.getResponse(String(i)); } catch(e) { t = null; }
                    if (t && t.length > 30) return t;
                }
            }
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            if (inp && inp.value && inp.value.length > 30) return inp.value;
            return null;
        }""")
    except Exception:
        return None

def handle_cloudflare(page, timeout=None):
    if timeout is None:
        timeout = SECURITY_TIMEOUT
    def active():
        return _has_interstitial_iframe(page) or _is_security_check_page(page)
    if not active():
        return True
    log("检测到 Cloudflare 安全验证...")
    time.sleep(3)
    start = time.time()
    last_click = 0
    clear = 0
    while time.time() - start < timeout:
        if not active():
            clear += 1
            if clear >= 2:
                return True
            time.sleep(1)
            continue
        clear = 0
        if time.time() - last_click > 6:
            frames = _find_visible_cf_frames(page)
            if frames:
                log("点击 Turnstile 验证...")
                if _click_cf_checkbox(page, frames[-1]):
                    last_click = time.time()
                    time.sleep(random.uniform(2, 4))
                    continue
        time.sleep(1)
    log("CF 验证超时")
    return False

def _captcha_pending(page):
    """页面存在 Turnstile 占位节点但没有 token -> 验证尚未完成(可能是组件未渲染)"""
    try:
        return page.evaluate("""() => {
            const hasWidget = !!document.querySelector(
                '[id*="cf-chl-widget"], .cf-turnstile, #cf-turnstile, input[name="cf-turnstile-response"]');
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            const hasToken = !!(inp && inp.value && inp.value.length > 30);
            return hasWidget && !hasToken;
        }""")
    except Exception:
        return False

def solve_turnstile(page, timeout=90):
    """处理页面内嵌 Turnstile(登录表单 / 续期弹窗共用)。

    点击节奏保持 5 秒一次(实测可一次通过)。若验证框迟迟不出现,
    先确认是"无需验证"还是"组件被网络拦截未渲染", 后者直接返回 False,
    避免表单在无 token 状态下提交(会触发 Please complete the captcha 并使组件重置)。
    """
    if _get_turnstile_token(page):
        return True
    frames = _find_visible_cf_frames(page)
    if not frames:
        # 组件可能仍在加载: 最多再等 ~15 秒
        for _ in range(10):
            time.sleep(1.5)
            if _get_turnstile_token(page):
                return True
            frames = _find_visible_cf_frames(page)
            if frames:
                break
        if not frames:
            if _captcha_pending(page):
                log("⚠️ Turnstile 占位存在但未渲染（Cloudflare 资源可能被网络拦截），不提交表单")
                return False
            return True   # 页面确实无 Turnstile
    log("处理 Turnstile 人机验证...")
    start = time.time()
    last_click = 0
    clicks = 0
    while time.time() - start < timeout:
        if _get_turnstile_token(page):
            log("Turnstile 验证通过")
            return True
        frames = _find_visible_cf_frames(page)
        if not frames:
            break
        if time.time() - last_click > 5:
            log(f"点击 Turnstile 复选框 (第 {clicks + 1} 次)...")
            if _click_cf_checkbox(page, frames[-1]):
                clicks += 1
                last_click = time.time()
                time.sleep(random.uniform(2, 4))
                continue
        time.sleep(1)
    tok = bool(_get_turnstile_token(page))
    log("Turnstile 验证通过" if tok else "Turnstile 未确认通过")
    return tok

# ==================== 浏览器 ====================
def _detect_channel():
    if BROWSER_CHANNEL:
        return BROWSER_CHANNEL
    if sys.platform == 'win32':
        for ch, paths in {
            'msedge': [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                       r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
            'chrome': [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                       r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
        }.items():
            if any(os.path.exists(p) for p in paths):
                return ch
        return None
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return "chrome"
    if shutil.which("microsoft-edge"):
        return "msedge"
    return "chrome"

def open_browser(p):
    proxy_arg = {"server": PROXY_SERVER} if IS_PROXY else None
    channel = _detect_channel()
    if USING_PATCHRIGHT and channel:
        try:
            browser = p.chromium.launch(channel=channel, headless=False,
                                        args=['--disable-infobars'], proxy=proxy_arg)
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900}, proxy=proxy_arg)
            log(f"启动反检测浏览器 (patchright + {channel})")
            return browser, ctx.new_page()
        except Exception as e:
            log(f"patchright 启动失败: {e}")
    log("使用内置 chromium (过 CF 能力较弱)")
    browser = p.chromium.launch(headless=False,
        args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars'])
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900},
                              user_agent=DEFAULT_UA, proxy=proxy_arg)
    page = ctx.new_page()
    page.add_init_script("try { Object.defineProperty(navigator,'webdriver',{get:()=>undefined}); } catch(e) {}")
    return browser, page

# ==================== 登录(两步: 邮箱 -> 密码+Turnstile) ====================
def login(page):
    if not EMAIL or not PASSWORD:
        log("缺少登录凭证 (BOT_HOSTING_EMAIL/BOT_HOSTING_PASSWORD)")
        return False
    try:
        log("打开登录页...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        time.sleep(3)

        # 步骤1: 填邮箱, 点 "Continue with email"
        log("步骤1: 提交邮箱...")
        email_in = page.locator('input[type="email"], input#email').first
        email_in.wait_for(state="visible", timeout=30000)
        email_in.fill(EMAIL, timeout=10000)
        time.sleep(0.8)

        clicked = False
        for t in ("Continue with email", "使用邮箱继续", "Continue"):
            try:
                b = page.locator(f'button:has-text("{t}")').first
                if b.count() > 0:
                    b.click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            email_in.press("Enter")
        time.sleep(4)
        handle_cloudflare(page, timeout=60)

        # 步骤2: 填密码 + Turnstile
        log("步骤2: 提交密码...")
        pwd_in = page.locator('input[type="password"]').first
        pwd_in.wait_for(state="visible", timeout=30000)
        pwd_in.fill(PASSWORD, timeout=10000)
        time.sleep(0.6)
        solve_turnstile(page, timeout=90)

        log("点击登录...")
        submitted = False
        for sel in ('button:has-text("Sign in")', 'button:has-text("登录")',
                    'button[type="submit"]'):
            try:
                b = page.locator(sel).first
                if b.count() > 0:
                    b.click(timeout=8000)
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted:
            pwd_in.press("Enter")

        time.sleep(6)
        handle_cloudflare(page)
        start = time.time()
        while time.time() - start < 90:
            u = (page.url or '').lower()
            if '/login' not in u and 'signin' not in u:
                log(f"登录成功: {page.url}")
                return True
            handle_cloudflare(page, timeout=10)
            time.sleep(1)

        # 失败: 记录原因
        msgs = page.evaluate(
            "() => Array.from(document.querySelectorAll('.alert,[role=alert]')).map(m => (m.innerText||'').trim())"
        )
        log(f"登录失败, 页面提示: {msgs}")
        page.screenshot(path="login_fail.png")
        return False
    except Exception as e:
        log(f"登录异常: {e}")
        try:
            page.screenshot(path="login_fail.png")
        except Exception:
            pass
        return False

# ==================== 到期时间 ====================
def get_due_date(page):
    try:
        text = page.locator("body").inner_text()
        due = _parse_due(text)
        if due:
            return due
    except Exception as e:
        log(f"读取到期时间异常: {e}")
    return "未知"

def _read_due_with_retry(page, old_due, retries=4):
    new_due = old_due
    for i in range(retries):
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)
            time.sleep(3)
        except Exception:
            pass
        new_due = get_due_date(page)
        if new_due != old_due:
            break
        if i < retries - 1:
            log("到期时间尚未刷新，4 秒后重试...")
            time.sleep(4)
    return new_due

# ==================== 续期 ====================
def _visible_messages(page):
    try:
        return page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('[role="dialog"], [role="alert"], .alert, .toast, [data-sonner-toast]')
                .forEach(m => {
                    const r = m.getBoundingClientRect();
                    const t = (m.innerText||'').trim();
                    if (t && r.width > 0 && r.height > 0) out.push(t);
                });
            return out;
        }""")
    except Exception:
        return []

def _detect_cooldown(page):
    """检测续期冷却期。免费套餐续期有间隔限制, 冷却中按钮显示倒计时且禁用。
    兼容中文("23:53:05 后可续期")与英文("Renewal cooldown 23:07:37")界面。
    返回冷却剩余时间文本, 否则返回空串"""
    def _pick(text):
        m = COOLDOWN_HINT_RE.search(text)
        if not m:
            return ""
        return (m.group(1) or m.group(2) or "").strip()

    try:
        info = page.evaluate("""() => {
            const out = {texts: []};
            document.querySelectorAll('button').forEach(b => {
                const t = (b.innerText||'').trim();
                if (t && b.disabled) out.texts.push(t);
            });
            out.body = document.body ? document.body.innerText.slice(0, 4000) : '';
            return out;
        }""")
        for t in info.get("texts", []):
            cd = _pick(t)
            if cd:
                return cd
        return _pick(info.get("body", ""))
    except Exception:
        pass
    return ""

def _click_text(page, texts, timeout=10000, wait_rounds=20):
    """点击首个可用且可见的按钮; 支持等 SPA 渲染。wait_rounds=0 表示不等待"""
    for t in texts:
        try:
            loc = page.locator(f'button:has-text("{t}")')
            for _ in range(wait_rounds):
                if loc.count() > 0:
                    break
                time.sleep(1)
            if loc.count() == 0:
                continue
            btn = loc.first
            btn.wait_for(state="visible", timeout=8000)
            try:
                if btn.is_disabled():
                    continue
            except Exception:
                pass
            btn.scroll_into_view_if_needed()
            btn.click(timeout=timeout)
            return t
        except Exception:
            continue
    return None

def _dump_buttons(page):
    try:
        btns = page.evaluate("""() => Array.from(document.querySelectorAll('button'))
            .map(b => ({t: (b.innerText||'').trim().slice(0,40), d: b.disabled}))
            .filter(x => x.t)""")
        desc = [(b["t"] + ("(禁用)" if b["d"] else "")) for b in btns][:30]
        log(f"页面按钮: {desc}")
    except Exception:
        pass

def renew_plan(page):
    """打开续期弹窗 -> 过 Turnstile -> 点确认续期。返回 (结果, 旧到期, 新到期)"""
    try:
        log("进入续期流程...")
        # 必须确保在账单页(续期按钮只在 /a/billings 出现)
        if 'billings' not in (page.url or ''):
            page.goto(BILLINGS_URL, wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)
            time.sleep(4)
        log(f"当前页面: {page.url}")

        old_due = get_due_date(page)
        log(f"续期前到期: {old_due}")

        # 先判冷却期(续期有间隔限制, 冷却中按钮文案是动态倒计时, 不可能匹配到)
        cd = _detect_cooldown(page)
        if cd:
            log(f"处于续期冷却期，剩余 {cd}")
            return "COOLDOWN", old_due, old_due, cd

        # 打开续期弹窗(先等 SPA 渲染, 最多 15 秒)
        log("点击『续期免费套餐』...")
        opened = _click_text(page, RENEW_OPEN_TEXTS, wait_rounds=15)
        if opened is None:
            # 可能处于冷却期: 按钮文案为 "23:53:05 后可续期"(禁用)
            cd = _detect_cooldown(page)
            if cd:
                log(f"处于续期冷却期: {cd}")
                _dump_buttons(page)
                return "COOLDOWN", old_due, old_due, cd
            log("未找到续期按钮(可能是付费套餐或界面变化)")
            _dump_buttons(page)
            page.screenshot(path="renew_no_button.png")
            return "NOT_TIME", old_due, old_due, ""
        log(f"已点击: {opened}")
        time.sleep(4)

        # 弹窗内人机验证
        log("弹窗内 Turnstile 验证...")
        if not solve_turnstile(page, timeout=90):
            log("Turnstile 未通过，尝试继续(按钮可能已解锁)")
        time.sleep(2)

        # 确认按钮解锁后再点
        confirm = None
        for i in range(6):
            for t in RENEW_CONFIRM_TEXTS:
                loc = page.locator(f'button:has-text("{t}")')
                if loc.count() > 0 and not loc.first.is_disabled():
                    confirm = loc.first
                    log(f"确认按钮已解锁: {t}")
                    break
            if confirm:
                break
            log("确认按钮仍禁用，重新过验证...")
            solve_turnstile(page, timeout=45)
            time.sleep(3)

        if confirm is None:
            msgs = _visible_messages(page)
            log(f"确认按钮不可用，页面提示: {msgs}")
            page.screenshot(path="renew_captcha_fail.png")
            return False, old_due, old_due, ""

        log("点击确认续期...")
        confirm.scroll_into_view_if_needed()
        confirm.click(timeout=10000)
        time.sleep(6)

        # 结果: 到期日变化为准
        new_due = get_due_date(page)
        if new_due == old_due:
            new_due = _read_due_with_retry(page, old_due)

        msgs = _visible_messages(page)
        if msgs:
            log(f"页面提示: {msgs}")
        if any(NOT_NOW_RE.search(m) for m in msgs) and new_due == old_due:
            cd = _detect_cooldown(page)
            if cd:
                log(f"处于续期冷却期: {cd}")
                return "COOLDOWN", old_due, old_due, cd
            log("未到续期时间")
            return "NOT_TIME", old_due, old_due, ""

        if new_due != old_due and new_due != "未知":
            log(f"续期成功! 到期 {old_due} -> {new_due}")
            return True, old_due, new_due, ""

        log(f"到期时间未变化: {old_due}")
        page.screenshot(path="renew_no_change.png")
        return False, old_due, new_due, ""
    except Exception as e:
        log(f"续期异常: {e}")
        try:
            page.screenshot(path="renew_error.png")
        except Exception:
            pass
        return False, "未知", "未知", ""

# ==================== 主流程 ====================
def main():
    log(f"凭证检测: EMAIL={'已配置' if EMAIL else '未配置'}, PASSWORD={'已配置' if PASSWORD else '未配置'}")
    if not EMAIL or not PASSWORD:
        log("缺少登录凭证，退出")
        sys.exit(1)

    with sync_playwright() as p:
        browser = None
        try:
            if IS_PROXY:
                log(f"代理已启用: {PROXY_SERVER}")
            current_ip = get_current_ip()
            log(f"当前出口IP: {current_ip}")

            browser, page = open_browser(p)
            page.set_default_timeout(15000)

            if current_ip == "获取失败":
                try:
                    page.goto("https://api.ip.sb/ip", wait_until="domcontentloaded", timeout=20000)
                    t = page.locator("body").inner_text().strip()
                    if t and len(t) < 50:
                        current_ip = t
                        log(f"当前出口IP(浏览器): {current_ip}")
                except Exception:
                    pass

            if not login(page):
                send_telegram_notification("登录失败", "-", "-", current_ip)
                sys.exit(1)

            result, old_due, new_due, cooldown = renew_plan(page)

            if result == "COOLDOWN":
                status = f"冷却期中（剩余 {cooldown}）"
            elif result == "NOT_TIME":
                status = "未到续期时间"
            elif result is True:
                status = "续期成功"
            else:
                status = "续期失败"
            log(f"结论: {status} ({old_due} -> {new_due})")

            send_telegram_notification(status, old_due, new_due, current_ip)
            # 冷却期属于正常等待状态, 不算失败
            sys.exit(0 if result is True or result in ("NOT_TIME", "COOLDOWN") else 1)
        except Exception as e:
            log(f"运行出错: {e}")
            sys.exit(1)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

if __name__ == "__main__":
    main()
