from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from core import AppError, run_pipeline
from llm_providers import LLMProviderError, available_providers, get_provider
from secure_storage import (
    SecureStorageError,
    delete_api_key,
    load_settings_and_migrate,
    set_api_key,
    write_public_settings,
)


APP_NAME = "YouTube Shorts 自動メーカー"
APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "outputs"
CONFIG_DIR = Path(os.environ.get("APPDATA", APP_DIR)) / "YouTubeShortMaker"
CONFIG_PATH = CONFIG_DIR / "settings.json"


class ShortsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x860")
        self.minsize(780, 760)
        self.configure(bg="#f4f6fb")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_output: Path | None = None
        self.providers = available_providers()
        self.provider_name_to_id = {item.display_name: item.provider_id for item in self.providers}
        self.model_display_to_id: dict[str, str] = {}
        self._build_styles()
        self._build_ui()
        self._load_settings()
        self.after(100, self._poll_events)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f4f6fb", foreground="#172033", font=("Yu Gothic UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#172033", font=("Yu Gothic UI", 10))
        style.configure("Title.TLabel", background="#f4f6fb", foreground="#121a2b", font=("Yu Gothic UI", 23, "bold"))
        style.configure("Sub.TLabel", background="#f4f6fb", foreground="#667085", font=("Yu Gothic UI", 10))
        style.configure("Accent.TButton", font=("Yu Gothic UI", 12, "bold"), padding=(16, 12))
        style.configure("TButton", font=("Yu Gothic UI", 10), padding=(10, 7))
        style.configure("TEntry", padding=8, font=("Yu Gothic UI", 10))
        style.configure("TCombobox", padding=7, font=("Yu Gothic UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#e7eaf0", background="#ff3158")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(34, 24, 34, 28))
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="YouTube Shorts 自動メーカー", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="URLを貼るだけで、字幕取得 → AI選定 → 縦型動画の作成まで自動化します。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(3, 18))

        card = ttk.Frame(root, style="Card.TFrame", padding=22)
        card.pack(fill="x")

        ttk.Label(card, text="YouTube URL", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(card, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(5, 15))

        ttk.Label(card, text="LLM接続先", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(card, text="OpenRouter APIキー", style="Card.TLabel").grid(row=2, column=1, sticky="w", padx=(12, 0))
        self.provider_var = tk.StringVar(value="OpenRouter")
        self.provider_combo = ttk.Combobox(
            card,
            textvariable=self.provider_var,
            state="readonly",
            values=tuple(self.provider_name_to_id),
        )
        self.provider_combo.grid(row=3, column=0, sticky="ew", pady=(5, 4))
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(card, textvariable=self.key_var, show="●")
        self.key_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(5, 4))
        self.key_button = ttk.Button(card, text="APIキーを取得", command=self._open_api_key_page)
        self.key_button.grid(row=3, column=3, padx=(8, 0), sticky="ew")
        self.save_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(card, text="APIキーをこのPCに保存", variable=self.save_key_var).grid(
            row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(0, 12)
        )

        ttk.Label(card, text="使用するモデル（選択またはモデルIDを直接入力）", style="Card.TLabel").grid(
            row=5, column=0, columnspan=3, sticky="w"
        )
        self.model_var = tk.StringVar(value="openrouter/auto")
        self.model_combo = ttk.Combobox(card, textvariable=self.model_var, state="normal", values=("openrouter/auto",))
        self.model_combo.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(5, 14))
        self.models_button = ttk.Button(card, text="モデル一覧を更新", command=self._refresh_models)
        self.models_button.grid(row=6, column=3, padx=(8, 0), pady=(5, 14), sticky="ew")

        ttk.Label(
            card,
            text="今回の切り抜き指示（毎回入力・保存しません）",
            style="Card.TLabel",
        ).grid(row=7, column=0, columnspan=4, sticky="w")
        self.highlight_prompt = tk.Text(
            card,
            height=4,
            relief="solid",
            borderwidth=1,
            bg="#ffffff",
            fg="#172033",
            insertbackground="#172033",
            font=("Yu Gothic UI", 10),
            wrap="word",
        )
        self.highlight_prompt.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(5, 4))
        ttk.Label(
            card,
            text="例：テンポ重視／驚きの発言を優先／30秒前後／初心者向けの場面",
            style="Card.TLabel",
            foreground="#667085",
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(0, 14))

        ttk.Label(card, text="Whisper精度", style="Card.TLabel").grid(row=10, column=0, sticky="w")
        ttk.Label(card, text="動画サイズ", style="Card.TLabel").grid(row=10, column=1, sticky="w", padx=(12, 0))
        ttk.Label(card, text="ログイン動画", style="Card.TLabel").grid(row=10, column=2, sticky="w", padx=(12, 0))
        ttk.Label(card, text="作成本数", style="Card.TLabel").grid(row=10, column=3, sticky="w", padx=(12, 0))

        self.whisper_var = tk.StringVar(value="標準（small）")
        self.whisper_combo = ttk.Combobox(
            card, textvariable=self.whisper_var, state="readonly", values=("高速（tiny）", "標準（small）", "高精度（medium）")
        )
        self.whisper_combo.grid(row=11, column=0, sticky="ew", pady=(5, 0))
        self.resolution_var = tk.StringVar(value="720p（高速）")
        ttk.Combobox(
            card, textvariable=self.resolution_var, state="readonly", values=("720p（高速）", "1080p（高画質）")
        ).grid(row=11, column=1, sticky="ew", padx=(12, 0), pady=(5, 0))
        self.browser_var = tk.StringVar(value="使用しない")
        ttk.Combobox(
            card, textvariable=self.browser_var, state="readonly", values=("使用しない", "Edge", "Chrome")
        ).grid(row=11, column=2, sticky="ew", padx=(12, 0), pady=(5, 0))
        self.clip_count_var = tk.IntVar(value=7)
        ttk.Spinbox(
            card,
            textvariable=self.clip_count_var,
            from_=1,
            to=20,
            state="readonly",
            width=7,
        ).grid(row=11, column=3, sticky="ew", padx=(12, 0), pady=(5, 0))
        for column in range(4):
            card.columnconfigure(column, weight=1)

        action_row = ttk.Frame(root)
        action_row.pack(fill="x", pady=(18, 10))
        self.start_button = ttk.Button(action_row, text="Shortsを作成", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.open_button = ttk.Button(action_row, text="出力フォルダーを開く", command=self._open_output, state="disabled")
        self.open_button.pack(side="left", padx=(10, 0))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 8))
        self.status_var = tk.StringVar(value="URLとOpenRouter APIキーを入力してください。")
        ttk.Label(root, textvariable=self.status_var, style="Sub.TLabel").pack(anchor="w", pady=(0, 9))

        log_frame = ttk.Frame(root, style="Card.TFrame", padding=12)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_frame,
            height=9,
            relief="flat",
            bg="#ffffff",
            fg="#344054",
            font=("Yu Gothic UI", 9),
            wrap="word",
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(
            root,
            text="※ 自分が権利を持つ動画、または利用許可のある動画だけを処理してください。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(10, 0))

    def _load_settings(self) -> None:
        try:
            data, saved_key = load_settings_and_migrate(CONFIG_PATH)
            provider_id = data.get("llm_provider", "openrouter")
            provider = next((item for item in self.providers if item.provider_id == provider_id), self.providers[0])
            self.provider_var.set(provider.display_name)
            if saved_key:
                self.key_var.set(saved_key)
                self.save_key_var.set(True)
            self.model_var.set(data.get("llm_model", provider.default_model))
            self.whisper_var.set(data.get("whisper", self.whisper_var.get()))
            self.resolution_var.set(data.get("resolution", self.resolution_var.get()))
            try:
                saved_count = int(data.get("clip_count", 7))
            except (TypeError, ValueError):
                saved_count = 7
            self.clip_count_var.set(min(20, max(1, saved_count)))
        except SecureStorageError as exc:
            self.after(100, lambda message=str(exc): messagebox.showerror(APP_NAME, message))

    def _save_settings(self) -> None:
        data = {
            "whisper": self.whisper_var.get(),
            "resolution": self.resolution_var.get(),
            "clip_count": self.clip_count_var.get(),
            "llm_provider": self._provider_id(),
            "llm_model": self._selected_model_id(),
        }
        if self.save_key_var.get():
            set_api_key(self.key_var.get().strip())
        else:
            delete_api_key()
        write_public_settings(CONFIG_PATH, data)

    def _provider_id(self) -> str:
        return self.provider_name_to_id.get(self.provider_var.get(), "openrouter")

    def _selected_model_id(self) -> str:
        value = self.model_var.get().strip()
        return self.model_display_to_id.get(value, value) or get_provider(self._provider_id()).default_model

    def _open_api_key_page(self) -> None:
        webbrowser.open(get_provider(self._provider_id()).api_key_url)

    def _on_provider_changed(self, _event: object | None = None) -> None:
        provider = get_provider(self._provider_id())
        self.model_display_to_id.clear()
        self.model_combo.configure(values=(provider.default_model,))
        self.model_var.set(provider.default_model)

    def _refresh_models(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning(APP_NAME, "OpenRouter APIキーを入力してから更新してください。")
            return
        self.models_button.configure(state="disabled")
        self.status_var.set("OpenRouterからモデル一覧を取得しています…")
        self._append_log("モデル一覧を更新しています。")
        provider_id = self._provider_id()

        def worker() -> None:
            try:
                models = get_provider(provider_id).list_models(key)
                self.events.put(("models", models))
            except Exception as exc:
                self.events.put(("models_error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        url = self.url_var.get().strip()
        key = self.key_var.get().strip()
        provider_id = self._provider_id()
        model = self._selected_model_id()
        highlight_prompt = self.highlight_prompt.get("1.0", "end-1c").strip()
        try:
            clip_count = int(self.clip_count_var.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showwarning(APP_NAME, "作成本数は1〜20本で選んでください。")
            return
        if not 1 <= clip_count <= 20:
            messagebox.showwarning(APP_NAME, "作成本数は1〜20本で選んでください。")
            return
        if not url or not key or not model:
            messagebox.showwarning(APP_NAME, "YouTube URL、OpenRouter APIキー、モデルを入力してください。")
            return
        try:
            self._save_settings()
        except SecureStorageError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.start_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.progress["value"] = 0
        self._append_log("処理を開始しました。")

        whisper = {"高速（tiny）": "tiny", "標準（small）": "small", "高精度（medium）": "medium"}[
            self.whisper_var.get()
        ]
        resolution = "1080p" if self.resolution_var.get().startswith("1080") else "720p"
        browser = {"Edge": "edge", "Chrome": "chrome"}.get(self.browser_var.get())

        def callback(message: str, progress: float | None) -> None:
            self.events.put(("progress", (message, progress)))

        def worker() -> None:
            try:
                result = run_pipeline(
                    url=url,
                    api_key=key,
                    output_root=OUTPUT_DIR,
                    whisper_model=whisper,
                    llm_provider=provider_id,
                    llm_model=model,
                    highlight_prompt=highlight_prompt,
                    clip_count=clip_count,
                    resolution=resolution,
                    cookie_browser=browser,
                    callback=callback,
                )
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    message, value = payload  # type: ignore[misc]
                    self.status_var.set(str(message))
                    self._append_log(str(message))
                    if value is not None:
                        self.progress["value"] = float(value) * 100
                elif event == "done":
                    result = payload  # type: ignore[assignment]
                    self.last_output = Path(result["output_dir"])
                    clip_count = int(result.get("clip_count", len(result.get("highlights", []))))
                    self.start_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.progress["value"] = 100
                    self.status_var.set(f"完成しました。{clip_count}本の動画を確認できます。")
                    messagebox.showinfo(APP_NAME, f"{clip_count}本のShortsを作成しました。\n\n{self.last_output}")
                elif event == "models":
                    models = payload  # type: ignore[assignment]
                    current_id = self._selected_model_id()
                    self.model_display_to_id = {}
                    values = []
                    selected_display = None
                    for item in models:
                        display = f"{item.name}  [{item.id}]"
                        self.model_display_to_id[display] = item.id
                        values.append(display)
                        if item.id == current_id:
                            selected_display = display
                    self.model_combo.configure(values=values)
                    if selected_display:
                        self.model_var.set(selected_display)
                    self.models_button.configure(state="normal")
                    self.status_var.set(f"{len(values)}件のモデルを取得しました。")
                    self._append_log(f"モデル一覧を更新しました（{len(values)}件）。")
                elif event == "models_error":
                    self.models_button.configure(state="normal")
                    message = str(payload)
                    self.status_var.set("モデル一覧を取得できませんでした。")
                    self._append_log("モデル一覧エラー: " + message)
                    messagebox.showerror(APP_NAME, message)
                elif event == "error":
                    self.start_button.configure(state="normal")
                    message = str(payload)
                    if not isinstance(payload, (AppError, LLMProviderError)):
                        message = f"予期しないエラーが発生しました。\n{message}"
                    self.status_var.set("処理を完了できませんでした。")
                    self._append_log("エラー: " + message)
                    messagebox.showerror(APP_NAME, message)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _open_output(self) -> None:
        target = self.last_output or OUTPUT_DIR
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])


if __name__ == "__main__":
    ShortsApp().mainloop()
