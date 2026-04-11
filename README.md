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
4. Enable `Enter after insert` if the app should submit/send after each insertion.
5. Use `Insert` for a delayed manual insert, or enable `Timer` and click `Start Timer`.
6. Enable `Loop` to insert the selected prompt repeatedly every N minutes.
7. Enable `Random` to choose a different saved prompt for each timed insert.
8. Put the cursor in the target app before the countdown reaches zero.

If pasting does not work in a target app, switch to `Type text`. It sends keystrokes directly instead of using the clipboard.

## Click Target Fallback

If the target app loses its cursor after the first insertion, enable `Click before paste`.

- Watch `Live mouse` to see the current pointer coordinates.
- Move the mouse to the field or area that should receive the prompt and click `Use Current Mouse Position`.
- Or click `Click to Set Target`, then click the exact field or area on screen.
- Start the timer with `Loop` enabled.

Before each paste, the widget clicks that screen coordinate, then sends Ctrl+V.

Prompts are stored at:

```text
~/.local/share/prompt-injection-widget/prompts.json
```

## Notes

The app uses the clipboard plus Ctrl+V to insert text. This works best on X11 desktop sessions. Some Wayland sessions or locked-down apps may block synthetic key events.
