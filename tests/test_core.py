import asyncio, sys
from datetime import time as dtime
from pathlib import Path, PureWindowsPath
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reolink_alerts as ra
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixture  # noqa: F401  (test settings + temp folder, never your own files)

s = ra.load_settings(ra.SETTINGS_PATH)
assert [c.name for c in s.cameras] == ["Frontyard", "Backyard"]
fy, by = s.cameras
assert fy.popup and not by.popup
assert fy.day == ["person", "animal", "vehicle", "motion"] and fy.night == ["person"]
assert by.day == ["person", "animal", "motion"] and by.night == ["person"]
assert s.max_popups == 1 and s.size_name == "Normal" and s.size_percent == 40
assert s.sizes == {"Normal": 40, "Large": 60, "Extra large": 80}
assert s.volume == 100 and "--volume=100" in ra.sound_args("mpv.exe", "x.mp3", s.volume) and "--no-config" in ra.sound_args("mpv.exe", "x.mp3", 100)
assert s.motion_alerts is False and s.motion_wait_seconds == 2 and ra.has_motion(s)
assert s.stream == "main" and s.fallback_to_sub
assert ra.mask("https://nvr:443/flv?port=1935&app=bcs&stream=channel0_ext.bcs&user=alerts&password=a&b#c") \
    == "https://nvr:443/flv?port=1935&app=bcs&stream=channel0_ext.bcs&user=alerts&password=****"
names = {k: PureWindowsPath(v).name for k, v in fy.sounds.items()}
bnames = {k: PureWindowsPath(v).name for k, v in by.sounds.items()}
assert names == {"person": "front yard person.mp3", "animal": "front yard animal.mp3",
                 "vehicle": "front yard vehicle.mp3", "motion": "front yard motion.mp3"}, names
assert bnames == {"person": "backyard person.mp3", "animal": "backyard animal.mp3",
                  "motion": "backyard motion.mp3"}, bnames
assert all(PureWindowsPath(v).parent.name == "sound files" for v in list(fy.sounds.values()) + list(by.sounds.values()))
print("settings ok")

for t, want in [((0, 30), True), ((10, 59), True), ((11, 0), False), ((23, 58), False), ((23, 59), True)]:
    assert ra.in_window(dtime(*t), dtime(23, 59), dtime(11, 0)) is want, t
print("night window ok")

main = ra.Monitor(3, 0, 0, 3440, 1440, 0, 0, 3440, 1392, True, "main")   # an ultrawide screen
assert ra.slot_geometry(s, main, 0) == (1376, 774, 3440 - 24 - 1376, 1392 - 24 - 774), ra.slot_geometry(s, main, 0)
s.size_percent = 55
assert ra.slot_geometry(s, main, 0)[:2] == (1892, 1064)
s.size_percent = 70                                   # too tall: shrinks to fit the height
w, h, x, y = ra.slot_geometry(s, main, 0)
assert h == 1392 - 48 and w == int(h * 16 / 9) and y == 24, (w, h, x, y)
s.size_percent = 40
print("geometry ok")

class FakeProc:
    def __init__(self, args, **kw): self.args, self.code, self.pid = args, None, 1
    def poll(self): return self.code
    def terminate(self): self.code = 1 if self.code is None else self.code
    def wait(self, timeout=None): return self.code
    def kill(self): self.code = -9
launched = []
def launcher(args, **kw):
    p = FakeProc(args, **kw); launched.append(p); return p
class Clock:
    t = 1000.0
    def __call__(self): return self.t
clock = Clock()
class Host:
    state = {}
    def ai_detected(self, ch, key): return key in self.state.get(ch, set())
    def motion_detected(self, ch): return "md" in self.state.get(ch, set())
host = Host()

def fresh(night=False):
    st = ra.load_settings(ra.SETTINGS_PATH)
    if night: st.night_start, st.night_end = dtime(0, 0), dtime(23, 59, 59)
    else: st.night_start = st.night_end = dtime(0, 0)
    for i, c in enumerate(st.cameras):
        c.channel, c.main_url, c.sub_url = [0, 3][i], f"rtsp://u:p@nvr/ch{i}_main", f"rtsp://u:p@nvr/ch{i}_sub"
    core = ra.AlertCore(st, launcher=launcher, clock=clock, monitors=lambda: [main])
    core.mpv = "mpv.exe"
    played = []
    core.sounds.play = lambda path: played.append(PureWindowsPath(path).name)
    launched.clear()
    return st, core, played

# Front Yard person: sound + pop-up, closes after linger
st, core, played = fresh(); clock.t = 1000
host.state = {0: {"person"}}; core.evaluate(host)
fa = core.alerts[0]
assert played == ["front yard person.mp3"] and fa.window
assert "--geometry=1376x774+2040+594" in launched[-1].args and "--screen=2" in launched[-1].args, launched[-1].args
clock.t = 1003; host.state = {}; core.evaluate(host)
clock.t = 1010; core.tick(); assert fa.window
clock.t = 1018.5; core.tick(); assert fa.window is None
print("front yard flow ok")

# Front Yard animal (different sound) then person joins: second sound, same window
st, core, played = fresh(); clock.t = 2000
host.state = {0: {"pet"}}; core.evaluate(host)
assert played == ["front yard animal.mp3"] and core.alerts[0].window
clock.t = 2002; host.state = {0: {"pet", "person"}}; core.evaluate(host)
assert played == ["front yard animal.mp3", "front yard person.mp3"] and len(launched) == 1
print("animal + person ok")

# Front Yard vehicle by day: its own sound + the pop-up; Backyard ignores vehicles
st, core, played = fresh(); clock.t = 2500
host.state = {0: {"vehicle"}, 3: {"vehicle"}}; core.evaluate(host)
assert played == ["front yard vehicle.mp3"] and core.alerts[0].window and len(launched) == 1, played
assert core.alerts[1].window is None
print("vehicle ok")

# Backyard: sound only, person and animal have different sounds, never a pop-up
st, core, played = fresh(); clock.t = 3000
host.state = {3: {"person"}}; core.evaluate(host)
host.state = {}; core.evaluate(host)
clock.t = 3001; host.state = {3: {"pet"}}; core.evaluate(host)
assert played == ["backyard person.mp3", "backyard animal.mp3"], played
assert not launched and core.alerts[1].window is None
print("backyard sound-only ok")

# Front Yard + Backyard at once: one pop-up (Front Yard), both sounds
st, core, played = fresh(); clock.t = 4000
host.state = {0: {"person"}, 3: {"person"}}; core.evaluate(host)
assert len(launched) == 1 and core.alerts[0].window and len(played) == 2
print("one pop-up ok")

# max time + cooldown, click-to-close, fallback to Fluent
st, core, played = fresh(); clock.t = 5000
host.state = {0: {"person"}}; core.evaluate(host)
clock.t = 5091; core.tick(); assert core.alerts[0].window is None and core.alerts[0].cooldown_until == 5121
st, core, played = fresh(); clock.t = 6000
host.state = {0: {"person"}}; core.evaluate(host); launched[-1].code = ra.USER_CLOSED_EXIT
clock.t = 6002; core.tick(); core.evaluate(host); assert core.alerts[0].window is None
st, core, played = fresh(); clock.t = 7000
host.state = {0: {"person"}}; core.evaluate(host); launched[-1].code = 2
clock.t = 7003; core.tick(); assert core.alerts[0].window.stream == "sub"
print("max/click/fallback ok")

# night: only person
st, core, played = fresh(night=True); clock.t = 8000
host.state = {0: {"pet", "vehicle"}, 3: {"pet", "vehicle"}}; core.evaluate(host)
assert not played and not launched
clock.t = 8001; host.state = {0: {"person"}}; core.evaluate(host)
assert played == ["front yard person.mp3"] and len(launched) == 1
print("night ok")

# plain motion: off by default, so movement alone does nothing
st, core, played = fresh(); clock.t = 10000
host.state = {0: {"md"}, 3: {"md"}}; core.evaluate(host)
clock.t = 10005; core.tick()
assert not played and not launched
print("motion off ok")

# motion on: Front Yard pop-up at once, its motion sound after the wait; Backyard sound only
st, core, played = fresh(); st.motion_alerts = True; clock.t = 11000
host.state = {0: {"md"}, 3: {"md"}}; core.evaluate(host)
assert len(launched) == 1 and core.alerts[0].window and not played
clock.t = 11001; core.tick(); assert not played
clock.t = 11002; core.tick()
assert sorted(played) == ["backyard motion.mp3", "front yard motion.mp3"], played
assert core.alerts[1].window is None and len(launched) == 1
assert "Motion" in " ".join(launched[0].args)                  # the pop-up caption says Motion
print("motion on ok")

# motion then a person within the wait: only the person sound
st, core, played = fresh(); st.motion_alerts = True; clock.t = 12000
host.state = {0: {"md"}}; core.evaluate(host)
clock.t = 12000.8; host.state = {0: {"md", "person"}}; core.evaluate(host)
clock.t = 12005; core.tick()
assert played == ["front yard person.mp3"] and len(launched) == 1, played
# motion and an animal in the same message: only the animal sound
st, core, played = fresh(); st.motion_alerts = True; clock.t = 13000
host.state = {3: {"md", "pet"}}; core.evaluate(host)
clock.t = 13005; core.tick()
assert played == ["backyard animal.mp3"], played
# a person first, then plain motion lingers in the same event: still no motion sound
st, core, played = fresh(); st.motion_alerts = True; clock.t = 13500
host.state = {0: {"md", "person"}}; core.evaluate(host)
clock.t = 13501; host.state = {0: {"md"}}; core.evaluate(host)
clock.t = 13510; core.tick()
assert played == ["front yard person.mp3"], played
print("motion skipped for person/animal ok")

# brief motion that ends before the wait still sounds; switching motion off cancels a waiting sound
st, core, played = fresh(); st.motion_alerts = True; clock.t = 14000
host.state = {3: {"md"}}; core.evaluate(host)
clock.t = 14000.5; host.state = {}; core.evaluate(host)
clock.t = 14002; core.tick()
assert played == ["backyard motion.mp3"], played
st, core, played = fresh(); st.motion_alerts = True; clock.t = 15000
ra.STATE_PATH.unlink(missing_ok=True)
host.state = {3: {"md"}}; core.evaluate(host)
core.set_motion(False)
clock.t = 15005; core.tick()
assert not played
st2 = ra.load_settings(ra.SETTINGS_PATH); ra.load_state(st2)
assert st2.motion_alerts is False
core.set_motion(True)
st2 = ra.load_settings(ra.SETTINGS_PATH); ra.load_state(st2)
assert st2.motion_alerts is True                               # remembered after a restart
ra.STATE_PATH.unlink()
print("motion timing + switch ok")

# motion at night: nothing (night is person only)
st, core, played = fresh(night=True); st.motion_alerts = True; clock.t = 16000
host.state = {0: {"md"}, 3: {"md"}}; core.evaluate(host)
clock.t = 16005; core.tick()
assert not played and not launched
print("motion night ok")

# pop-up feeds: Balanced uses the FLV address; a feed that won't play steps down Clear > Balanced > Fluent
def with_ext(st):
    for c in st.cameras:
        c.ext_url = "https://nvr:443/flv?port=1935&app=bcs&stream=channel0_ext.bcs&user=u&password=p"
st, core, played = fresh(); with_ext(st); st.stream = "ext"; clock.t = 17000
host.state = {0: {"person"}}; core.evaluate(host)
assert launched[-1].args[1].startswith("https://nvr:443/flv") and core.alerts[0].window.stream == "ext"
launched[-1].code = 2; clock.t = 17002; core.tick()
assert core.alerts[0].window.stream == "sub" and launched[-1].args[1].endswith("ch0_sub")
launched[-1].code = 2; clock.t = 17003; core.tick()
assert core.alerts[0].window is None and len(launched) == 2           # nothing below Fluent
st, core, played = fresh(); with_ext(st); clock.t = 17100              # Clear fails -> Balanced -> Fluent
host.state = {0: {"person"}}; core.evaluate(host)
launched[-1].code = 2; clock.t = 17101; core.tick()
assert core.alerts[0].window.stream == "ext"
launched[-1].code = 2; clock.t = 17102; core.tick()
assert core.alerts[0].window.stream == "sub" and len(launched) == 3
st, core, played = fresh(); st.stream = "ext"; clock.t = 17200         # no Balanced address: Fluent
host.state = {0: {"person"}}; core.evaluate(host)
assert core.alerts[0].window.stream == "sub"
st, core, played = fresh(); with_ext(st); st.fallback_to_sub = False; clock.t = 17300
host.state = {0: {"person"}}; core.evaluate(host)
launched[-1].code = 2; clock.t = 17301; core.tick()
assert core.alerts[0].window is None                                  # step-down switched off
st, core, played = fresh()
ra.STATE_PATH.unlink(missing_ok=True)
core.set_stream("ext")
st2 = ra.load_settings(ra.SETTINGS_PATH); ra.load_state(st2)
assert st2.stream == "ext"                                            # remembered after a restart
core.set_stream("sub"); st2 = ra.load_settings(ra.SETTINGS_PATH); ra.load_state(st2)
assert st2.stream == "sub"
ra.STATE_PATH.unlink()
print("feeds ok")

# size picker: remembered in state.json
st, core, played = fresh()
ra.STATE_PATH.unlink(missing_ok=True)
core.set_size("Large")
assert st.size_percent == 60 and ra.STATE_PATH.is_file()
st2 = ra.load_settings(ra.SETTINGS_PATH); ra.load_state(st2)
assert st2.size_name == "Large" and st2.size_percent == 60
clock.t = 9000; host.state = {0: {"person"}}; core.evaluate(host)
assert "--geometry=2064x1161+1352+207" in launched[-1].args, launched[-1].args
ra.STATE_PATH.unlink()
for typed, want in [("normal", "Normal"), ("Large", "Large"), ("extralarge", "Extra large"),
                    ("Extra large", "Extra large"), ("extra-large", "Extra large"), ("huge", None)]:
    assert ra.size_named(st, typed) == want, (typed, ra.size_named(st, typed))
print("size picker ok")

# once per visit (v1.3.3): settings
assert s.clear_seconds == 120 and s.repeat_seconds == 30
import tempfile
text = ra.SETTINGS_PATH.read_text(encoding="utf-8")
no_line = "\n".join(l for l in text.splitlines() if not l.startswith("clear_seconds"))
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "settings.toml"; p.write_text(no_line, encoding="utf-8")
    assert ra.load_settings(p).clear_seconds == 120              # missing line = built-in 2 minutes
    p.write_text(text.replace("clear_seconds = 120", "clear_seconds = 300"), encoding="utf-8")
    assert ra.load_settings(p).clear_seconds == 300

# the dog wandering in and out of view: one sound; back after 2+ min clear: a new visit
st, core, played = fresh(); clock.t = 20000
host.state = {3: {"pet"}}; core.evaluate(host)                    # dog out: sound
clock.t = 20005; host.state = {}; core.evaluate(host)             # out of view
clock.t = 20045; host.state = {3: {"pet"}}; core.evaluate(host)   # back after 40 s (old rule would sound)
clock.t = 20050; host.state = {}; core.evaluate(host)
clock.t = 20160; host.state = {3: {"pet"}}; core.evaluate(host)   # back after 110 s: same visit
clock.t = 20170; host.state = {}; core.evaluate(host)
assert played == ["backyard animal.mp3"], played
clock.t = 20290.5; host.state = {3: {"pet"}}; core.evaluate(host) # back after 120.5 s clear: new visit
assert played == ["backyard animal.mp3"] * 2, played
clock.t = 20300; host.state = {3: {"pet", "person"}}; core.evaluate(host)   # a person while the dog is out
assert played[-1] == "backyard person.mp3" and len(played) == 3, played
clock.t = 20310; host.state = {3: {"pet"}}; core.evaluate(host)
clock.t = 20330; host.state = {3: {"pet", "person"}}; core.evaluate(host)   # same person back 20 s later
assert len(played) == 3, played
print("once per visit: dog ok")

# seen continuously for minutes (polled while the pop-up is up), gone 30 s, back: no sound,
# because "gone" counts from when it left, not from when it arrived; the pop-up still opens
st, core, played = fresh(); clock.t = 21000
host.state = {0: {"person"}}; core.evaluate(host)
for k in range(1, 61):
    clock.t = 21000 + 3 * k; core.evaluate(host); core.tick()    # 3 minutes in view
clock.t = 21183; host.state = {}; core.evaluate(host)
clock.t = 21200; core.tick(); assert core.alerts[0].window is None   # closed after the linger
clock.t = 21213; host.state = {0: {"person"}}; core.evaluate(host)
assert played == ["front yard person.mp3"] and core.alerts[0].window, played
print("once per visit: long visit ok")

# plain motion: one sound per visit too, even if it flickers during the 2 s wait
st, core, played = fresh(); st.motion_alerts = True; clock.t = 22000
host.state = {3: {"md"}}; core.evaluate(host)
clock.t = 22000.5; host.state = {}; core.evaluate(host)
clock.t = 22001; host.state = {3: {"md"}}; core.evaluate(host)    # flicker inside the wait
clock.t = 22003.5; core.tick()
assert played == ["backyard motion.mp3"], played
clock.t = 22010; host.state = {}; core.evaluate(host)
clock.t = 22060; host.state = {3: {"md"}}; core.evaluate(host)    # back after 50 s: same visit
clock.t = 22063; core.tick()
assert played == ["backyard motion.mp3"], played
clock.t = 22070; host.state = {}; core.evaluate(host)
clock.t = 22200; host.state = {3: {"md"}}; core.evaluate(host)    # 130 s clear: new visit
clock.t = 22203; core.tick()
assert played == ["backyard motion.mp3"] * 2, played
print("once per visit: motion ok")

# clear_seconds = 0 turns it off: back to the 30 s repeat rule
st, core, played = fresh(); st.clear_seconds = 0; clock.t = 23000
host.state = {3: {"pet"}}; core.evaluate(host)
clock.t = 23005; host.state = {}; core.evaluate(host)
clock.t = 23020; host.state = {3: {"pet"}}; core.evaluate(host)   # 20 s: inside the 30 s repeat
clock.t = 23025; host.state = {}; core.evaluate(host)
clock.t = 23040; host.state = {3: {"pet"}}; core.evaluate(host)   # 40 s: sounds again
assert played == ["backyard animal.mp3"] * 2, played
print("once per visit off ok")
print("ALL TESTS PASSED")
