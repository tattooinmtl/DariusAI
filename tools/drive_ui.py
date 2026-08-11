import asyncio, json, subprocess, sys, tempfile, time, urllib.request
sys.path.insert(0, r"C:\.dariusai-harness\src")
from pathlib import Path
import websockets
from dariusai.brain.store import BrainStore
from dariusai.viz.server import create_app
from dariusai.viz.window import _start_server

SHOT = Path(sys.argv[1])
home = Path(tempfile.mkdtemp()) / "brain"
BrainStore(home)
app = create_app(home, project_dir=Path(r"C:\.dariusai-harness"))
server, port = _start_server(app, "127.0.0.1", 19010)
edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
proc = subprocess.Popen([edge, "--headless=new", "--disable-gpu", "--no-first-run",
                         f"--user-data-dir={tempfile.mkdtemp()}", "--remote-debugging-port=9333",
                         "--window-size=1280,840", f"http://127.0.0.1:{port}/"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def target():
    for _ in range(80):
        try:
            for t in json.load(urllib.request.urlopen("http://127.0.0.1:9333/json")):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("no devtools target")

async def main():
    ws_url = target()
    async with websockets.connect(ws_url, max_size=40_000_000) as ws:
        n = 0
        async def send(method, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == n:
                    return msg
        async def js(expr):
            r = await send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
            res = r.get("result", {})
            if "exceptionDetails" in res:
                return "JS ERROR: " + json.dumps(res["exceptionDetails"])[:400]
            return res.get("result", {}).get("value")

        await asyncio.sleep(3)
        print("1. settings opens:", await js("window.__openSettingsPanel(); 'ok'"))
        await asyncio.sleep(1.5)
        print("2. add-provider button:", await js("!!document.getElementById('addProviderBtn')"))
        await js("document.getElementById('addProviderBtn').click(); 'ok'")
        await asyncio.sleep(0.5)
        print("3. model field is a SELECT:", await js("(document.getElementById('pfModel')||{}).tagName"))
        print("4. load button present:", await js("!!document.getElementById('pfLoadModels')"))
        print("5. preset dropdown options:", await js("(document.getElementById('pfPreset')||{}).length"))
        # choose agnes-ai from presets, then save a key -> should auto-fetch
        await js("var s=document.getElementById('pfPreset');"
                 "for(var i=0;i<s.options.length;i++){if(s.options[i].value==='agnes-ai'){s.selectedIndex=i;}}"
                 "s.dispatchEvent(new Event('change')); 'ok'")
        await asyncio.sleep(0.3)
        print("6. preset filled name/url:", await js("document.getElementById('pfName').value + ' | ' + document.getElementById('pfBaseUrl').value"))
        await js("document.getElementById('pfApiKey').value='test-key-123';"
                 "document.getElementById('pfSave').click(); 'ok'")
        await asyncio.sleep(3)
        print("7. models loaded into select:", await js(
            "Array.prototype.map.call(document.getElementById('pfModel').options,function(o){return o.value}).join(', ')"))
        print("8. note text:", await js("(document.getElementById('pfModelNote')||{}).textContent"))
        shot = await send("Page.captureScreenshot", format="png")
        import base64
        SHOT.write_bytes(base64.b64decode(shot["result"]["data"]))
        print("screenshot:", SHOT)
    proc.terminate(); server.should_exit = True

asyncio.run(main())
