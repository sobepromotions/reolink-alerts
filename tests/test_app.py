import asyncio, sys, logging, time
from datetime import time as dtime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reolink_alerts as ra
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixture  # noqa: F401  (test settings + temp folder, never your own files)
logging.basicConfig(level=logging.INFO, format="%(message)s")

class BC:
    def __init__(self): self.cbs = {}; self.events_active = True; self.subscribed = False
    def register_callback(self, cid, cb, cmd_id=None, channel=None): self.cbs[cid] = cb
    def unregister_callback(self, cid): self.cbs.pop(cid, None)
    async def subscribe_events(self): self.subscribed = True
    async def check_subscribe_events(self): pass
    async def unsubscribe_events(self): self.subscribed = False

class FakeHost:
    def __init__(self):
        self.baichuan = BC(); self.channels = [0, 1, 2, 3, 4]
        # Porch is on the NVR but not in settings.toml
        self.names = {0: "Frontyard", 1: "Driveway", 2: "Side Yard", 3: "Backyard", 4: "Porch"}
        self.state = {}; self.nvr_name = "NVR"; self.logged_out = False
    def camera_name(self, ch): return self.names[ch]
    def ai_detected(self, ch, key): return key in self.state.get(ch, set())
    def motion_detected(self, ch): return "md" in self.state.get(ch, set())
    def get_flv_stream_source(self, ch, stream): return f"https://192.0.2.10:443/flv?stream=channel{ch}_{stream}.bcs&user=alerts&password=pw"
    async def get_rtsp_stream_source(self, ch, stream): return f"rtsp://alerts:pw@192.0.2.10:554/Preview_{ch+1:02d}_{stream}"
    async def get_motion_state_all_ch(self): return True
    async def get_state(self, cmd, ch=None): pass
    def camera_online(self, ch): return ch != 3          # Backyard "offline"
    async def logout(self): self.logged_out = True

fake = FakeHost()
async def fake_make_host(s, pw): return fake
ra.make_host = fake_make_host
ra.write_input_conf = lambda s: None
procs = []
class P:
    def __init__(self, args, **kw): self.args = args; self.code = None; self.pid = 1; procs.append(self)
    def poll(self): return self.code
    def terminate(self): self.code = 1
    def wait(self, timeout=None): return self.code
    def kill(self): self.code = -9

s = ra.load_settings(ra.SETTINGS_PATH)
s.night_start = s.night_end = dtime(0, 0)            # daytime rules
s.offline_checks = 1
app = ra.App(s, "pw")
app.core.launcher = P
app.core.sounds.launcher = P
app.core.monitors = lambda: [ra.Monitor(1, 0, 0, 1920, 1080, 0, 0, 1920, 1032, True, "x")]
ra.find_mpv = lambda s: "mpv.exe"
played = []
_play = app.core.sounds.play
app.core.sounds.play = lambda path: (played.append(Path(path.replace("\\", "/")).name), _play(path))

def push(state):
    fake.state = state
    fake.baichuan.cbs[ra.APP_ID]()

def popups():
    return [p for p in procs if any(a.startswith("--geometry") for a in p.args)]

async def scenario():
    app.loop = asyncio.get_running_loop()
    main = asyncio.create_task(app.main())
    await asyncio.sleep(0.5)
    assert fake.baichuan.subscribed, "should subscribe"
    assert [c.name for c in s.cameras] == ["Frontyard", "Backyard"], [c.name for c in s.cameras]
    assert [c.channel for c in s.cameras] == [0, 3], [c.channel for c in s.cameras]
    front = next(a for a in app.core.alerts if a.cam.name == "Frontyard")
    back = next(a for a in app.core.alerts if a.cam.name == "Backyard")

    push({4: {"person"}})                       # Porch: not in settings.toml, so ignored
    await asyncio.sleep(0.3)
    assert not played and not popups(), (played, popups())

    push({3: {"pet", "vehicle"}})               # Backyard animal: its sound, no pop-up; vehicle ignored
    await asyncio.sleep(0.3)
    assert played == ["backyard animal.mp3"], played
    assert back.window is None and not popups()
    push({3: {"pet", "person"}})                # then a person: the other sound
    await asyncio.sleep(0.3)
    assert played == ["backyard animal.mp3", "backyard person.mp3"], played
    assert back.window is None and not popups()
    push({})
    await asyncio.sleep(0.3)

    push({0: {"person"}})                       # Front Yard person: sound + pop-up
    await asyncio.sleep(0.3)
    assert played[-1] == "front yard person.mp3", played
    assert front.window, "Front Yard pop-up should open"
    assert len(popups()) == 1
    push({0: {"person", "pet"}})                # a dog joins: its own sound, same pop-up
    await asyncio.sleep(0.3)
    assert played[-1] == "front yard animal.mp3", played
    assert len(popups()) == 1
    push({0: {"person", "pet", "vehicle"}})     # a car too: its own sound, same pop-up
    await asyncio.sleep(0.3)
    assert played[-1] == "front yard vehicle.mp3", played
    assert len(popups()) == 1

    push({3: {"md"}})                           # plain motion while motion alerts are off: nothing
    await asyncio.sleep(2.6)
    assert played[-1] == "front yard vehicle.mp3", played
    app.core.set_motion(True)                   # tray > Motion alerts
    push({})
    await asyncio.sleep(0.3)
    push({3: {"md"}})                           # Backyard plain motion: its sound after the wait
    await asyncio.sleep(0.5)
    assert played[-1] != "backyard motion.mp3", played
    await asyncio.sleep(2.0)
    assert played[-1] == "backyard motion.mp3", played
    app.core.set_motion(False)
    ra.STATE_PATH.unlink(missing_ok=True)

    await app.check_cameras(fake)               # Backyard reported offline once
    assert back.offline_warned
    app.stop_event.set()
    await asyncio.wait_for(main, 5)
    assert front.window is None and fake.logged_out
    print("app flow ok; sounds:", played, "; pop-ups:", len(popups()))

asyncio.run(scenario())
