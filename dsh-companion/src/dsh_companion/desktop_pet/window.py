# Derived from wraven68/deepseek-harness-pet (MIT), copyright 2026 wraven68.
from __future__ import annotations

import random
from queue import SimpleQueue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from pathlib import Path

from PIL import ImageTk

from .animation import AnimationPlayer, status_animation

from .client import read_status, control, base_url, assign_task
import json
import os
import webbrowser


SOURCE_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
ASSET_DIR = BUNDLE_DIR / "assets"
KEY_GREEN = "#00ff00"
WINDOW_WIDTH = 380
WINDOW_HEIGHT = 425
IMAGE_WIDTH = 360
IMAGE_HEIGHT = 360
POLL_MS = 750
PANEL_WIDTH = 248
PANEL_ALPHA = 0.82
CARD_BG = "#f5faff"
TEXT = "#29475f"
MUTED = "#688398"


class HarnessPet:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("大肥鱼桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY_GREEN)
        self.root.attributes("-transparentcolor", KEY_GREEN)
        self.scale_factor = 1.0
        self.window_width = WINDOW_WIDTH
        self.window_height = WINDOW_HEIGHT
        self.image_width = IMAGE_WIDTH
        self.image_height = IMAGE_HEIGHT
        self._place_window()

        self.canvas = tk.Canvas(
            self.root,
            width=self.window_width,
            height=self.window_height,
            bg=KEY_GREEN,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
        self.player = AnimationPlayer(ASSET_DIR)
        self.current_animation = "idle"
        self.player.select("idle", time.monotonic())
        self.current_image = ImageTk.PhotoImage(self.player.frame(time.monotonic(), (self.image_width, self.image_height)))
        self.character = self.canvas.create_image(
            self.window_width // 2,
            self.image_height // 2 + int(14 * self.scale_factor),
            image=self.current_image,
            anchor="center",
            tags=("character",),
        )
        self.badge_items: list[int] = []
        self.drag_origin: tuple[int, int] | None = None
        self.was_dragged = False
        self.status = self._offline_status("正在找大肥鱼…")
        self.task_panel: tk.Toplevel | None = None
        self.panel_labels: dict[str, tk.Label] = {}
        self.idle_animation = "idle"
        self.idle_until = time.monotonic() + 15
        self.reaction_until = 0.0
        self.is_polling = False
        self.status_updates = SimpleQueue()
        self.task_updates = SimpleQueue()
        self.is_assigning = False
        self.command_feedback = ""
        self.pending_command = None

        self._bind_events()
        self._set_scale(float(os.environ.get("COMPANION_PET_SCALE", "0.55")))
        self._restore_position()
        self._draw_badge()
        self._tick_animation()
        self._poll_status()

    def _place_window(self) -> None:
        self.root.update_idletasks()
        x = max(12, self.root.winfo_screenwidth() - self.window_width - 24)
        y = max(12, self.root.winfo_screenheight() - self.window_height - 70)
        self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._stop_drag)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.root.bind("<Escape>", lambda _event: self._quit())
        self.root.bind_all("<Control-MouseWheel>", self._on_zoom_wheel)
        self.root.bind_all("<Control-plus>", lambda _event: self._change_scale(self.scale_factor + 0.1))
        self.root.bind_all("<Control-minus>", lambda _event: self._change_scale(self.scale_factor - 0.1))

    def _start_drag(self, event: tk.Event) -> None:
        self.drag_origin = (event.x_root, event.y_root)
        self.was_dragged = False

    def _drag(self, event: tk.Event) -> None:
        if self.drag_origin is None:
            return
        origin_x, origin_y = self.drag_origin
        current_x = self.root.winfo_x() + event.x_root - origin_x
        current_y = self.root.winfo_y() + event.y_root - origin_y
        if abs(event.x_root - origin_x) + abs(event.y_root - origin_y) > 3:
            self.was_dragged = True
        self.root.geometry(f"+{current_x}+{current_y}")
        if self._task_panel_is_visible():
            self._position_task_panel()
        self.drag_origin = (event.x_root, event.y_root)

    def _stop_drag(self, _event: tk.Event) -> None:
        should_toggle_panel = not self.was_dragged
        self.drag_origin = None
        self.was_dragged = False
        self._save_position()
        if should_toggle_panel:
            self.reaction_until = time.monotonic() + self.player.duration("wave")
            self._toggle_task_panel()

    def _show_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="显示 / 隐藏任务面板", command=self._toggle_task_panel)
        menu.add_command(label="打开大肥鱼工作台", command=lambda: webbrowser.open(base_url()))
        menu.add_command(label="让肥鱼休息", command=lambda: self._control("pause"))
        menu.add_command(label="唤醒肥鱼", command=lambda: self._control("resume"))
        menu.add_separator()
        actions = tk.Menu(menu, tearoff=False)
        for label, animation in (("跳个舞", "dance"), ("玩魔方", "cube"), ("吃火锅", "hotpot")):
            actions.add_command(label=label, command=lambda name=animation: self._play_idle(name))
        menu.add_cascade(label="待命时摸鱼", menu=actions)
        menu.add_command(label="放大 10%", command=lambda: self._change_scale(self.scale_factor + 0.1))
        menu.add_command(label="缩小 10%", command=lambda: self._change_scale(self.scale_factor - 0.1))
        menu.add_command(label="还原为 100%", command=lambda: self._change_scale(1.0))
        menu.add_separator()
        menu.add_command(label="退出桌宠", command=self._quit)
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _play_idle(self, animation: str) -> None:
        self.idle_animation = animation
        self.idle_until = time.monotonic() + self.player.duration(animation)

    def _on_zoom_wheel(self, event: tk.Event) -> str:
        step = 0.1 if event.delta > 0 else -0.1
        self._change_scale(self.scale_factor + step)
        return "break"

    def _change_scale(self, requested: float) -> None:
        self._set_scale(requested)
        self._save_position()

    def _set_scale(self, requested: float) -> None:
        scale = max(0.25, min(1.45, round(requested, 2)))
        if abs(scale - self.scale_factor) < 0.01:
            return
        self.scale_factor = scale
        self.window_width = round(WINDOW_WIDTH * scale)
        self.window_height = round(WINDOW_HEIGHT * scale)
        self.image_width = round(IMAGE_WIDTH * scale)
        self.image_height = round(IMAGE_HEIGHT * scale)
        self.window_width = max(self.window_width, 210)
        self.window_height = max(self.window_height, self.image_height + 56)
        self.canvas.configure(width=self.window_width, height=self.window_height)
        self.root.geometry(f"{self.window_width}x{self.window_height}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self._render_frame(time.monotonic(), force=True)
        self._draw_badge()
        if self._task_panel_is_visible():
            self._position_task_panel()

    def _quit(self) -> None:
        self._save_position()
        self.player.close()
        self.root.destroy()

    @staticmethod
    def _offline_status(message: str) -> dict:
        return {
            "state": "offline",
            "percent": None,
            "completed": 0,
            "total": 0,
            "inProgress": 0,
            "currentTask": None,
            "todos": [],
            "message": message,
        }

    def _poll_status(self) -> None:
        if not self.is_polling:
            self.is_polling = True
            thread = threading.Thread(target=self._read_status_worker, daemon=True)
            thread.start()
        self.root.after(POLL_MS, self._poll_status)

    def _read_status_worker(self) -> None:
        try:
            status = read_status()
        except Exception:
            status = self._offline_status("暂时无法读取任务")
        self.status_updates.put(status)

    def _apply_status(self, status: dict) -> None:
        self.is_polling = False
        if status.get("state") == "idle" and self.status.get("state") != "idle":
            self.idle_animation = "idle"
            self.idle_until = time.monotonic() + 15
        self.status = status
        self._draw_badge()
        self._refresh_task_panel()

    def _choose_animation(self, now: float) -> str:
        if self.was_dragged:
            return "drag"
        animation = status_animation(self.status)
        if animation != "idle":
            return animation
        if now < self.reaction_until:
            return "wave"
        if now >= self.idle_until:
            self.idle_animation = random.choice(["idle", "idle", "idle", "cube", "dance", "hotpot"])
            self.idle_until = now + self.player.duration(self.idle_animation)
        return self.idle_animation

    def _render_frame(self, now: float, *, force=False) -> None:
        frame = self.player.frame(now, (self.image_width, self.image_height), force=force)
        if frame is not None:
            self.current_image = ImageTk.PhotoImage(frame)
            self.canvas.itemconfigure(self.character, image=self.current_image)
        self.canvas.coords(self.character, self.window_width // 2, self.image_height // 2)

    def _tick_animation(self) -> None:
        # HTTP workers only enqueue data; all Tk calls stay on its UI thread.
        while not self.status_updates.empty():
            self._apply_status(self.status_updates.get())
        while not self.task_updates.empty():
            self._apply_task_result(*self.task_updates.get())
        now = time.monotonic()
        animation = self._choose_animation(now)
        if animation != self.current_animation:
            self.current_animation = animation
            self.player.select(animation, now)
        self._render_frame(now)
        self.root.after(20, self._tick_animation)

    @staticmethod
    def _status_style(state: object) -> tuple[str, str]:
        return {
            "working": ("正在执行", "#287ba6"),
            "completed": ("任务完成", "#2b876b"),
            "idle": ("自由待命", "#557c99"),
            "paused": ("正在休息", "#557c99"),
            "offline": ("未连接", "#788796"),
            "error": ("读取失败", "#b3677a"),
        }.get(state, ("等待任务", "#557c99"))

    @staticmethod
    def _rounded_card(canvas: tk.Canvas, x: int, y: int, width: int, height: int,
                      *, radius=12, fill=CARD_BG, outline="#ccdfe9") -> int:
        right, bottom = x + width, y + height
        return canvas.create_polygon(
            x+radius, y, right-radius, y, right, y, right, y+radius,
            right, bottom-radius, right, bottom, right-radius, bottom,
            x+radius, bottom, x, bottom, x, bottom-radius, x, y+radius, x, y,
            smooth=True, splinesteps=24, fill=fill, outline=outline, width=1,
        )

    def _draw_badge(self) -> None:
        for item in self.badge_items:
            self.canvas.delete(item)
        state_text, accent = self._status_style(self.status.get("state"))
        text = self.status.get("title") or state_text
        font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        width = min(self.window_width-12, font.measure(text)+22)
        x, y = (self.window_width-width)//2, self.image_height+4
        self.badge_items = [
            self._rounded_card(self.canvas, x, y, width, 26, radius=10),
            self.canvas.create_text(self.window_width//2, y+13, text=text,
                                    fill=accent, font=font),
        ]

    def _control(self, operation: str) -> None:
        def apply():
            control(operation)
            self._read_status_worker()
        threading.Thread(target=apply, daemon=True).start()

    def _position_file(self) -> Path:
        return Path(os.environ.get("COMPANION_PET_STATE", str(SOURCE_DIR.parents[2] / "var/pet-position.json")))

    def _save_position(self) -> None:
        path = self._position_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                                    "scale": self.scale_factor}), encoding="utf-8")

    def _restore_position(self) -> None:
        path = self._position_file()
        if path.exists():
            position = json.loads(path.read_text(encoding="utf-8"))
            self._set_scale(position["scale"])
            self.root.geometry(f"+{position['x']}+{position['y']}")

    def _task_panel_is_visible(self) -> bool:
        return self.task_panel is not None and bool(self.task_panel.winfo_viewable())

    def _position_task_panel(self) -> None:
        if self.task_panel is None:
            return
        width, height = PANEL_WIDTH, self.panel_height
        x = self.root.winfo_x() - width - 8
        if x < 12:
            x = self.root.winfo_x() + self.window_width + 8
        x = max(12, min(x, self.root.winfo_screenwidth()-width-12))
        y = self.root.winfo_y() + self.image_height + 30 - height
        y = max(12, min(y, self.root.winfo_screenheight()-height-54))
        self.task_panel.geometry(f"{width}x{height}+{x}+{y}")

    def _create_task_panel(self) -> None:
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.configure(bg=KEY_GREEN)
        panel.attributes("-transparentcolor", KEY_GREEN)
        panel.attributes("-alpha", PANEL_ALPHA)
        panel.withdraw()
        panel.bind("<Escape>", lambda _event: self._hide_task_panel())
        panel.protocol("WM_DELETE_WINDOW", self._hide_task_panel)
        self.panel_height = 124
        self.panel_canvas = tk.Canvas(panel, bg=KEY_GREEN, highlightthickness=0, bd=0)
        self.panel_canvas.pack(fill="both", expand=True)
        self.panel_labels = {
            "status": tk.Label(panel, bg=CARD_BG, fg=TEXT, anchor="w",
                               font=("Microsoft YaHei UI", 10, "bold")),
            "count": tk.Label(panel, bg=CARD_BG, fg=MUTED, anchor="w",
                              font=("Microsoft YaHei UI", 8)),
            "tasks": tk.Label(panel, bg=CARD_BG, fg=TEXT, anchor="nw", justify="left",
                              font=("Microsoft YaHei UI", 9)),
        }
        self.panel_close = tk.Button(panel, text="×", command=self._hide_task_panel,
            bg=CARD_BG, fg=MUTED, activebackground="#e3eff8", activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 12), bd=0, highlightthickness=0, cursor="hand2")
        self.panel_workbench = tk.Button(panel, text="工作台 ↗", command=lambda: webbrowser.open(base_url()),
            bg=CARD_BG, fg="#287ba6", activebackground="#e3eff8", activeforeground=TEXT,
            relief="flat", font=("Microsoft YaHei UI", 8), bd=0, highlightthickness=0, cursor="hand2")
        self.panel_labels["feedback"] = tk.Label(panel, bg=CARD_BG, fg=MUTED, anchor="w",
            font=("Microsoft YaHei UI", 8))
        self.panel_buttons = {}
        for action, label in (("wake", "唤醒"), ("watch", "刷视频"), ("socialize", "找人聊")):
            self.panel_buttons[action] = tk.Button(panel, text=label,
                command=lambda action=action: self._assign_task(action),
                bg="#e6f1fa", fg=TEXT, activebackground="#d3e7f5", activeforeground=TEXT,
                relief="flat", font=("Microsoft YaHei UI", 9), bd=0,
                highlightthickness=0, cursor="hand2")
        self.panel_entry = tk.Entry(panel, bg="#ffffff", fg=TEXT, insertbackground=TEXT,
            relief="flat", font=("Microsoft YaHei UI", 9), bd=0,
            highlightthickness=1, highlightbackground="#d8e5ed", highlightcolor="#91b8d0")
        self.panel_entry.bind("<Return>", lambda _event: self._assign_task("custom"))
        self.panel_entry.bind("<Button-1>", lambda _event: self.panel_entry.focus_set())
        self.panel_send = tk.Button(panel, text="派活", command=lambda: self._assign_task("custom"),
            bg="#e6f1fa", fg="#287ba6", activebackground="#d3e7f5", activeforeground=TEXT,
            relief="flat", font=("Microsoft YaHei UI", 9), bd=0,
            highlightthickness=0, cursor="hand2")
        self.task_panel = panel

    def _show_task_panel(self) -> None:
        if self.task_panel is None:
            self._create_task_panel()
        self._refresh_task_panel()
        self.task_panel.deiconify()
        self.task_panel.lift()

    def _hide_task_panel(self) -> None:
        if self.task_panel is not None:
            self.task_panel.withdraw()

    def _toggle_task_panel(self) -> None:
        if self._task_panel_is_visible():
            self._hide_task_panel()
        else:
            self._show_task_panel()

    @staticmethod
    def _short_text(text: str, limit=17) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[:limit-1] + "…"

    def _refresh_task_panel(self) -> None:
        if self.task_panel is None:
            return
        state_text, accent = self._status_style(self.status.get("state"))
        self.panel_labels["status"].configure(
            text=self._short_text(self.status.get("title") or state_text, 16), fg=accent)
        self.panel_labels["count"].configure(text=
            f"{self.status.get('activeCount', 0)} 在忙 · {self.status.get('waitingCount', 0)} 排队"
            f" · 今日 {self.status.get('completed', 0)}")
        todos = self.status.get("todos") or []
        markers = {"completed": "✓", "in_progress": "●", "pending": "○"}
        lines = [f"{markers.get(todo.get('status'), '○')}  {self._short_text(todo['content'])}"
                 for todo in todos[:3]]
        if len(todos) > 3:
            lines.append(f"另有 {len(todos)-3} 项 · 去工作台看")
        if not lines:
            text = self.status.get("message") or self.status.get("direction") or "没在忙，摸会儿鱼 🐟"
            lines = [self._short_text(text)]
        self.panel_labels["tasks"].configure(text="\n".join(lines))
        self.panel_height = 178 + 20*len(lines)
        self.panel_labels["feedback"].configure(text=self._short_text(self._command_message(), 16))
        for button in (*self.panel_buttons.values(), self.panel_send):
            button.configure(state="disabled" if self.is_assigning else "normal")
        canvas = self.panel_canvas
        canvas.delete("all")
        self._rounded_card(canvas, 1, 1, PANEL_WIDTH-2, self.panel_height-2, radius=14)
        canvas.create_window(12, 12, window=self.panel_labels["status"], anchor="nw", width=200)
        canvas.create_window(PANEL_WIDTH-27, 8, window=self.panel_close, anchor="nw", width=20, height=24)
        canvas.create_window(12, 39, window=self.panel_labels["count"], anchor="nw", width=PANEL_WIDTH-24)
        canvas.create_line(13, 64, PANEL_WIDTH-13, 64, fill="#d8e5ed")
        canvas.create_window(12, 72, window=self.panel_labels["tasks"], anchor="nw", width=PANEL_WIDTH-24)
        controls_y = 78 + 20*len(lines)
        for index, button in enumerate(self.panel_buttons.values()):
            canvas.create_window(12+76*index, controls_y, window=button, anchor="nw", width=72, height=26)
        canvas.create_window(12, controls_y+33, window=self.panel_entry, anchor="nw", width=174, height=26)
        canvas.create_window(192, controls_y+33, window=self.panel_send, anchor="nw", width=44, height=26)
        canvas.create_window(12, self.panel_height-27, window=self.panel_labels["feedback"],
                             anchor="nw", width=150, height=21)
        canvas.create_window(PANEL_WIDTH-80, self.panel_height-27, window=self.panel_workbench,
                             anchor="nw", width=68, height=21)
        self._position_task_panel()

    def _assign_task(self, action: str) -> None:
        if self.is_assigning:
            return
        text = self.panel_entry.get() if action == "custom" else ""
        if action == "custom" and not text.strip():
            self.command_feedback = "先写一句想做的事呀"
            self.panel_entry.focus_set()
            self._refresh_task_panel()
            return
        self.is_assigning = True
        self.command_feedback = "正在派活…"
        self._refresh_task_panel()
        threading.Thread(target=self._assign_task_worker, args=(action, text), daemon=True).start()

    def _assign_task_worker(self, action: str, text: str) -> None:
        from urllib.error import HTTPError, URLError
        try:
            result = assign_task(action, text)
        except HTTPError as exc:
            self.task_updates.put((action, text, None, f"派活没成功 · HTTP {exc.code}"))
        except URLError:
            self.task_updates.put((action, text, None, "工作台没连接"))
        except Exception as exc:
            self.task_updates.put((action, text, None, str(exc)))
        else:
            self.task_updates.put((action, text, result, ""))

    def _apply_task_result(self, action: str, text: str, result: dict | None, error: str) -> None:
        self.is_assigning = False
        self.command_feedback = error
        if result:
            self.pending_command = {"id": result["id"], "status": result["status"],
                                    "title": result["payload"]["task_title"]}
            if action == "custom" and self.panel_entry.get() == text:
                self.panel_entry.delete(0, "end")
        self._refresh_task_panel()

    def _command_message(self) -> str:
        if self.command_feedback:
            return self.command_feedback
        command = self.status.get("command")
        if self.pending_command:
            if command and command["id"] == self.pending_command["id"]:
                self.pending_command = None
            else:
                command = self.pending_command
        if not command:
            return "一句话派活，回车就行"
        state = command["status"]
        if state == "succeeded":
            return "✓ " + (command.get("response") or "办好啦")
        if state == "failed":
            return "未完成 · " + (command.get("error") or command["title"])
        prefix = {"ready": "已排队", "running": "在做", "cancelled": "已取消"}.get(state, state)
        return prefix + " · " + command["title"]

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    try:
        HarnessPet().run()
    except Exception as exc:
        import tkinter.messagebox as messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("大肥鱼桌宠启动失败", str(exc))
        root.destroy()
        sys.exit(1)
