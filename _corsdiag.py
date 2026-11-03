# -*- coding: utf-8 -*-
"""诊断 POST /vote 为何 Failed to fetch: 看 OPTIONS 预检是否被拦（本地辅助）。"""
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

    reqs = []
    page.on("request", lambda r: reqs.append((r.method, r.url)))
    fails = []
    page.on("requestfailed", lambda r: fails.append((r.method, r.url, str(r.failure))))

    page.goto(f"{G.SITE}/servers/fun", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)

    print("[d] ===== 手动 fetch 诊断 =====")
    out = page.evaluate("""async ([api]) => {
        const o = {};
        // 1) 简单 GET（不带自定义头）
        try {
            const r = await fetch(api + '/api/servers/fun');
            o.simple_get = r.status;
            await r.text();
        } catch(e) { o.simple_get = 'ERR ' + String(e); }
        // 2) 带自定义头的 GET（会触发预检）
        try {
            const r = await fetch(api + '/api/servers/fun',
                {headers:{'Content-Type':'application/json','Accept':'application/json'}, credentials:'omit'});
            o.preflight_get = r.status;
            await r.text();
        } catch(e) { o.preflight_get = 'ERR ' + String(e); }
        // 3) POST 空 body（必然触发预检）
        try {
            const r = await fetch(api + '/api/servers/fun/vote',
                {method:'POST', headers:{'Content-Type':'application/json','Accept':'application/json'},
                 credentials:'omit', body: JSON.stringify({})});
            o.post_empty = r.status;
            o.post_empty_text = (await r.text()).slice(0,300);
        } catch(e) { o.post_empty = 'ERR ' + String(e); }
        // 4) 显式 OPTIONS 预检
        try {
            const r = await fetch(api + '/api/servers/fun/vote',
                {method:'OPTIONS',
                 headers:{'Origin':'https://gaming4free.net',
                          'Access-Control-Request-Method':'POST',
                          'Access-Control-Request-Headers':'content-type'}});
            o.options_status = r.status;
            o.options_headers = {};
            r.headers.forEach((v,k)=>{ o.options_headers[k]=v; });
            await r.text();
        } catch(e) { o.options_status = 'ERR ' + String(e); }
        return o;
    }""", [G.API])

    import json as _json
    print(_json.dumps(out, ensure_ascii=False, indent=2))

    print("\n[d] ===== 失败请求 =====")
    for m, u, f in fails:
        print(f"  {m} {u[:110]}  -> {f[:160]}")

    print("\n[d] ===== 含 api/servers 的请求 =====")
    for m, u in reqs:
        if "api/servers" in u or "options" in m.lower():
            print(f"  {m} {u[:150]}")
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
