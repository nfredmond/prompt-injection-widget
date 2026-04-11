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
    from pynput.mouse import Button
    from pynput.mouse import Controller as MouseController
    from pynput.mouse import Listener as MouseListener
except Exception:  # pragma: no cover - handled at runtime for missing deps/display
    KeyboardController = None
    Key = None
    MouseController = None
    MouseListener = None
    Button = None


APP_DIR = Path.home() / ".local" / "share" / "prompt-injection-widget"
PROMPTS_FILE = APP_DIR / "prompts.json"
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
        self.geometry("420x520")
        self.minsize(360, 420)
        self.attributes("-topmost", True)

        self.prompts = self.load_prompts()
        self.keyboard = KeyboardController() if KeyboardController else None
        self.mouse = MouseController() if MouseController else None
        self.timer_job: str | None = None
        self.remaining_seconds = 0
        self.loop_timer = tk.BooleanVar(value=False)
        self.shuffle_prompts = tk.BooleanVar(value=False)
        self.timer_enabled = tk.BooleanVar(value=False)
        self.timer_minutes = tk.DoubleVar(value=1.0)
        self.manual_delay_seconds = tk.IntVar(value=3)
        self.insert_method = tk.StringVar(value="paste")
        self.press_enter_after_insert = tk.BooleanVar(value=False)
        self.use_click_target = tk.BooleanVar(value=False)
        self.click_x = tk.IntVar(value=0)
        self.click_y = tk.IntVar(value=0)
        self.mouse_position_var = tk.StringVar(value="Mouse: unavailable")
        self.capture_listener = None
        self.status_var = tk.StringVar(value="Select a prompt, then insert or start the timer.")

        self.build_ui()
        self.refresh_list()
        self.update_mouse_position()

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
        ttk.Button(main, text="Remove", command=self.remove_prompt).grid(row=3, column=2, sticky="ew", padx=4)
        ttk.Button(main, text="Insert", command=self.insert_selected).grid(row=3, column=3, sticky="ew", padx=(4, 0))

        insertion = ttk.LabelFrame(main, text="Insertion", padding=8)
        insertion.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 8))
        ttk.Radiobutton(insertion, text="Paste with Ctrl+V", variable=self.insert_method, value="paste").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(insertion, text="Type text", variable=self.insert_method, value="type").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Label(insertion, text="Manual delay").grid(row=0, column=2, sticky="e", padx=(12, 4))
        ttk.Spinbox(insertion, from_=1, to=30, textvariable=self.manual_delay_seconds, width=5).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(insertion, text="Enter after insert", variable=self.press_enter_after_insert).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        timer = ttk.LabelFrame(main, text="Timer", padding=8)
        timer.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(timer, text="Enable timer", variable=self.timer_enabled).grid(row=0, column=0, sticky="w")
        ttk.Label(timer, text="Minutes").grid(row=0, column=1, sticky="e", padx=(12, 4))
        ttk.Spinbox(timer, from_=0.1, to=1440, increment=0.1, textvariable=self.timer_minutes, width=6).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(timer, text="Loop", variable=self.loop_timer).grid(row=0, column=3, sticky="w", padx=(12, 0))
        ttk.Checkbutton(timer, text="Random", variable=self.shuffle_prompts).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(timer, text="Start Timer", command=self.start_timer).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0), padx=4)
        ttk.Button(timer, text="Stop", command=self.stop_timer).grid(row=1, column=3, sticky="ew", pady=(8, 0), padx=(4, 0))

        click = ttk.LabelFrame(main, text="Click Target Fallback", padding=8)
        click.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(click, text="Click before paste", variable=self.use_click_target).grid(row=0, column=0, sticky="w")
        ttk.Label(click, text="X").grid(row=0, column=1, sticky="e", padx=(12, 4))
        ttk.Spinbox(click, from_=0, to=10000, textvariable=self.click_x, width=6).grid(row=0, column=2, sticky="w")
        ttk.Label(click, text="Y").grid(row=0, column=3, sticky="e", padx=(12, 4))
        ttk.Spinbox(click, from_=0, to=10000, textvariable=self.click_y, width=6).grid(row=0, column=4, sticky="w")
        ttk.Button(click, text="Use Current Mouse Position", command=self.capture_mouse_position).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0), padx=(0, 4))
        ttk.Button(click, text="Click to Set Target", command=self.arm_click_capture).grid(row=1, column=2, columnspan=3, sticky="ew", pady=(8, 0), padx=(4, 0))
        ttk.Label(click, textvariable=self.mouse_position_var).grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))

        ttk.Label(main, textvariable=self.status_var, wraplength=380).grid(row=7, column=0, columnspan=4, sticky="ew")

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

    def insert_selected(self) -> None:
        if self.timer_enabled.get():
            self.start_timer()
            return
        self.schedule_paste(delay_ms=max(1, int(self.manual_delay_seconds.get())) * 1000, hide_widget=True)

    def start_timer(self) -> None:
        if not self.prompts:
            messagebox.showinfo("No prompts", "Add at least one prompt before starting the timer.")
            return
        if not self.shuffle_prompts.get() and not self.selected_prompt():
            messagebox.showinfo("No selection", "Select a prompt before starting the timer, or enable Random.")
            return
        self.stop_timer(clear_status=False)
        self.remaining_seconds = self.timer_interval_seconds()
        self.status_var.set(f"Timer running: {self.format_remaining_time()}. Place the cursor in the target app.")
        self.tick_timer()

    def tick_timer(self) -> None:
        if self.remaining_seconds <= 0:
            self.schedule_paste(delay_ms=100, hide_widget=False)
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
        if clear_status:
            self.status_var.set("Timer stopped.")

    def schedule_paste(self, delay_ms: int, hide_widget: bool) -> None:
        prompt = self.prompt_for_paste(hide_widget=hide_widget)
        if not prompt:
            messagebox.showinfo("No selection", "Select a prompt first.")
            return
        if not self.keyboard or not Key:
            self.copy_to_clipboard(prompt.body)
            messagebox.showerror(
                "Keyboard control unavailable",
                "Prompt copied to clipboard, but automatic paste requires pynput. Run ./run.sh to install dependencies.",
            )
            return

        self.copy_to_clipboard(prompt.body)
        action = "typing" if self.insert_method.get() == "type" else "pasting"
        self.status_var.set(f"Prompt copied. {action.capitalize()} into the focused app...")
        if hide_widget:
            self.withdraw()
        self.after(delay_ms, lambda: self.insert_and_restore(prompt.body, restore_widget=hide_widget))

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

    def insert_and_restore(self, text: str, restore_widget: bool) -> None:
        try:
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
            self.status_var.set("Inserted selected prompt.")
        except Exception as exc:
            self.status_var.set("Paste failed; prompt remains on the clipboard.")
            messagebox.showerror("Paste failed", str(exc))
        finally:
            if restore_widget:
                self.after(250, self.deiconify)

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()


def main() -> int:
    app = PromptWidget()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
