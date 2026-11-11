# Build or change it with Claude

Paste the prompt below into Claude (one that can create files works best, like the Claude desktop app or Claude Code). Fill in the "My setup" part or let Claude ask you. It builds the program, tests what it can and walks you through installing it one step at a time. Change any part of the prompt to get a different version, like other alert types, a different pop-up layout or cameras without an NVR.

Never paste your NVR password into a chat. The program asks for it with `--set-password` and keeps it in Windows Credential Manager.

```text
Build me a Windows program in Python that gives instant alerts from my Reolink NVR: a sound per camera and alert type, plus a small live video pop-up of the camera that saw something. Work with me step by step: ask the setup questions first, then build complete files (not snippets), test what you can, then walk me through installing and testing one step at a time. If you can't create files here, give me each file in full in its own code block and tell me where to save it. Explain things in plain English. Never ask me to paste my NVR password into the chat; I'll type it into the program myself.

=== MY SETUP (ask me for anything blank) ===
- NVR model and its fixed LAN IP address (reserved in my router): ___
- A login on the NVR just for this program (user name; Administrator role): ___
- Cameras to watch, named exactly as on the NVR: ___
- For each camera: pop-up + sound, or sound only? Daytime alert types (person / animal / vehicle / package / motion)? Night alert types? My night hours: ___
- The PCs that will run it, their screens (which screen gets the pop-up), and which PCs may use the sharp "Clear" feed (the NVR allows only 2 Clear viewers at once, counting phones and the Reolink Client): ___
- Sound files: will I supply one per camera and type, or should you help me make them?: ___
- No NVR (cameras only) or a Reolink Home Hub? Tell me what would change. The original was only tested with an NVR and wired PoE cameras: ___

=== WHAT IT MUST DO ===
1. Events: listen to the NVR's instant event push (Reolink "Baichuan" TCP push, port 9000). If push isn't active, poll every 5 s. While a pop-up is open, also poll every 3 s. No cloud, no Reolink app.
2. Each camera has a day list and a night list of alert types (person, animal, vehicle, package, motion). The night window comes from settings and can cross midnight (for example 22:00 to 06:00).
3. Sounds:
   - One file per camera + type, played through mpv at the file's own level (volume=100; mpv's volume curve is cubic, so 80 is about -6 dB).
   - Play mono files at full level on both speakers with --af=lavfi=[pan=stereo|FL=FL+FC|FR=FR+FC]. If mpv errors on that filter, retry without it.
   - Queue sounds so two cameras never talk over each other.
4. Once per visit:
   - A camera + type sounds when it first appears, then stays quiet while it keeps coming and going.
   - It can sound again only after that camera has seen none of that type for clear_seconds (default 120). Measure that from when the type LEFT view, not when it arrived.
   - Also never repeat the same camera + type within repeat_seconds (30).
   - Log the quiet ones, e.g. "Animal at Backyard (same visit, back after 40 s: no sound)".
   - Pop-ups are not affected by this rule.
5. Plain motion is optional: daytime only, off by default, switched from the tray.
   - Wait 2 s before its sound, and skip the sound if a person, animal or vehicle shows up in that event.
   - Apply the once-per-visit rule using how long motion had been gone when the wait began, so a flicker during the wait can't swallow the first sound.
6. Pop-up (only for cameras set to pop up):
   - A borderless, always-on-top, muted mpv window with the live feed.
   - It must never take keyboard focus: set WS_EX_NOACTIVATE / tool-window styles and give focus back to the previous foreground window. A game or full-screen video keeps running.
   - Placed in a chosen corner (default bottom-right) with a margin, on a chosen screen ("primary" or a number from --monitors).
   - Size is a % of the screen width at 16:9, shrunk to fit the screen height. Three named sizes: Normal / Large / Extra large (e.g. 40 / 60 / 80).
   - Wake a sleeping monitor when it opens. One pop-up at a time.
   - Caption: "Person · Front Yard 4:21 PM" on mpv's on-screen text (send updates over mpv's IPC pipe when a new type appears).
   - A left or right click closes it: mpv input.conf bindings that quit with a special exit code, so the program knows the user closed it and doesn't reopen it for the same event.
   - Timing: close linger_seconds (15) after the event ends, never sooner than min_seconds (10), never later than max_seconds (90). After a max-time close, wait cooldown_seconds (30) before another pop-up if the event is still going.
7. Feeds:
   - Clear = RTSP main stream; Fluent = RTSP sub stream; Balanced = the NVR's FLV "ext" stream over HTTPS (on my NVR it played with RTMP left off).
   - If the chosen feed's mpv exits within about 12 s, step down by itself: Clear > Balanced > Fluent.
8. mpv sometimes opens at the previous window's size or position. Start every mpv with --no-config. After the window appears, read its real rectangle at about 0, 0.4, 1, 2.5, 5 and 10 s and fix it with SetWindowPos. Log a warning once per pop-up.
9. Tray icon (pystray + Pillow):
   - Colored dot: green = watching, amber = snoozed, red = problem (details on hover), grey = connecting.
   - Menu: Snooze 15 / 30 / 60 min, Resume, Pop-up size (radio), Pop-up feed (radio), Motion alerts (checkbox), Test (the pop-up and each sound), Open the log, Open settings, Quit.
   - Remember the size, feed and motion choices in state.json.
   - Global hotkey Ctrl+Alt+S toggles a 30-minute snooze. Snooze = no sounds, no pop-ups, close any open pop-up, resume by itself.
   - Gotcha: pystray rebuilds the menu right after a click and reads the check marks then, so change state synchronously inside the click handler.
10. Watchdog and reconnects:
   - Check each camera's online status once a minute; warn after 2 failed checks in a row. Warn if the NVR is unreachable for 120 s, with an optional "offline" sound (off at night by default).
   - If the NVR rejects the password, stop and show that in the tray instead of retrying (repeated wrong passwords can lock the login). Retry other login problems every 5 minutes and lost connections every 30 s, forever.
   - Keep the event push alive every 60 s.
11. Single instance: a second start just exits. It runs with no console window through pyw.exe. Log to reolink_alerts.log next to the program, with passwords masked in any URL.
12. Never store the password in a file. --set-password reads it with getpass and saves it with the keyring package (Windows Credential Manager) under the key "user@nvr_address".

=== HOW TO BUILD IT ===
- Windows 10/11, Python 3.11+ (for tomllib), packages: reolink-aio, keyring, pystray, pillow. Use mpv (the shinchiro build via winget) for both video and sounds; look for it in C:\Program Files\MPV Player, or use an mpv_path setting.
- reolink-aio calls that worked for me on version 0.22.0 (check them against the installed version and adapt if names changed):
  - Connect: Host(ip, user, password, use_https=True); await host.get_host_data(). Channel info: host.channels, host.camera_name(ch), host.camera_model(ch), host.ai_supported_types(ch), host.ai_supported(ch, key), host.supported(ch, "motion_detection").
  - Push: host.baichuan.register_callback(app_id, callback, cmd_id=33); await host.baichuan.subscribe_events(); host.baichuan.events_active; await host.baichuan.check_subscribe_events() every 60 s.
  - Detection state: host.motion_detected(ch); host.ai_detected(ch, key), with keys person / vehicle / pet / package. Polling: await host.get_motion_state_all_ch().
  - Streams: await host.get_rtsp_stream_source(ch, "main" or "sub"); host.get_flv_stream_source(ch, "ext").
  - Online check: await host.get_state("GetChannelstatus"); host.camera_online(ch). Clean up with host.baichuan.unsubscribe_events() and host.logout().
- Keep all the decisions (sounds, pop-up timing, once-per-visit, snooze) in a class with no network code, so it can be unit-tested with a fake host, a fake clock and a fake process launcher. Write those tests and run them before giving me files.
- mpv pop-up options: --no-config --profile=low-latency --rtsp-transport=tcp --hwdec=auto-safe --network-timeout=8 --ontop --no-border --screen=N --geometry=WxH+X+Y --force-window=immediate --keep-open=no --idle=no --no-osc --osd-bar=no --cursor-autohide=1000 --window-dragging=no (so a click isn't read as a drag) --no-input-default-bindings --input-conf=<click bindings file> --input-ipc-server=<pipe> --title=... --osd-playing-msg=<caption> --osd-duration=6000 --osd-font-size=30 --no-terminal --aid=no
- mpv sound options: --no-config --no-video --audio-display=no --force-window=no --no-terminal --volume=100 (plus the mono filter).

=== SETTINGS FILE (settings.toml, one per PC, every line commented in plain English) ===
- [nvr] address, user, https
- [night] start, end
- [popup] monitor, size, sizes, corner, margin, stream (clear/balanced/fluent), fallback_to_sub, max_popups, linger_seconds, min_seconds, max_seconds, cooldown_seconds, mute, click_to_close, wake_screen
- [sound] enabled, volume, repeat_seconds, clear_seconds, offline (sound file), offline_at_night
- [motion] enabled, wait_seconds
- [watchdog] camera_offline_checks, nvr_offline_seconds
- [snooze] choices, hotkey, hotkey_minutes
- [program] mpv_path, tray
- [[camera]] blocks: name (matched to the NVR's camera name, ignoring case and spaces), label, popup, sound, day, night, sounds = { person = 'sound files\front yard person.mp3', ... }

Check every value on load and give a clear error that names the bad line.

=== COMMANDS ===
- --set-password
- --check: report version, Python, mpv found, day/night mode, pop-up size/feed, sound rules, NVR ports (443 or 80, 554, 9000) open or closed, login, NVR model/firmware, every channel with its AI types and online state, which camera matched which channel, whether each configured type is supported, every sound file found (MISSING if not), the screens, and whether Balanced plays (the first bytes read "FLV"; hint about RTMP if not).
- --monitors
- --test CAMERA [TYPE]: that sound + a 15 s pop-up. Options --stream clear|balanced|fluent and --size for one-off tests.
- --version
- --no-tray

=== INSTALL STEPS TO WALK ME THROUGH, ONE AT A TIME ===
1. NVR (once):
   - Reserve its IP in the router.
   - Server Settings: HTTPS and RTSP on (HTTP and ONVIF can stay off; RTMP only if Balanced won't play). Event push uses port 9000.
   - Make the dedicated Administrator user.
2. Each PC:
   - Check Windows Smart App Control. If it's on, Python's add-ons fail with "WinError 4551". It has no per-app exception, so it has to be off. Explain the trade-off before I switch it off (on some Windows versions it can't be switched back on without resetting Windows).
   - winget install Python.Python.3.13, then a NEW Terminal: py -m pip install reolink-aio keyring pystray pillow
   - winget install --id shinchiro.mpv --exact
3. Folder C:\ReolinkAlerts with: the program, settings.toml, a "sound files" folder, README.txt, Installation Instructions.txt, and a double-click launcher .pyw (a few lines using runpy, so no console window).
4. Run --set-password, then --check, then --monitors, then a --test for each camera and type, each feed and each size.
5. Auto-start: a PowerShell block that registers a Task Scheduler task with Register-ScheduledTask:
   - pyw.exe, an AtLogOn trigger with a 30 s delay, Interactive/Limited, no time limit, restart 3 times 1 minute apart.
   - Plus Start-ScheduledTask to start it now, and Unregister-ScheduledTask to remove it.
6. More PCs: copy everything except state.json and the log. Each PC saves its own password and picks its own screen, size and feed.

=== SOUND FILE TIPS (help me with these) ===
- One short clip per camera + type, e.g. "Front yard, person". Text-to-speech is fine.
- Put a short chime (about 1.3 s) in front of the voice inside each file, with the voice starting about 1.8 s in. This does three things:
  - All PCs play identical files in step, so speakers around the house don't echo each other.
  - Camera alerts sound different from other voice alerts in the house.
  - A speaker that wakes up slowly (for example an Echo on a line-in cable, which cuts the first ~0.8 s) cuts the chime instead of the first word.
- Keep the chime about 3 LU quieter than the voice and keep the files' original bitrate or higher.
- You can make a royalty-free chime with numpy (for example the public-domain Westminster opening G#4-F#4-E4-B3 on tubular-bell tones) and join it to each clip with ffmpeg. Keep my originals in a backup folder and keep the same file names, so the settings don't change.

=== DELIVERABLES ===
reolink_alerts.py (with a version number), settings.toml, README.txt, Installation Instructions.txt (every step in plain English, including updating and removing it), the .pyw launcher, and the unit tests you ran.
```
