import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reolink_alerts as ra
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixture  # noqa: F401  (test settings + temp folder, never your own files)
class BC:
    async def unsubscribe_events(self): pass
class FakeHost:
    baichuan = BC(); channels = [0, 3]; names = {0: "Frontyard", 3: "Backyard"}
    def camera_name(self, ch): return self.names[ch]
    async def get_rtsp_stream_source(self, ch, stream): return f"rtsp://alerts:x@nvr:554/Preview_{ch+1:02d}_{stream}"
    def get_flv_stream_source(self, ch, stream): return f"https://nvr:443/flv?stream=channel{ch}_{stream}.bcs&password=x"
    async def logout(self): pass
async def fake_make_host(s, pw): return FakeHost()
ra.make_host = fake_make_host
ra.get_password = lambda s: "pw"
ra.find_mpv = lambda s: "mpv.exe"
ra.write_input_conf = lambda s: None
geoms = []
class P:
    def __init__(self, args, **kw):
        self.args = args; self.code = None; self.pid = 1
        geoms.extend(a for a in args if a.startswith("--geometry") or a.startswith("http") or a.startswith("rtsp"))
    def poll(self): return self.code
    def terminate(self): self.code = 1
    def wait(self, timeout=None): return self.code
    def kill(self): pass
d = ra.AlertCore.__init__.__defaults__
ra.AlertCore.__init__.__defaults__ = (P,) + d[1:]
ra.SoundQueue.__init__.__defaults__ = (P,)
ra.Path.is_file = lambda self: True
ra.list_monitors = lambda: [ra.Monitor(3, 0, 0, 3440, 1440, 0, 0, 3440, 1392, True, "main")]
orig = ra.run_test
async def quick(s, names, kind, seconds=15): await orig(s, names, kind, seconds=0.3)
ra.run_test = quick
for args in (["--size", "normal"], ["--size", "large"], ["--size", "extralarge"],
             ["--size", "large", "--stream", "balanced"], ["--size", "giant"]):
    geoms.clear()
    sys.argv = ["reolink_alerts.py", "--test", "Frontyard", *args]
    ra.main()
    print("   ", " ".join(args), "->", [g if not g.startswith(("http", "rtsp")) else g.split("?")[0].split("/")[-1] for g in geoms])
    logging.getLogger("reolink_alerts").handlers.clear()
