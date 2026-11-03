# -*- coding: utf-8 -*-
"""验证在 control.gaming4free.net 同源页面里 POST /vote 是否可行（本地辅助）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gaming4free_renew as G  # noqa: E402

proc = pw = None
try:
    proc, cdp = G.launch_browser_cdp()
    pw = G.sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # 先访问 control 域, 拿到同源上下文
    print("[t] 打开 control 域登录页…")
    try:
        r = page.goto("https://control.gaming4free.net/login", wait_until="domcontentloaded", timeout=60000)
        print("[t] 状态:", r.status if r else None, "| URL:", page.url[:100])
    except Exception as e:
        print("[t] 打开失败:", str(e)[:200])

    out = page.evaluate("""async ([api]) => {
        const o = {origin: location.origin};
        try {
            const g = await fetch('/api/servers/fun', {headers:{'Accept':'application/json'}});
            o.get_status = g.status;
            o.get_text = (await g.text()).slice(0,150);
        } catch(e) { o.get_error = String(e); }
        try {
            const p = await fetch('/api/servers/fun/vote', {method:'POST',
                headers:{'Content-Type':'application/json','Accept':'application/json'},
                body: JSON.stringify({'cf-turnstile-response':'test','voter_name':'probe','ad_watched':'0'})});
            o.post_status = p.status;
            o.post_text = (await p.text()).slice(0,300);
        } catch(e) { o.post_error = String(e); }
        return o;
    }""", [G.API])
    import json as _json
    print("[t] 同源测试结果:")
    print(_json.dumps(out, ensure_ascii=False, indent=2))
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
