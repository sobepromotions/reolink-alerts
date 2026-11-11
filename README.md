# Reolink Alerts

Instant sound and live pop-up alerts on Windows from a Reolink NVR. It talks straight to the NVR over your home network, with no cloud and no Reolink app.

When a camera's AI sees a person, animal or vehicle, Reolink Alerts:

- plays a sound for that camera and type (for example a chime, then a voice saying "Front yard, person"),
- opens a small, always-on-top live video of that camera in a corner of the screen without taking focus, so a game or a full-screen video keeps running, and closes it after the visitor leaves,
- stays quiet while the same visitor (or your dog) keeps walking in and out of view: one sound per visit,
- can be snoozed from its tray icon or with a hotkey.

Tested on a Reolink RP-PN8 NVR (firmware v3.6.5) with CX820 ColorX cameras, Windows 11, Python 3.13, [reolink-aio](https://github.com/starkillerOG/reolink_aio) 0.22.0 and [mpv](https://mpv.io). Other Reolink NVRs that work with reolink-aio should work too, but they haven't been tested.

Not affiliated with or endorsed by Reolink.

## How it works

- Alerts come from the NVR's instant event push (Reolink's "Baichuan" TCP push on port 9000, the same push [Home Assistant's Reolink integration](https://www.home-assistant.io/integrations/reolink/) uses). If the push isn't active, the program asks the NVR every 5 seconds instead (every 3 while a pop-up is open).
- Each camera has a day list and a night list of alert types: person, animal, vehicle, package and plain motion.
- The live video plays muted in a borderless [mpv](https://mpv.io) window. mpv also plays the sounds.
- The NVR password is kept in Windows Credential Manager (through the [keyring](https://pypi.org/project/keyring/) package), never in a file. Passwords in the log are masked.

## Files

| File | What it is |
|---|---|
| `reolink_alerts.py` | The program |
| `Start Reolink Alerts.pyw` | Double-click to start it with no console window |
| `settings.toml` | The settings for each PC: NVR address, cameras, sounds, pop-up size and more. Every line has a note. |
| `sound files/Westminster chime.mp3` | A short chime: the opening of the Westminster Quarters (a public-domain melody), synthesized |
| `tools/add_chime.py` | Puts that chime in front of your own voice clips |
| `tests/` | Tests of the alert logic with a pretend NVR |
| `CLAUDE_PROMPT.md` | A prompt for building or changing your own version with Claude |

The program also makes `state.json`, `reolink_alerts.log` and `popup_input.conf` next to itself. They belong to that PC and hold its addresses and history, so don't share or upload them (`.gitignore` keeps them out of git).

## What you need

- A Reolink NVR with your cameras, and a Windows 11 PC on the same network (Windows 10 should work but hasn't been tested)
- Python 3.11 or newer with the packages reolink-aio, keyring, pystray and pillow
- mpv

## Setup

### 1. On the NVR (once)

1. Give the NVR a fixed address by reserving its IP in your router.
2. In the NVR's Server Settings, turn on **HTTPS** and **RTSP**. HTTP and ONVIF can stay off. Turn on RTMP only if the Balanced feed won't play (see [Troubleshooting](#troubleshooting)).
3. Make a login just for this program with **Administrator** access: NVR Settings > System > Account Center > Add Account. This guide calls it `alerts`. (The Reolink app's "Share Device" is something else.)

### 2. On each PC

1. **Smart App Control.** Open Windows Security > App & browser control > Smart App Control settings. If it's On or Evaluation, Python's add-ons fail later with `Cannot load native module ... [WinError 4551]`. It has no exception for a single program, so it has to be Off. That's your call: on some Windows versions it can't be turned back on without resetting Windows.
2. **Python.** Open Terminal (right-click Start > Terminal) and run:
   ```
   winget install Python.Python.3.13
   ```
   Close Terminal, open a new one, and run:
   ```
   py -m pip install reolink-aio keyring pystray pillow
   ```
3. **mpv:**
   ```
   winget install --id shinchiro.mpv --exact
   ```
   It installs to `C:\Program Files\MPV Player`, where the program looks for it. Without winget, take the shinchiro build from [mpv.io](https://mpv.io/installation/) and unpack it so that `C:\mpv\mpv.exe` exists, or put the path in `mpv_path` in settings.toml.

### 3. Get the files

On this page click **Code > Download ZIP**. Unzip it and rename the folder so the program is at `C:\ReolinkAlerts\reolink_alerts.py`. (The auto-start step uses that path.)

### 4. Edit settings.toml

Open `settings.toml` in Notepad and set:

- `address` under `[nvr]`: your NVR's IP address.
- `user` under `[nvr]`: the login from step 1.
- The `[[camera]]` sections at the bottom: one per camera to watch. `name` is the camera's name on the NVR (spaces and capitals don't matter). `popup = true` gives a live pop-up and a sound; `popup = false` gives the sound only. Then the day and night alert types and a sound for each type.
- `[night]`: your night hours.

The sounds start as the included chime and built-in Windows sounds, so everything works right away. To hear a voice instead, see [Make your own alert sounds](#make-your-own-alert-sounds).

### 5. Save the NVR password (once per PC)

```
py C:\ReolinkAlerts\reolink_alerts.py --set-password
```

Type the password of the program's NVR login. Nothing shows while you type; that's normal. It goes into this PC's Windows Credential Manager.

### 6. Check and test

```
py C:\ReolinkAlerts\reolink_alerts.py --check
```

A good report shows mpv found, ports 443, 554 and 9000 open, the NVR's model, every camera on the NVR with its AI types, which `[[camera]]` matched which channel, every sound file found (none MISSING), your screens, and `Balanced ... plays`. If a camera name doesn't match, the report lists the names on your NVR. To copy the report, add ` | Set-Clipboard` to the end.

With more than one screen, pick the one for pop-ups:

```
py C:\ReolinkAlerts\reolink_alerts.py --monitors
```

and put its number in settings.toml, for example `monitor = "2"`.

Test each camera and type. A pop-up camera plays its sound and opens a 15-second pop-up; a sound-only camera plays its sound. Write the camera name without spaces:

```
py C:\ReolinkAlerts\reolink_alerts.py --test FrontYard
py C:\ReolinkAlerts\reolink_alerts.py --test FrontYard animal
py C:\ReolinkAlerts\reolink_alerts.py --test Backyard person
py C:\ReolinkAlerts\reolink_alerts.py --test FrontYard --stream balanced
py C:\ReolinkAlerts\reolink_alerts.py --test FrontYard --size large
```

`--stream` (clear, balanced or fluent) and `--size` (normal, large or extralarge) apply to that one test only.

### 7. Run it

Double-click `Start Reolink Alerts.pyw`. If Windows asks what to open it with, pick Python. A tray icon appears (click the ^ arrow by the clock if it's hidden) and turns green once it's connected. Starting it again while it's running does nothing.

- Tray icon: green = watching, amber = snoozed, red = a problem (hover over it for details), grey = connecting.
- Tray menu: Snooze 15 min / 30 min / 1 hour, Resume alerts, Pop-up size, Pop-up feed, Motion alerts (daytime), Test, Open the log, Open settings, Quit.
- Ctrl+Alt+S snoozes for 30 minutes; press it again to resume. Snoozed means no sounds and no pop-ups, and an open pop-up closes.
- Click a pop-up to close it early.
- After editing settings.toml, Quit from the tray and start it again.

To run it with the log on screen instead: `py C:\ReolinkAlerts\reolink_alerts.py`

### 8. Start automatically at sign-in

Once the tests work, paste this whole block into Terminal and press Enter. It makes a Task Scheduler task that starts the program 30 seconds after you sign in, with only the tray icon showing. Running it again replaces the task.

```powershell
& {
  $pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
  if (-not $pyw) { $pyw = "$env:LOCALAPPDATA\Programs\Python\Launcher\pyw.exe" }
  if (-not (Test-Path $pyw)) { Write-Host "pyw.exe not found. Run: where.exe pyw"; return }
  $me = "$env:USERDOMAIN\$env:USERNAME"
  $action = New-ScheduledTaskAction -Execute $pyw -Argument '"C:\ReolinkAlerts\reolink_alerts.py"' -WorkingDirectory 'C:\ReolinkAlerts'
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
  $trigger.Delay = 'PT30S'
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName 'Reolink Alerts' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Starts Reolink Alerts at sign-in' -Force | Out-Null
  Write-Host "Done. Reolink Alerts will start 30 seconds after you sign in. ($pyw)"
}
```

If it says "Access is denied", open Terminal with right-click > Run as administrator and paste it again.

- Start it now without signing out (quit any running copy first): `Start-ScheduledTask -TaskName 'Reolink Alerts'`
- Remove the task: `Unregister-ScheduledTask -TaskName 'Reolink Alerts' -Confirm:$false`

### More PCs

Copy the `C:\ReolinkAlerts` folder to the other PC, leaving out `state.json` and `reolink_alerts.log`. Then do steps 2, 5, 6 and 8 there. Each PC keeps its own password, screen, pop-up size and feed.

## Pop-up feeds and sizes

- **Clear** is the sharpest (the main stream), **Balanced** is in between (the NVR's FLV feed), and **Fluent** is the lightest (the sub stream). Pick one per PC in the tray menu; it's remembered.
- Reolink allows only 2 viewers on Clear at the same time, counting every PC, the Reolink Client and the phone app ([Reolink](https://support.reolink.com/articles/360008742233-How-Many-Online-Users-can-Live-View-the-Camera-at-the-Same-Time/)). Use Clear on 2 PCs at most and Balanced on the others.
- If a feed won't play, the pop-up steps down by itself within seconds: Clear > Balanced > Fluent.
- Normal, Large and Extra large are 40, 60 and 80% of the screen width, always 16:9. Change the numbers in `sizes` in settings.toml. On very wide screens the biggest sizes are held to the screen's height.

## Sounds

### One sound per visit

Each camera sounds once per visit for each type. While a person or your dog keeps walking in and out of view, it stays quiet. It sounds again only after that camera has seen none of that type for 2 minutes (`clear_seconds = 120` under `[sound]`; 0 turns it off). A different type still sounds right away, like a person while the dog is out. The same camera and type never repeats within 30 seconds (`repeat_seconds`). Pop-ups aren't affected. The log marks the quiet ones "(same visit, ... no sound)".

### Plain motion (optional)

Plain motion is any movement the camera reports, not just people, animals or vehicles, so it also catches shadows, rain, headlights and lightning. It's off to start; turn it on or off with tray menu > Motion alerts (daytime only). A camera uses it when `motion` is in its day list. The motion sound waits 2 seconds and is skipped if a person, animal or vehicle alert follows, so you never hear two sounds for one thing.

### Make your own alert sounds

A short voice clip per camera and type ("Front yard, person") tells you what and where without looking. Windows can make them for free with its built-in voices. Change the names and words to match your cameras, then paste this into Windows PowerShell:

```powershell
Add-Type -AssemblyName System.Speech
$clips = [ordered]@{
  'front yard person'  = 'Front yard, person'
  'front yard animal'  = 'Front yard, animal'
  'front yard vehicle' = 'Front yard, vehicle'
  'backyard person'    = 'Backyard, person'
  'backyard animal'    = 'Backyard, animal'
}
foreach ($name in $clips.Keys) {
  $voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $voice.SetOutputToWaveFile("C:\ReolinkAlerts\sound files\$name.wav")
  $voice.Speak($clips[$name])
  $voice.Dispose()
}
```

If it says the type can't be found, run it in "Windows PowerShell" instead of PowerShell 7. Then point each camera's `sounds` line at the new files, for example:

```toml
sounds = { person = 'sound files\front yard person.wav', animal = 'sound files\front yard animal.wav', vehicle = 'sound files\front yard vehicle.wav' }
```

and run `--check`, which shows MISSING for any file it can't find. Any MP3, WAV or M4A works, so your own recordings or another text-to-speech voice are fine too.

### Add the chime

Some speakers and TVs take a moment to wake up and cut off the first word. With a chime in front of the voice, they cut into the chime instead, and camera alerts sound different from other spoken alerts. To add the included chime to every clip in a folder:

```
py -m pip install numpy
winget install Gyan.FFmpeg
```

Open a new Terminal, then:

```
py C:\ReolinkAlerts\tools\add_chime.py "C:\ReolinkAlerts\sound files"
```

Each clip gets the chime first, with the voice starting 1.84 seconds in and the chime 3 LU quieter than that voice. The file names stay the same, so settings.toml doesn't change, and the originals move to `sound files\originals (no chime)`. Running it again skips files that already have the chime. `--chime`, `--voice-at` and `--chime-level` change the chime file, timing and level.

### Volume

`volume = 100` under `[sound]` plays each file at its own level, never louder than the Windows volume. A lower number turns the alert sounds down. Mono files play at full level on both speakers.

## Commands

Run them as `py C:\ReolinkAlerts\reolink_alerts.py` followed by:

| Command | What it does |
|---|---|
| `--set-password` | Saves the NVR password on this PC |
| `--check` | Tests the setup and prints a report |
| `--monitors` | Lists the screens for the `monitor` setting |
| `--test CAMERA [TYPE]` | Plays that sound and opens a 15-second pop-up. Add `--stream` or `--size` for that test only |
| `--version` | Shows the version |
| `--no-tray` | Runs without the tray icon |
| (nothing) | Runs with the log on screen |

## Updating

1. Tray > Quit.
2. Replace `reolink_alerts.py` with the new one. Keep your settings.toml unless the release notes say otherwise.
3. Start it again. The `--check` report shows the version at the top.

## Removing it

1. Tray > Quit.
2. In Terminal: `Unregister-ScheduledTask -TaskName 'Reolink Alerts' -Confirm:$false`
3. Delete the `C:\ReolinkAlerts` folder.
4. Optional, the saved password: Control Panel > Credential Manager > Windows Credentials > the entry with `reolink_alerts` in its name > Remove.
5. Optional: `winget uninstall --id shinchiro.mpv --exact`, and remove Python in Settings > Apps > Installed apps.

## Troubleshooting

| What you see | What to do |
|---|---|
| `"py" is not recognized` | Close Terminal and open a new one after installing Python. |
| `can't open file 'C:\WINDOWS\system32\reolink_alerts.py'` | Use the full path: `py C:\ReolinkAlerts\reolink_alerts.py ...` |
| `Cannot load native module ... [WinError 4551]` | Smart App Control is on (setup step 2). |
| `Put your NVR's IP address in settings.toml` | Fill in `address` under `[nvr]`. |
| "NVR rejected the password" | Run `--set-password` again, then restart the program. It doesn't keep retrying a wrong password, because repeated wrong passwords can lock the NVR login. |
| `mpv: NOT FOUND` | Find mpv.exe and put its full path in settings.toml under `[program]`: `mpv_path = 'C:\...\mpv.exe'` |
| A sound line says MISSING | The file name must match the `sounds` line in settings.toml exactly. |
| Pop-up on the wrong screen | Run `--monitors` and set `monitor`. |
| `Balanced ... NO VIDEO` | Turn on RTMP in the NVR's Server Settings (next to HTTPS and RTSP), then run `--check` again. |
| The start of a sound is cut off | The speakers are waking up. See [Add the chime](#add-the-chime). |
| It won't start after editing settings.toml | Run `--check`; it names the line with the typo. |
| Anything else | Tray > Open the log and read the last 30 lines. |

## Privacy

- Nothing leaves your network. The program talks only to your NVR.
- The password lives in Windows Credential Manager. The log masks it.
- settings.toml and the log contain your NVR's address and camera names. Before posting either one anywhere, remove the addresses.

## Build or change it with Claude

[CLAUDE_PROMPT.md](CLAUDE_PROMPT.md) has a prompt that describes this whole program. Paste it into Claude and it will ask about your setup, build a version for you and walk you through installing it. You can also ask for changes, like other alert types or a different pop-up layout.

## Tests

The tests run the alert logic against a pretend NVR. They use `tests/test_settings.toml` and a temporary folder, so they never touch your own settings or log:

```
py tests\test_core.py
py tests\test_app.py
py tests\test_cli.py
py tests\test_main_size.py
py tests\test_probe.py
```

## Credits

- [reolink-aio](https://github.com/starkillerOG/reolink_aio) by starkillerOG talks to the NVR.
- [mpv](https://mpv.io) plays the video and the sounds.
- [pystray](https://pypi.org/project/pystray/), [Pillow](https://pypi.org/project/pillow/) and [keyring](https://pypi.org/project/keyring/).
- Written with Claude.

## License

MIT. See [LICENSE](LICENSE).
