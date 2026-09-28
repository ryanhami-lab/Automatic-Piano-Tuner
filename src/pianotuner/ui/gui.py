"""Small desktop view. The runtime worker owns all session and I/O work."""
from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from pianotuner.domain.math import Target

SCENARIOS = ("nominal", "no_response", "wrong_direction", "stale_audio", "disconnect", "unload_shift",
             "lost_ack", "lost_done", "sudden_slip")
NOTES = ("A3", "A#3", "B3", "C4", "C#4", "D4", "D#4", "E4", "F4", "F#4", "G4", "G#4", "A4")
BG = "#eef2f6"
INK = "#142536"
MUTED = "#5b6d7e"
ACCENT = "#087b72"


def parse_target(value: str) -> float:
    """Accept explicit note labels or finite custom frequencies in the supported band."""
    try:
        hz = float(value)
    except ValueError:
        return Target.named(value).target_hz
    return Target.from_hz(hz).target_hz


def display_number(value: Any, *, signed: bool = False) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


class TunerWindow:
    def __init__(self, root: tk.Tk, session_factory: Callable[..., Any] | None = None,
                 output_dir: Path = Path("runs")) -> None:
        self.root = root
        self.session_factory = session_factory
        self.output_dir = output_dir
        self.snapshots: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.commands: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.closing = False
        self.last_state = ""
        self.pending_command: tuple[str, str] | None = None
        self.last_snapshot: dict[str, Any] = {}
        self.target = tk.StringVar(value="A4")
        self.mode = tk.StringVar(value="SIMULATION")
        self.source_file = tk.StringVar()
        self.channel = tk.StringVar(value="")
        self.mode_description = tk.StringVar(value="One string  ·  Audio feedback  ·  No hardware connected")
        self.elapsed_label = tk.StringVar(value="VIRTUAL ELAPSED")
        self.elapsed_description = tk.StringVar(value="accelerated simulation")
        self.scenario = tk.StringVar(value="nominal")
        self.seed = tk.StringVar(value="7")
        self.manual = tk.BooleanVar(value=False)
        self.state = tk.StringVar(value="Ready")
        self.frequency = tk.StringVar(value="—")
        self.error = tk.StringVar(value="—")
        self.quality = tk.StringVar(value="Waiting for a session")
        self.moves = tk.StringVar(value="0")
        self.elapsed = tk.StringVar(value="0.0 s")
        self.prompt = tk.StringVar(value="Choose a target and scenario, then start the simulation.")
        self.result = tk.StringVar(value="No session yet")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._poll)

    def _build(self) -> None:
        root = self.root
        root.title("Automatic Piano Tuner • Simulation")
        root.geometry("1060x820")
        root.minsize(980, 760)
        root.configure(bg=BG)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", font=("Segoe UI", 10), padding=(16, 9))
        style.configure("Primary.TButton", background=ACCENT, foreground="white")
        style.map("Primary.TButton", background=[("active", "#07685f"), ("disabled", "#bdc9ce")])
        style.configure("Stop.TButton", background="#9e352d", foreground="white")
        style.map("Stop.TButton", background=[("active", "#812b25"), ("disabled", "#d2bdba")])
        style.configure("TCombobox", padding=7, font=("Segoe UI", 10))
        style.configure("TEntry", padding=7)
        style.configure("TCheckbutton", background=BG, foreground=INK, font=("Segoe UI", 10))

        header = tk.Frame(root, background=INK, padx=28, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="Automatic Piano Tuner", background=INK, foreground="white",
                 font=("Segoe UI", 21, "bold")).pack(anchor="w")
        row = tk.Frame(header, background=INK)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, textvariable=self.mode, background="#bfe8de", foreground="#104e47",
                 padx=10, pady=4, font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(row, textvariable=self.mode_description, background=INK,
                 foreground="#d2dde7", font=("Segoe UI", 10)).pack(side="left", padx=14)

        body = ttk.Frame(root, padding=(28, 12))
        body.pack(fill="both", expand=True)
        mode_row = ttk.Frame(body)
        mode_row.pack(fill="x", pady=(0, 8))
        ttk.Label(mode_row, text="Mode", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        self.mode_control = ttk.Combobox(mode_row, textvariable=self.mode,
            values=("SIMULATION", "FILE_ANALYSIS"), width=18, state="readonly")
        self.mode_control.pack(side="left")
        self.mode_control.bind("<<ComboboxSelected>>", self._mode_changed)
        ttk.Label(mode_row, text="Live monitoring and hardware commissioning use the CLI.",
                  style="Muted.TLabel").pack(side="left", padx=18)
        self.file_row = ttk.Frame(body)
        ttk.Label(self.file_row, text="WAV file", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        self.file_control = ttk.Entry(self.file_row, textvariable=self.source_file, width=58)
        self.file_control.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.browse_button = ttk.Button(self.file_row, text="Browse", command=self._browse)
        self.browse_button.pack(side="left", padx=(0, 16))
        ttk.Label(self.file_row, text="Channel", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.channel_control = ttk.Entry(self.file_row, textvariable=self.channel, width=5)
        self.channel_control.pack(side="left")
        selectors = ttk.Frame(body)
        self.selectors = selectors
        selectors.pack(fill="x")
        for column, (label, variable, values, width) in enumerate((
            ("Target note or Hz", self.target, NOTES, 15),
            ("Scenario", self.scenario, SCENARIOS, 21),
        )):
            cell = ttk.Frame(selectors)
            cell.grid(row=0, column=column, padx=(0, 18), sticky="w")
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w", pady=(0, 5))
            control = ttk.Combobox(cell, textvariable=variable, values=values, width=width,
                                   state="normal" if column == 0 else "readonly")
            control.pack()
            if column == 0:
                self.target_control = control
            else:
                self.scenario_control = control
        seed_cell = ttk.Frame(selectors)
        seed_cell.grid(row=0, column=2, sticky="w", padx=(0, 18))
        ttk.Label(seed_cell, text="Seed", style="Muted.TLabel").pack(anchor="w", pady=(0, 5))
        self.seed_control = ttk.Entry(seed_cell, textvariable=self.seed, width=9)
        self.seed_control.pack()
        self.manual_control = ttk.Checkbutton(selectors, text="Manual strikes and unloading", variable=self.manual)
        self.manual_control.grid(row=0, column=3, sticky="sw", pady=(0, 7))

        metrics = tk.Frame(body, bg="white", padx=22, pady=12)
        metrics.pack(fill="x", pady=(14, 0))
        for column, (label, variable, unit, size) in enumerate((
            ("MEASURED FREQUENCY", self.frequency, "Hz", 28),
            ("ERROR FROM TARGET", self.error, "cents", 28),
            ("ISSUED MOVES", self.moves, "finite commands", 24),
            ("VIRTUAL ELAPSED", self.elapsed, "accelerated simulation", 22),
        )):
            metric_cell = tk.Frame(metrics, bg="white")
            metric_cell.grid(row=0, column=column, sticky="nw", padx=(0, 24))
            label_options = {"textvariable": self.elapsed_label} if column == 3 else {"text": label}
            tk.Label(metric_cell, **label_options, bg="white", fg=MUTED,
                     font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(metric_cell, textvariable=variable, bg="white", fg=INK,
                     font=("Segoe UI", size, "bold")).pack(anchor="w", pady=(5, 0))
            unit_options = {"textvariable": self.elapsed_description} if column == 3 else {"text": unit}
            tk.Label(metric_cell, **unit_options, bg="white", fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        self.meter = tk.Canvas(body, height=55, bg=BG, highlightthickness=0)
        self.meter.pack(fill="x", pady=(6, 4))
        self.meter.bind("<Configure>", lambda _: self._draw_meter(self.last_snapshot.get("cents_error")))

        status = ttk.Frame(body)
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.state, font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(status, textvariable=self.quality, style="Muted.TLabel").pack(side="right")
        ttk.Label(body, textvariable=self.prompt, wraplength=930,
                  font=("Segoe UI", 11)).pack(anchor="w", pady=(6, 10))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Start simulation", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="Stop", style="Stop.TButton", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 16))
        self.strike_button = ttk.Button(buttons, text="Strike", command=lambda: self._command("strike"), state="disabled")
        self.strike_button.pack(side="left", padx=(0, 8))
        self.unload_button = ttk.Button(buttons, text="Confirm unloaded", command=lambda: self._command("unload"), state="disabled")
        self.unload_button.pack(side="left")
        self.reset_button = ttk.Button(buttons, text="New session", command=self.reset)
        self.reset_button.pack(side="right")

        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x", pady=(9, 0))
        ttk.Label(footer, textvariable=self.result, style="Muted.TLabel", wraplength=920).pack(anchor="w")
        ttk.Label(footer, text="Single-string frequency reference • A3–A4 • ±2 cents attached / ±3 cents unloaded",
                  style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Label(body, text="SESSION ACTIVITY", style="Muted.TLabel",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(14, 7))
        log_frame = ttk.Frame(body)
        log_frame.pack(fill="both", expand=True)
        self.activity = tk.Text(log_frame, height=4, wrap="word", bg="white", fg=INK,
                                relief="flat", padx=13, pady=10, font=("Consolas", 10), state="disabled")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.activity.yview)
        self.activity.configure(yscrollcommand=scroll.set)
        self.activity.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _append(self, value: str) -> None:
        self.activity.configure(state="normal")
        self.activity.insert("end", value + "\n")
        if int(self.activity.index("end-1c").split(".")[0]) > 250:
            self.activity.delete("1.0", "2.0")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _draw_meter(self, cents: Any) -> None:
        canvas = self.meter
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        left, right, y = 18, width - 18, 27
        center = (left + right) / 2
        scale = (right - left) / 40
        canvas.create_rectangle(center - 2 * scale, y - 9, center + 2 * scale, y + 9,
                                fill="#c4e6dc", outline="")
        canvas.create_line(left, y, right, y, fill="#aabac8", width=2)
        for value in (-20, -10, 0, 10, 20):
            x = center + value * scale
            canvas.create_line(x, y - 4, x, y + 5, fill=MUTED)
            canvas.create_text(x, y + 20, text=f"{value:+d}" if value else "0", fill=MUTED,
                               font=("Segoe UI", 9))
        if isinstance(cents, (int, float)) and math.isfinite(cents):
            x = center + max(-20, min(20, cents)) * scale
            color = ACCENT if abs(cents) <= 2 else "#ba7820"
            canvas.create_polygon(x, y - 5, x - 6, y - 17, x + 6, y - 17, fill=color)
        canvas.create_text(left, 5, text="FLAT", anchor="w", fill=MUTED, font=("Segoe UI", 8))
        canvas.create_text(right, 5, text="SHARP", anchor="e", fill=MUTED, font=("Segoe UI", 8))

    def _active(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _mode_changed(self, _event: Any = None) -> None:
        if self._active():
            return
        is_file = self.mode.get() == "FILE_ANALYSIS"
        self.root.title("Automatic Piano Tuner • " + ("File analysis" if is_file else "Simulation"))
        self.mode_description.set("WAV analysis  ·  Measurement only  ·  No actuator" if is_file else
                                  "One string  ·  Audio feedback  ·  No hardware connected")
        self.elapsed_label.set("ELAPSED" if is_file else "VIRTUAL ELAPSED")
        self.elapsed_description.set("analysis time" if is_file else "accelerated simulation")
        self.start_button.configure(text="Analyze WAV" if is_file else "Start simulation")
        if is_file:
            self.file_row.pack(before=self.selectors, fill="x", pady=(0, 8))
        else:
            self.file_row.pack_forget()
        self._set_running(False)
        self.reset()

    def _browse(self) -> None:
        filename = filedialog.askopenfilename(parent=self.root, title="Choose a WAV file",
                                              filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")])
        if filename:
            self.source_file.set(filename)

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.reset_button.configure(state="disabled" if running else "normal")
        self.target_control.configure(state="disabled" if running else "normal")
        self.mode_control.configure(state="disabled" if running else "readonly")
        is_file = self.mode.get() == "FILE_ANALYSIS"
        self.scenario_control.configure(state="disabled" if running or is_file else "readonly")
        self.seed_control.configure(state="disabled" if running or is_file else "normal")
        self.manual_control.configure(state="disabled" if running or is_file else "normal")
        for control in (self.file_control, self.channel_control, self.browse_button):
            control.configure(state="disabled" if running else "normal")
        if not running:
            self.strike_button.configure(state="disabled")
            self.unload_button.configure(state="disabled")

    def start(self) -> None:
        if self._active() or self.closing:
            return
        try:
            target_hz = parse_target(self.target.get().strip())
            if self.mode.get() == "FILE_ANALYSIS":
                source = Path(self.source_file.get())
                if not source.is_file():
                    raise ValueError("Choose an existing WAV file")
                channel = int(self.channel.get()) if self.channel.get().strip() else None
                if channel is not None and channel < 0:
                    raise ValueError("Channel is a zero-based index; leave blank for mono")
                return self._start_file(source, target_hz, channel)
            seed = int(self.seed.get())
            if not 0 <= seed <= 2**31 - 1:
                raise ValueError("Seed must be an integer from 0 through 2147483647")
            scenario = self.scenario.get()
            if scenario not in SCENARIOS:
                raise ValueError("Select a supported scenario")
        except ValueError as exc:
            self.prompt.set(str(exc))
            self.state.set("Check session settings")
            return
        self.reset()
        self.stop_event = threading.Event()
        self.commands = queue.Queue()
        manual = self.manual.get()
        self._set_running(True)
        self.state.set("Starting")
        self.result.set("Session active • Waiting for the runtime")
        self._append(f"SIMULATION  target {target_hz:.4f} Hz  seed {seed}  scenario {scenario}")
        self._append("Elapsed time is virtual; the 60-second stability wait is accelerated.")

        def work() -> None:
            try:
                factory = self.session_factory
                if factory is None:
                    from pianotuner.runtime.simulation import SimulationSession
                    factory = SimulationSession
                session = factory(target_hz=target_hz, seed=seed, scenario=scenario,
                                  output_dir=self.output_dir, manual=manual)
                summary = session.run(callback=lambda value: self.snapshots.put(("snapshot", dict(value))),
                                      stop_event=self.stop_event, commands=self.commands, realtime=True)
                self.snapshots.put(("finished", summary))
            except Exception as exc:  # noqa: BLE001 - contain worker failures at the UI boundary
                self.stop_event.set()
                self.snapshots.put(("error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=work, name="pianotuner-session", daemon=False)
        self.worker.start()

    def _start_file(self, source: Path, target_hz: float, channel: int | None) -> None:
        self.reset()
        self.stop_event = threading.Event()
        self._set_running(True)
        self.state.set("Analyzing WAV")
        self.result.set("File analysis active")
        self.prompt.set("Reading the selected file. No actuator can be connected in this mode.")
        self._append(f"FILE_ANALYSIS  target {target_hz:.4f} Hz  source {source}")

        def work() -> None:
            started = time.monotonic()
            try:
                from pianotuner.runtime.analysis import analyze_file
                summary = analyze_file(source, target_hz=target_hz, channel=channel,
                                       output_dir=self.output_dir, stop_event=self.stop_event)
                measurement = summary.get("measurement", {}) or {}
                outcome = summary.get("outcome", "ANALYSIS_COMPLETE")
                self.snapshots.put(("snapshot", {"mode": "FILE_ANALYSIS",
                    "state": "ABORTED" if outcome == "ABORTED" else "COMPLETE",
                    "frequency_hz": measurement.get("frequency_hz"),
                    "cents_error": measurement.get("cents_error"),
                    "quality": measurement.get("reasons") or ("Accepted" if measurement.get("accepted") else "No estimate"),
                    "move_count": 0, "elapsed_s": time.monotonic() - started,
                    "prompt": "File analysis ended. This measurement is not a tuning verification.",
                    "session_dir": summary.get("session_dir")}))
                self.snapshots.put(("finished", summary))
            except Exception as exc:  # noqa: BLE001 - contain worker errors at the UI boundary
                self.snapshots.put(("error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=work, name="pianotuner-file-analysis", daemon=False)
        self.worker.start()

    def stop(self) -> None:
        if self._active():
            self.stop_event.set()
            self.prompt.set("Stop requested. Waiting for the runtime to disarm and close the session.")
            self.strike_button.configure(state="disabled")
            self.unload_button.configure(state="disabled")

    def _command(self, command: str) -> None:
        if self._active() and not self.stop_event.is_set():
            self.commands.put(command)
            self.pending_command = (str(self.last_snapshot.get("state", "")),
                                    str(self.last_snapshot.get("prompt", "")))
            self.strike_button.configure(state="disabled")
            self.unload_button.configure(state="disabled")

    def reset(self) -> None:
        if self._active():
            return
        self.last_snapshot = {}
        self.last_state = ""
        self.pending_command = None
        self.state.set("Ready")
        self.frequency.set("—")
        self.error.set("—")
        self.quality.set("Waiting for a session")
        self.moves.set("0")
        self.elapsed.set("0.0 s")
        self.prompt.set("Choose a WAV file and target. For multichannel audio, select a zero-based channel index."
                        if self.mode.get() == "FILE_ANALYSIS" else
                        "Choose a target and scenario, then start the simulation.")
        self.result.set("No session yet")
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.configure(state="disabled")
        self._draw_meter(None)

    def _snapshot(self, data: dict[str, Any]) -> None:
        self.last_snapshot = data
        state = str(data.get("state", ""))
        self.state.set(state.replace("_", " ").title())
        self.frequency.set(display_number(data.get("frequency_hz")))
        self.error.set(display_number(data.get("cents_error"), signed=True))
        quality = data.get("quality", "Awaiting audio")
        self.quality.set(", ".join(map(str, quality)) if isinstance(quality, (list, tuple)) else str(quality))
        self.moves.set(str(data.get("move_count", 0)))
        self.elapsed.set(f"{float(data.get('elapsed_s', 0)):.1f} s")
        prompt = str(data.get("prompt", ""))
        if self.pending_command != (state, prompt):
            self.pending_command = None
        self.prompt.set(prompt)
        if data.get("session_dir"):
            self.result.set(f"Session active • Evidence: {data['session_dir']}")
        self._draw_meter(data.get("cents_error"))
        if state != self.last_state:
            self._append(f"{self.elapsed.get():>9}  {state}  {prompt}")
            self.last_state = state
        allowed = (self.mode.get() == "SIMULATION" and self.manual.get()
                   and not self.stop_event.is_set() and self.pending_command is None)
        can_strike = state in {"WAIT_STRIKE", "VERIFY_UNLOADED"} and "strike" in prompt.lower()
        self.strike_button.configure(state="normal" if allowed and can_strike else "disabled")
        self.unload_button.configure(state="normal" if allowed and "unload" in prompt.lower() else "disabled")

    def _poll(self) -> None:
        try:
            for _ in range(100):
                kind, value = self.snapshots.get_nowait()
                if kind == "snapshot":
                    self._snapshot(value)
                elif kind == "finished":
                    outcome = str(value.get("outcome", value.get("result", "Session ended")))
                    directory = value.get("session_dir", self.last_snapshot.get("session_dir", ""))
                    self.result.set(f"{outcome}  •  Evidence: {directory}" if directory else outcome)
                    if outcome == "SIM_VERIFIED":
                        self.prompt.set("Simulation verified after unloading and repeated fresh strikes. Physical commissioning is still required.")
                    self._append(outcome)
                elif kind == "error":
                    self.state.set("Fault")
                    self.prompt.set(value)
                    self.result.set("FAULT • The session did not produce a verified result.")
                    self._append(value)
        except queue.Empty:
            pass
        if not self._active():
            self._set_running(False)
            if self.closing:
                self.root.destroy()
                return
        self.root.after(50, self._poll)

    def close(self) -> None:
        self.closing = True
        self.stop()
        self._set_running(True)
        self.stop_button.configure(state="disabled")
        if not self._active():
            self.root.destroy()


def launch_gui() -> None:
    root = tk.Tk()
    TunerWindow(root)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
