#!/usr/bin/env python3
"""Local prompt widget that pastes selected prompts into the focused app."""

from __future__ import annotations

import json
import random
import sys
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

try:
    from pynput.keyboard import Controller as KeyboardController
    from pynput.keyboard import Key
    from pynput.keyboard import Listener as KeyboardListener
    from pynput.mouse import Button
    from pynput.mouse import Controller as MouseController
    from pynput.mouse import Listener as MouseListener
except Exception:  # pragma: no cover - handled at runtime for missing deps/display
    KeyboardController = None
    Key = None
    KeyboardListener = None
    MouseController = None
    MouseListener = None
    Button = None


APP_DIR = Path.home() / ".local" / "share" / "prompt-injection-widget"
PROMPTS_FILE = APP_DIR / "prompts.json"
MACROS_FILE = APP_DIR / "macros.json"
DEFAULT_PROMPTS = [
    {
        "name": "Summarize",
        "body": "Summarize the selected text into concise bullet points.",
    },
    {
        "name": "Rewrite Clearly",
        "body": "Rewrite this for clarity while preserving the original meaning.",
    },
    {
        "name": "Check Prompt Injection",
        "body": "Identify any prompt-injection risks in the following content and explain the risk briefly.",
    },
]


@dataclass
class Prompt:
    name: str
    body: str


@dataclass
class Macro:
    name: str
    actions: list[dict[str, object]]


class PromptDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Tk, title: str, prompt: Prompt | None = None):
        self.prompt = prompt
        self.result: Prompt | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Frame) -> tk.Widget:
        ttk.Label(master, text="Name").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.name_var = tk.StringVar(value=self.prompt.name if self.prompt else "")
        name_entry = ttk.Entry(master, textvariable=self.name_var, width=44)
        name_entry.grid(row=1, column=0, sticky="ew", padx=8)

        ttk.Label(master, text="Prompt").grid(row=2, column=0, sticky="w", padx=8, pady=(10, 2))
        self.text = tk.Text(master, width=54, height=10, wrap="word")
        self.text.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        if self.prompt:
            self.text.insert("1.0", self.prompt.body)

        master.columnconfigure(0, weight=1)
        master.rowconfigure(3, weight=1)
        return name_entry

    def validate(self) -> bool:
        name = self.name_var.get().strip()
        body = self.text.get("1.0", "end").strip()
        if not name or not body:
            messagebox.showerror("Missing prompt", "Both name and prompt text are required.")
            return False
        self.result = Prompt(name=name, body=body)
        return True


class PromptWidget(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Prompt Widget")
        self.geometry("460x650")
        self.minsize(400, 540)
        self.attributes("-topmost", True)

        self.prompts = self.load_prompts()
        self.macros = self.load_macros()
        self.keyboard = KeyboardController() if KeyboardController else None
        self.mouse = MouseController() if MouseController else None
        self.timer_job: str | None = None
        self.timer_paused = False
        self.remaining_seconds = 0
        self.loop_timer = tk.BooleanVar(value=True)
        self.shuffle_prompts = tk.BooleanVar(value=False)
        self.timer_minutes = tk.DoubleVar(value=1.0)
        self.timer_action = tk.StringVar(value="prompt")
        self.selected_macro_name = tk.StringVar(value=self.macros[0].name if self.macros else "")
        self.insert_method = tk.StringVar(value="paste")
        self.press_enter_after_insert = tk.BooleanVar(value=True)
        self.use_click_target = tk.BooleanVar(value=True)
        self.click_x = tk.IntVar(value=420)
        self.click_y = tk.IntVar(value=2000)
        self.mouse_position_var = tk.StringVar(value="Mouse: unavailable")
        self.capture_listener = None
        self.recording_macro = False
        self.recorded_actions: list[dict[str, object]] = []
        self.last_macro_event_at = 0.0
        self.macro_mouse_listener = None
        self.macro_keyboard_listener = None
        self.status_var = tk.StringVar(value="Select a prompt, then start the timer.")

        self.build_ui()
        self.refresh_list()
        self.update_mouse_position()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self) -> None:
        main = ttk.Frame(self, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        ttk.Label(main, text="Prompts").grid(row=0, column=0, columnspan=4, sticky="w")
        self.listbox = tk.Listbox(main, height=10, exportselection=False)
        self.listbox.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(4, 8))
        self.listbox.bind("<<ListboxSelect>>", lambda _event: self.update_preview())

        self.preview = tk.Text(main, height=8, wrap="word", state="disabled")
        self.preview.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(0, 8))

        ttk.Button(main, text="Add", command=self.add_prompt).grid(row=3, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(main, text="Edit", command=self.edit_prompt).grid(row=3, column=1, sticky="ew", padx=4)
        ttk.Button(main, text="Remove", command=self.remove_prompt).grid(row=3, column=2, columnspan=2, sticky="ew", padx=(4, 0))

        insertion = ttk.LabelFrame(main, text="Timer Delivery", padding=8)
        insertion.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 8))
        ttk.Radiobutton(insertion, text="Paste with Ctrl+V", variable=self.insert_method, value="paste").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(insertion, text="Type text", variable=self.insert_method, value="type").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Checkbutton(insertion, text="Enter after send", variable=self.press_enter_after_insert).grid(row=0, column=2, columnspan=2, sticky="w", padx=(12, 0))

        timer = ttk.LabelFrame(main, text="Timer", padding=8)
        timer.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Label(timer, text="Minutes").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(timer, from_=0.1, to=1440, increment=0.1, textvariable=self.timer_minutes, width=6).grid(row=0, column=1, sticky="w", padx=(4, 0))
        ttk.Checkbutton(timer, text="Loop", variable=self.loop_timer).grid(row=0, column=3, sticky="w", padx=(12, 0))
        ttk.Checkbutton(timer, text="Random", variable=self.shuffle_prompts).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(timer, text="Start Timer", command=self.start_timer).grid(row=1, column=1, sticky="ew", pady=(8, 0), padx=4)
        self.pause_timer_button = ttk.Button(timer, text="Pause", command=self.toggle_timer_pause)
        self.pause_timer_button.grid(row=1, column=2, sticky="ew", pady=(8, 0), padx=4)
        ttk.Button(timer, text="Stop", command=self.stop_timer).grid(row=1, column=3, sticky="ew", pady=(8, 0), padx=(4, 0))

        macro = ttk.LabelFrame(main, text="Macro", padding=8)
        macro.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Radiobutton(macro, text="Prompt only", variable=self.timer_action, value="prompt").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(macro, text="Macro only", variable=self.timer_action, value="macro").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Radiobutton(macro, text="Macro then prompt", variable=self.timer_action, value="macro_prompt").grid(row=0, column=2, columnspan=2, sticky="w", padx=(8, 0))
        ttk.Label(macro, text="Saved macro").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.macro_combo = ttk.Combobox(macro, textvariable=self.selected_macro_name, values=self.macro_names(), state="readonly")
        self.macro_combo.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0), padx=(8, 0))
        self.add_macro_button = ttk.Button(macro, text="Add Macro", command=self.start_macro_recording)
        self.add_macro_button.grid(row=2, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.stop_macro_button = ttk.Button(macro, text="Stop Recording", command=self.stop_macro_recording, state="disabled")
        self.stop_macro_button.grid(row=2, column=1, sticky="ew", pady=(8, 0), padx=4)
        ttk.Button(macro, text="Delete Macro", command=self.delete_selected_macro).grid(row=2, column=2, columnspan=2, sticky="ew", pady=(8, 0), padx=(4, 0))
        for col in range(4):
            macro.columnconfigure(col, weight=1)

        click = ttk.LabelFrame(main, text="Cursor Position for Prompt", padding=8)
        click.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(click, text="Click before paste", variable=self.use_click_target).grid(row=0, column=0, sticky="w")
        ttk.Label(click, text="X").grid(row=0, column=1, sticky="e", padx=(12, 4))
        ttk.Spinbox(click, from_=0, to=10000, textvariable=self.click_x, width=6).grid(row=0, column=2, sticky="w")
        ttk.Label(click, text="Y").grid(row=0, column=3, sticky="e", padx=(12, 4))
        ttk.Spinbox(click, from_=0, to=10000, textvariable=self.click_y, width=6).grid(row=0, column=4, sticky="w")
        ttk.Button(click, text="Click to Set Target", command=self.arm_click_capture).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        ttk.Label(click, textvariable=self.mouse_position_var).grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))

        ttk.Label(main, textvariable=self.status_var, wraplength=420).grid(row=8, column=0, columnspan=4, sticky="ew")

        for col in range(4):
            main.columnconfigure(col, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(2, weight=1)

    def load_prompts(self) -> list[Prompt]:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if not PROMPTS_FILE.exists():
            prompts = [Prompt(**item) for item in DEFAULT_PROMPTS]
            self.save_prompts(prompts)
            return prompts

        try:
            data = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
            return [Prompt(name=item["name"], body=item["body"]) for item in data]
        except Exception as exc:
            messagebox.showwarning("Prompt load failed", f"Using defaults because prompts could not be loaded:\n{exc}")
            return [Prompt(**item) for item in DEFAULT_PROMPTS]

    def save_prompts(self, prompts: list[Prompt] | None = None) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        active_prompts = prompts if prompts is not None else self.prompts
        PROMPTS_FILE.write_text(
            json.dumps([asdict(prompt) for prompt in active_prompts], indent=2),
            encoding="utf-8",
        )

    def load_macros(self) -> list[Macro]:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if not MACROS_FILE.exists():
            return []

        try:
            data = json.loads(MACROS_FILE.read_text(encoding="utf-8"))
            return [Macro(name=item["name"], actions=item["actions"]) for item in data]
        except Exception as exc:
            messagebox.showwarning("Macro load failed", f"Macros could not be loaded:\n{exc}")
            return []

    def save_macros(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        MACROS_FILE.write_text(
            json.dumps([asdict(macro) for macro in self.macros], indent=2),
            encoding="utf-8",
        )

    def macro_names(self) -> list[str]:
        return [macro.name for macro in self.macros]

    def refresh_macro_options(self) -> None:
        names = self.macro_names()
        self.macro_combo.configure(values=names)
        if not names:
            self.selected_macro_name.set("")
            return
        if self.selected_macro_name.get() not in names:
            self.selected_macro_name.set(names[0])

    def selected_macro(self) -> Macro | None:
        selected_name = self.selected_macro_name.get()
        for macro in self.macros:
            if macro.name == selected_name:
                return macro
        return None

    def refresh_list(self) -> None:
        selected = self.current_index()
        self.listbox.delete(0, "end")
        for prompt in self.prompts:
            self.listbox.insert("end", prompt.name)
        if self.prompts:
            next_index = selected if selected is not None and selected < len(self.prompts) else 0
            self.listbox.selection_set(next_index)
            self.listbox.activate(next_index)
        self.update_preview()

    def current_index(self) -> int | None:
        selection = self.listbox.curselection() if hasattr(self, "listbox") else ()
        return selection[0] if selection else None

    def selected_prompt(self) -> Prompt | None:
        index = self.current_index()
        if index is None:
            return None
        return self.prompts[index]

    def update_preview(self) -> None:
        prompt = self.selected_prompt()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if prompt:
            self.preview.insert("1.0", prompt.body)
        self.preview.configure(state="disabled")

    def add_prompt(self) -> None:
        dialog = PromptDialog(self, "Add Prompt")
        if dialog.result:
            self.prompts.append(dialog.result)
            self.save_prompts()
            self.refresh_list()

    def edit_prompt(self) -> None:
        index = self.current_index()
        if index is None:
            messagebox.showinfo("No selection", "Select a prompt to edit.")
            return
        dialog = PromptDialog(self, "Edit Prompt", self.prompts[index])
        if dialog.result:
            self.prompts[index] = dialog.result
            self.save_prompts()
            self.refresh_list()

    def remove_prompt(self) -> None:
        index = self.current_index()
        if index is None:
            messagebox.showinfo("No selection", "Select a prompt to remove.")
            return
        prompt = self.prompts[index]
        if messagebox.askyesno("Remove prompt", f"Remove '{prompt.name}'?"):
            del self.prompts[index]
            self.save_prompts()
            self.refresh_list()

    def capture_mouse_position(self) -> None:
        if not self.mouse:
            messagebox.showerror("Mouse control unavailable", "Mouse control requires pynput. Run ./run.sh to install dependencies.")
            return
        x, y = self.mouse.position
        self.click_x.set(int(x))
        self.click_y.set(int(y))
        self.use_click_target.set(True)
        self.status_var.set(f"Click target set to ({int(x)}, {int(y)}).")

    def arm_click_capture(self) -> None:
        if not MouseListener:
            messagebox.showerror("Mouse listener unavailable", "Click capture requires pynput. Run ./run.sh to install dependencies.")
            return
        if self.capture_listener:
            self.capture_listener.stop()
            self.capture_listener = None

        self.status_var.set("Click anywhere on the screen to set the paste target.")
        self.capture_listener = MouseListener(on_click=self.handle_target_click)
        self.capture_listener.start()

    def handle_target_click(self, x: int, y: int, _button: Button, pressed: bool) -> bool:
        if not pressed:
            return True
        self.after(0, lambda: self.set_click_target(int(x), int(y)))
        return False

    def set_click_target(self, x: int, y: int) -> None:
        self.click_x.set(x)
        self.click_y.set(y)
        self.use_click_target.set(True)
        self.capture_listener = None
        self.status_var.set(f"Click target set to ({x}, {y}).")

    def update_mouse_position(self) -> None:
        if self.mouse:
            x, y = self.mouse.position
            self.mouse_position_var.set(f"Live mouse: ({int(x)}, {int(y)})")
        self.after(150, self.update_mouse_position)

    def start_macro_recording(self) -> None:
        if not MouseListener or not KeyboardListener:
            messagebox.showerror("Macro recording unavailable", "Macro recording requires pynput. Run ./run.sh to install dependencies.")
            return
        if self.recording_macro:
            return
        if self.capture_listener:
            self.capture_listener.stop()
            self.capture_listener = None

        self.recording_macro = True
        self.recorded_actions = []
        self.last_macro_event_at = time.monotonic()
        self.add_macro_button.configure(state="disabled")
        self.stop_macro_button.configure(state="normal")
        self.status_var.set("Recording macro. Perform clicks and keystrokes, then click Stop Recording.")
        self.macro_mouse_listener = MouseListener(on_click=self.handle_macro_click)
        self.macro_keyboard_listener = KeyboardListener(on_press=self.handle_macro_key_press, on_release=self.handle_macro_key_release)
        self.macro_mouse_listener.start()
        self.macro_keyboard_listener.start()

    def stop_macro_recording(self) -> None:
        if not self.recording_macro:
            return

        self.recording_macro = False
        if self.macro_mouse_listener:
            self.macro_mouse_listener.stop()
            self.macro_mouse_listener = None
        if self.macro_keyboard_listener:
            self.macro_keyboard_listener.stop()
            self.macro_keyboard_listener = None
        self.add_macro_button.configure(state="normal")
        self.stop_macro_button.configure(state="disabled")

        if not self.recorded_actions:
            self.status_var.set("Macro recording discarded; no actions were captured.")
            return

        name = simpledialog.askstring("Save Macro", "Macro name:", parent=self)
        if not name:
            self.status_var.set("Macro recording discarded.")
            return

        macro = Macro(name=self.unique_macro_name(name.strip()), actions=self.recorded_actions)
        self.macros.append(macro)
        self.selected_macro_name.set(macro.name)
        self.save_macros()
        self.refresh_macro_options()
        self.status_var.set(f"Saved macro '{macro.name}' with {len(macro.actions)} actions.")

    def unique_macro_name(self, name: str) -> str:
        base_name = name or "Macro"
        existing_names = set(self.macro_names())
        if base_name not in existing_names:
            return base_name

        suffix = 2
        while f"{base_name} {suffix}" in existing_names:
            suffix += 1
        return f"{base_name} {suffix}"

    def delete_selected_macro(self) -> None:
        macro = self.selected_macro()
        if not macro:
            messagebox.showinfo("No macro", "Select a saved macro to delete.")
            return
        if not messagebox.askyesno("Delete macro", f"Delete '{macro.name}'?"):
            return

        self.macros = [saved_macro for saved_macro in self.macros if saved_macro.name != macro.name]
        self.save_macros()
        self.refresh_macro_options()
        self.status_var.set(f"Deleted macro '{macro.name}'.")

    def handle_macro_click(self, x: int, y: int, button: Button, pressed: bool) -> None:
        if not self.recording_macro or self.point_inside_widget(int(x), int(y)):
            return
        self.record_macro_action(
            {
                "kind": "mouse_click",
                "x": int(x),
                "y": int(y),
                "button": self.button_to_name(button),
                "pressed": bool(pressed),
            }
        )

    def handle_macro_key_press(self, key) -> None:
        if not self.recording_macro:
            return
        self.record_macro_action({"kind": "key_press", "key": self.key_to_spec(key)})

    def handle_macro_key_release(self, key) -> None:
        if not self.recording_macro:
            return
        self.record_macro_action({"kind": "key_release", "key": self.key_to_spec(key)})

    def record_macro_action(self, action: dict[str, object]) -> None:
        now = time.monotonic()
        action["delay"] = min(30.0, max(0.0, now - self.last_macro_event_at))
        self.last_macro_event_at = now
        self.recorded_actions.append(action)

    def point_inside_widget(self, x: int, y: int) -> bool:
        left = self.winfo_rootx()
        top = self.winfo_rooty()
        return left <= x <= left + self.winfo_width() and top <= y <= top + self.winfo_height()

    def key_to_spec(self, key) -> str:
        if hasattr(key, "char") and key.char is not None:
            return f"char:{key.char}"
        return f"key:{getattr(key, 'name', str(key).replace('Key.', ''))}"

    def spec_to_key(self, spec: str):
        if spec.startswith("char:"):
            return spec.removeprefix("char:")
        key_name = spec.removeprefix("key:")
        return getattr(Key, key_name)

    def button_to_name(self, button: Button) -> str:
        return getattr(button, "name", str(button).replace("Button.", ""))

    def name_to_button(self, name: str):
        return getattr(Button, name)

    def start_timer(self) -> None:
        if self.timer_action_uses_prompt() and not self.prompts:
            messagebox.showinfo("No prompts", "Add at least one prompt before starting the timer.")
            return
        if self.timer_action_uses_prompt() and not self.shuffle_prompts.get() and not self.selected_prompt():
            messagebox.showinfo("No selection", "Select a prompt before starting the timer, or enable Random.")
            return
        if self.timer_action_uses_macro() and not self.selected_macro():
            messagebox.showinfo("No macro", "Record or select a macro before starting the timer.")
            return
        self.stop_timer(clear_status=False)
        self.timer_paused = False
        self.pause_timer_button.configure(text="Pause")
        self.remaining_seconds = self.timer_interval_seconds()
        self.status_var.set(f"Timer running: {self.format_remaining_time()}. Place the cursor in the target app.")
        self.tick_timer()

    def tick_timer(self) -> None:
        if self.timer_paused:
            self.timer_job = None
            self.status_var.set(f"Timer paused: {self.format_remaining_time()} remaining.")
            return

        if self.remaining_seconds <= 0:
            self.schedule_timer_action(delay_ms=100, hide_widget=False)
            if self.loop_timer.get():
                self.remaining_seconds = self.timer_interval_seconds() - 1
                self.timer_job = self.after(1000, self.tick_timer)
            else:
                self.timer_job = None
            return

        mode = "Random loop" if self.loop_timer.get() and self.shuffle_prompts.get() else "Loop" if self.loop_timer.get() else "Timer"
        self.status_var.set(f"{mode} running: {self.format_remaining_time()}. Place the cursor in the target app.")
        self.remaining_seconds -= 1
        self.timer_job = self.after(1000, self.tick_timer)

    def toggle_timer_pause(self) -> None:
        if self.timer_paused:
            self.timer_paused = False
            self.pause_timer_button.configure(text="Pause")
            self.status_var.set(f"Timer running: {self.format_remaining_time()}. Place the cursor in the target app.")
            self.tick_timer()
            return

        if not self.timer_job or self.remaining_seconds <= 0:
            self.status_var.set("No timer is running.")
            return

        self.after_cancel(self.timer_job)
        self.timer_job = None
        self.timer_paused = True
        self.pause_timer_button.configure(text="Resume")
        self.status_var.set(f"Timer paused: {self.format_remaining_time()} remaining.")

    def timer_interval_seconds(self) -> int:
        return max(1, round(float(self.timer_minutes.get()) * 60))

    def format_remaining_time(self) -> str:
        minutes, seconds = divmod(max(0, self.remaining_seconds), 60)
        if minutes and seconds:
            return f"{minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m"
        return f"{seconds}s"

    def stop_timer(self, clear_status: bool = True) -> None:
        if self.timer_job:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        self.timer_paused = False
        self.pause_timer_button.configure(text="Pause")
        if clear_status:
            self.status_var.set("Timer stopped.")

    def timer_action_uses_prompt(self) -> bool:
        return self.timer_action.get() in {"prompt", "macro_prompt"}

    def timer_action_uses_macro(self) -> bool:
        return self.timer_action.get() in {"macro", "macro_prompt"}

    def schedule_timer_action(self, delay_ms: int, hide_widget: bool) -> None:
        prompt = self.prompt_for_paste(hide_widget=hide_widget) if self.timer_action_uses_prompt() else None
        macro = self.selected_macro() if self.timer_action_uses_macro() else None
        if self.timer_action_uses_prompt() and not prompt:
            messagebox.showinfo("No selection", "Select a prompt first.")
            return
        if self.timer_action_uses_macro() and not macro:
            messagebox.showinfo("No macro", "Select a saved macro first.")
            return
        if self.timer_action_uses_prompt() and (not self.keyboard or not Key):
            self.copy_to_clipboard(prompt.body)
            messagebox.showerror(
                "Keyboard control unavailable",
                "Prompt copied to clipboard, but automatic paste requires pynput. Run ./run.sh to install dependencies.",
            )
            return
        if self.timer_action_uses_macro() and not self.can_play_macro(macro):
            return

        action_description = self.timer_action_description()
        self.status_var.set(f"Timer finished. {action_description}...")
        if hide_widget:
            self.withdraw()
        self.after(
            delay_ms,
            lambda: self.run_timer_action(
                text=prompt.body if prompt else None,
                macro=macro,
                restore_widget=hide_widget,
            ),
        )

    def timer_action_description(self) -> str:
        if self.timer_action.get() == "macro":
            return "Playing macro"
        if self.timer_action.get() == "macro_prompt":
            return "Playing macro, then sending prompt"
        action = "typing" if self.insert_method.get() == "type" else "pasting"
        return f"{action.capitalize()} prompt"

    def can_play_macro(self, macro: Macro | None) -> bool:
        if not macro:
            return False
        needs_mouse = any(action["kind"] == "mouse_click" for action in macro.actions)
        needs_keyboard = any(str(action["kind"]).startswith("key_") for action in macro.actions)
        if needs_mouse and (not self.mouse or not Button):
            messagebox.showerror("Mouse control unavailable", "Macro playback needs mouse control. Run ./run.sh to install dependencies.")
            return False
        if needs_keyboard and (not self.keyboard or not Key):
            messagebox.showerror("Keyboard control unavailable", "Macro playback needs keyboard control. Run ./run.sh to install dependencies.")
            return False
        return True

    def prompt_for_paste(self, hide_widget: bool) -> Prompt | None:
        if not hide_widget and self.shuffle_prompts.get() and self.prompts:
            prompt = random.choice(self.prompts)
            self.select_prompt(prompt)
            return prompt
        return self.selected_prompt()

    def select_prompt(self, prompt: Prompt) -> None:
        index = self.prompts.index(prompt)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)
        self.update_preview()

    def run_timer_action(self, text: str | None, macro: Macro | None, restore_widget: bool) -> None:
        try:
            if macro:
                self.play_macro(macro)
            if text is not None:
                self.copy_to_clipboard(text)
                self.send_prompt_text(text)
                self.status_var.set("Sent selected prompt.")
            elif macro:
                self.status_var.set(f"Played macro '{macro.name}'.")
        except Exception as exc:
            self.status_var.set("Timer action failed.")
            messagebox.showerror("Timer action failed", str(exc))
        finally:
            if restore_widget:
                self.after(250, self.deiconify)

    def play_macro(self, macro: Macro) -> None:
        for action in macro.actions:
            time.sleep(float(action.get("delay", 0.0)))
            kind = action["kind"]
            if kind == "mouse_click":
                self.mouse.position = (int(action["x"]), int(action["y"]))
                button = self.name_to_button(str(action["button"]))
                if action.get("pressed"):
                    self.mouse.press(button)
                else:
                    self.mouse.release(button)
            elif kind == "key_press":
                self.keyboard.press(self.spec_to_key(str(action["key"])))
            elif kind == "key_release":
                self.keyboard.release(self.spec_to_key(str(action["key"])))

    def send_prompt_text(self, text: str) -> None:
        if self.use_click_target.get() and self.mouse and Button:
            self.mouse.position = (int(self.click_x.get()), int(self.click_y.get()))
            self.mouse.click(Button.left, 1)
            time.sleep(0.08)
        if self.insert_method.get() == "type":
            self.keyboard.type(text)
        else:
            self.keyboard.press(Key.ctrl)
            self.keyboard.press("v")
            self.keyboard.release("v")
            self.keyboard.release(Key.ctrl)
        if self.press_enter_after_insert.get():
            time.sleep(0.05)
            self.keyboard.press(Key.enter)
            self.keyboard.release(Key.enter)

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def on_close(self) -> None:
        self.stop_timer(clear_status=False)
        if self.capture_listener:
            self.capture_listener.stop()
            self.capture_listener = None
        if self.macro_mouse_listener:
            self.macro_mouse_listener.stop()
            self.macro_mouse_listener = None
        if self.macro_keyboard_listener:
            self.macro_keyboard_listener.stop()
            self.macro_keyboard_listener = None
        self.destroy()


def main() -> int:
    app = PromptWidget()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
