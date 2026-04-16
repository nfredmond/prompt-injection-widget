# Prompt Widget

A small local desktop widget for storing reusable prompts and feeding the
selected one into whatever app currently has focus. Runs a timer that can
paste a prompt, play a recorded macro, or both — on a loop or one-shot.

Built for Linux (X11). Single Python file, one dependency (`pynput`).

## Run

```bash
chmod +x run.sh
./run.sh
```

The first run creates a local `.venv` and installs `pynput` quietly. Subsequent
runs skip pip entirely unless `requirements.txt` or the system Python version
changed — startup is silent and instant.

## Window layout

The header shows a big `MM:SS` countdown and the main timer buttons (Start,
Pause/Resume, Stop, Send Now). Everything else lives in four tabs:

- **Prompts** — manage the prompt list, preview, drag-to-reorder, search
- **Timer** — interval input (`mm:ss` or decimal minutes), preset chips, action
  mode, Loop/Random/Enter-after/Bell, click-target capture
- **Macros** — record, preview actions, rename, or delete
- **Settings** — theme, always-on-top, bell-on-fire, global hotkey, undo, import/export

A tone-coded status bar sits at the bottom (neutral / running / paused / error).
The window title mirrors state: `Prompt Widget — 4:23`, `— paused 4:23`, or
`— recording` while a macro is being captured.

## Typical flow

1. Add prompts with `Add` (or `Ctrl+N`).
2. Pick one from the list. Use the search box (`Ctrl+F`) to filter by name.
3. Choose `Paste with Ctrl+V` or `Type text`. Paste is faster but some apps
   block synthetic clipboard events — fall back to `Type text` if paste fails.
4. `Enter after send` is on by default. Disable it if the target app should
   not auto-submit after each prompt.
5. Set the timer interval and hit `Start Timer`. Presets (1m / 5m / 7m / 15m
   / 30m) are one click. Manual entry accepts `7`, `7.5`, or `7:30`.
6. Pick a timer action: `Prompt only`, `Macro only`, or `Macro then prompt`.
7. `Loop` runs the action repeatedly; disable for one-shot. `Random` picks a
   different saved prompt each tick (no repeats within a cycle).
8. Put the cursor in the target app before the countdown reaches zero — or
   use `Click before paste` (see below).
9. `Send Now` (or `Ctrl+Enter`) fires the configured action immediately
   without waiting on the countdown.

The widget stays on top by default. Toggle this off in Settings if you want
other windows to cover it.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+N` | New prompt |
| `Ctrl+E` | Edit selected prompt |
| `Ctrl+D` | Duplicate selected prompt |
| `Delete` | Remove selected prompt |
| `Ctrl+F` | Focus prompt search |
| `Ctrl+Z` | Undo last prompt/macro delete (stack of 20) |
| `Ctrl+Enter` | Send Now |
| `Alt+Up` / `Alt+Down` | Reorder selected prompt |
| `Space` | Toggle pause (when prompt list has focus) |
| `Escape` | Stop timer, or clear search when search box has focus |

Shortcuts are mode-gated: they never fire while the focus is inside a text
entry or the preview pane.

## Global hotkey

A system-wide pause/resume hotkey binds even when the widget isn't focused.
Default is `<ctrl>+<shift>+<f12>`; change it in Settings or clear the field
to disable. The combo uses `pynput` syntax (e.g. `<alt>+<f9>`).

## Macros

1. Go to the Macros tab, click `Add Macro`, and give it a name.
2. Recording starts immediately — perform clicks and keystrokes in other
   apps (F8 stops recording early without touching the widget).
3. Select the macro to see the first few actions and the total count.
4. Rename or delete as needed.

Macros run in a background thread so the UI and countdown stay responsive
even during long playback.

## Cursor Position for Prompt

If the target app loses its cursor after the first send, enable
`Click before paste` on the Timer tab.

- `Live mouse` shows the current pointer coordinates (throttled; only active
  on the Timer tab).
- Click `Click to Set Target`, then click the exact field on screen.
- Start the timer with `Loop` enabled.

Before each paste, the widget clicks that coordinate, then injects Ctrl+V.

## Safe-abort and clipboard hygiene

- If any modifier (Ctrl / Shift / Alt) is held when the timer fires, the
  tick is skipped and the status bar notes it. Prevents firing into a
  password field or a keyboard shortcut you're typing elsewhere.
- The paste path saves and restores your clipboard so a tick doesn't
  permanently clobber whatever you copied.

## Import / Export / Undo

Settings tab has buttons to export prompts or macros to JSON, and to import
them back. Imports merge, auto-deduping names. `Ctrl+Z` undoes the most
recent prompt or macro delete (stack of 20, cleared at shutdown).

## Data files

```text
~/.local/share/prompt-injection-widget/prompts.json   # prompt list
~/.local/share/prompt-injection-widget/macros.json    # recorded macros
~/.local/share/prompt-injection-widget/settings.json  # UI preferences
~/.local/share/prompt-injection-widget/widget.log     # rotating logs
```

## Desktop launcher

An `.desktop` file ships at the repo root. Install with:

```bash
cp prompt-widget.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

The launcher uses absolute paths inside this repo; edit them if you move
the checkout.

## Notes and caveats

- **Wayland:** `pynput`'s mouse-position query and synthetic Ctrl+V injection
  tend to fail silently on Wayland. The widget detects Wayland sessions and
  shows a one-time advisory. Switch to an X11 session for reliable macros
  and click-targets.
- **Locked-down apps** (some Electron builds, Steam overlays, remote desktop
  clients) may ignore synthetic key events regardless of session type. Use
  `Type text` instead of `Paste with Ctrl+V`, or record a macro that clicks
  through the app's own UI.
