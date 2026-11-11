import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reolink_alerts as ra
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixture  # noqa: F401  (test settings + temp folder, never your own files)
from aiohttp import web

async def main():
    async def flv(request):
        resp = web.StreamResponse(); await resp.prepare(request)
        await resp.write(b"FLV\x01\x05\x00\x00\x00\x09"); await asyncio.sleep(0.2); return resp
    async def html(request): return web.Response(text="<html>login</html>")
    app = web.Application(); app.router.add_get("/flv", flv); app.router.add_get("/page", html)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 18431); await site.start()
    base = "http://127.0.0.1:18431"
    assert await ra.probe_flv(f"{base}/flv?stream=channel0_ext.bcs&password=x") is True
    assert await ra.probe_flv(f"{base}/page") is False                 # a web page, not video
    assert await ra.probe_flv(f"{base}/missing") is False              # 404
    assert await ra.probe_flv("http://127.0.0.1:18432/flv", timeout=2) is False   # nothing listening
    assert await ra.probe_flv(None) is False
    await runner.cleanup()
    print("probe ok")
asyncio.run(main())
