# Prompt Injection Widget

A small local desktop widget for storing reusable prompts and pasting the selected prompt into the currently focused application.

## Run

```bash
chmod +x run.sh
./run.sh
```

The first run creates a local `.venv` and installs `pynput`.

## Usage

1. Add prompts with `Add`.
2. Select a prompt from the list.
3. Choose `Paste with Ctrl+V` or `Type text`.
4. `Enter after send` is on by default. Disable it if the app should not submit/send after each prompt.
5. Set the timer interval and click `Start Timer`.
6. Use `Pause` to pause the timer countdown, then `Resume` to continue from the remaining time.
7. Use `Add Macro` to record clicks and keystrokes, then `Stop Recording` to save it.
8. Choose `Prompt only`, `Macro only`, or `Macro then prompt` for the timer action.
9. `Loop` is on by default. Disable it if the timer should only run once.
10. Enable `Random` to choose a different saved prompt for each timer run.
11. Put the cursor in the target app before the countdown reaches zero.

If pasting does not work in a target app, switch to `Type text`. It sends keystrokes directly instead of using the clipboard.

## Cursor Position for Prompt

If the target app loses its cursor after the first send, enable `Click before paste`.

- Watch `Live mouse` to see the current pointer coordinates.
- Click `Click to Set Target`, then click the exact field or area on screen.
- Start the timer with `Loop` enabled.

Before each paste, the widget clicks that screen coordinate, then sends Ctrl+V.

Prompts are stored at:

```text
~/.local/share/prompt-injection-widget/prompts.json
```

Macros are stored at:

```text
~/.local/share/prompt-injection-widget/macros.json
```

## Notes

The app uses the clipboard plus Ctrl+V to insert text. This works best on X11 desktop sessions. Some Wayland sessions or locked-down apps may block synthetic key events.
