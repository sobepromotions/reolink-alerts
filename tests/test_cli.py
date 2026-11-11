import asyncio, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reolink_alerts as ra
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixture  # noqa: F401  (test settings + temp folder, never your own files)

class BC:
    async def unsubscribe_events(self): pass
class FakeHost:
    def __init__(self):
        self.baichuan = BC(); self.channels = [0, 1, 2, 3, 4]
        self.names = {0: "Frontyard", 1: "Driveway", 2: "Side Yard", 3: "Backyard", 4: "Porch"}
        self.nvr_name, self.model, self.sw_version = "NVR", "RP-PN8", "v3.6.0"
    def camera_name(self, ch): return self.names[ch]
    def camera_model(self, ch): return {1: "RP-PCB8MX", 2: "FE-P"}.get(ch, "CX820")
    def camera_online(self, ch): return True
    def ai_supported_types(self, ch): return ["people", "vehicle", "dog_cat"]
    def ai_supported(self, ch, key): return key in ("person", "vehicle", "pet")
    def supported(self, ch, cap): return cap == "motion_detection"
    rtmp_enabled = False
    def get_flv_stream_source(self, ch, stream):
        return f"https://192.0.2.10:443/flv?port=1935&app=bcs&stream=channel{ch}_{stream}.bcs&user=alerts&password=secret"
    async def get_rtsp_stream_source(self, ch, stream): return f"rtsp://alerts:secret@192.0.2.10:554/Preview_{ch+1:02d}_{stream}"
    async def logout(self): pass

async def fake_make_host(s, pw): return FakeHost()
async def no_flv(url, timeout=6): return False
ra.probe_flv = no_flv
ra.make_host = fake_make_host
ra.get_password = lambda s: "pw"
ra.find_mpv = lambda s: "mpv.exe"
ra.write_input_conf = lambda s: None
class P:
    def __init__(self, args, **kw): self.args = args; self.code = None; self.pid = 1; print("  launch:", " ".join(a for a in args[:2]), "...")
    def poll(self): return self.code
    def terminate(self): self.code = 1
    def wait(self, timeout=None): return self.code
    def kill(self): pass
ra.subprocess.Popen = P
d = ra.AlertCore.__init__.__defaults__
ra.AlertCore.__init__.__defaults__ = (P,) + d[1:]
ra.SoundQueue.__init__.__defaults__ = (P,)
ra.Path.is_file = lambda self: True          # pretend the Windows sound files exist

s = ra.load_settings(ra.SETTINGS_PATH)
asyncio.run(ra.run_check(s))
print("---- test")
logging.basicConfig(level=logging.INFO, format="%(message)s")
s = ra.load_settings(ra.SETTINGS_PATH)
asyncio.run(ra.run_test(s, ["frontyard"], "vehicle", seconds=0.6))
s = ra.load_settings(ra.SETTINGS_PATH)
asyncio.run(ra.run_test(s, [], "person", seconds=0.6))
s = ra.load_settings(ra.SETTINGS_PATH)
asyncio.run(ra.run_test(s, ["Backyard"], "animal", seconds=0.6))
s = ra.load_settings(ra.SETTINGS_PATH)
asyncio.run(ra.run_test(s, ["Frontyard"], "motion", seconds=0.6))
s = ra.load_settings(ra.SETTINGS_PATH); s.stream = "ext"
asyncio.run(ra.run_test(s, ["Frontyard"], "person", seconds=0.6))
