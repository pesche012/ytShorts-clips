from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from candidate_models import CandidateProject, load_candidate_project
from core import (
    AppCancelled,
    AppError,
    analyze_file_candidates,
    create_candidate_preview,
    delete_candidate_previews,
    encode_selected_candidates,
    run_live_edit_pipeline,
    seconds_to_timestamp,
)
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
        self.geometry("940x900")
        self.minsize(800, 780)
        self.configure(bg="#f4f6fb")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.last_output: Path | None = None
        self.current_candidate_file: Path | None = None
        self.candidate_project: CandidateProject | None = None
        self.selected_candidate_ids: set[str] = set()
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
            text="PC内の動画からのShorts作成と、生配信の不要部分を除く編集をタブで切り替えられます。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(3, 18))

        self.mode_notebook = ttk.Notebook(root)
        self.mode_notebook.pack(fill="both", expand=True)
        self.standard_tab = ttk.Frame(self.mode_notebook, style="TFrame")
        self.live_tab = ttk.Frame(self.mode_notebook, style="TFrame")
        self.candidates_tab = ttk.Frame(self.mode_notebook, style="TFrame")
        self.mode_notebook.add(self.standard_tab, text="動画ファイル・Shorts")
        self.mode_notebook.add(self.candidates_tab, text="切り抜き候補")
        self.mode_notebook.add(self.live_tab, text="生配信編集")

        card = ttk.Frame(self.standard_tab, style="Card.TFrame", padding=22)
        card.pack(fill="x")

        ttk.Label(card, text="元動画ファイル", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.video_file_var = tk.StringVar()
        self.video_file_entry = ttk.Entry(card, textvariable=self.video_file_var)
        self.video_file_entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 15))
        ttk.Button(card, text="動画を選択", command=self._select_video_file).grid(
            row=1, column=3, sticky="ew", padx=(8, 0), pady=(5, 15)
        )

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
        ttk.Label(card, text="候補数", style="Card.TLabel").grid(
            row=10, column=2, columnspan=2, sticky="w", padx=(12, 0)
        )

        self.whisper_var = tk.StringVar(value="標準（small）")
        self.whisper_combo = ttk.Combobox(
            card, textvariable=self.whisper_var, state="readonly", values=("高速（tiny）", "標準（small）", "高精度（medium）")
        )
        self.whisper_combo.grid(row=11, column=0, sticky="ew", pady=(5, 0))
        self.resolution_var = tk.StringVar(value="720p（高速）")
        ttk.Combobox(
            card, textvariable=self.resolution_var, state="readonly", values=("720p（高速）", "1080p（高画質）")
        ).grid(row=11, column=1, sticky="ew", padx=(12, 0), pady=(5, 0))
        self.clip_count_var = tk.IntVar(value=7)
        ttk.Spinbox(
            card,
            textvariable=self.clip_count_var,
            from_=1,
            to=20,
            state="readonly",
            width=7,
        ).grid(row=11, column=2, columnspan=2, sticky="ew", padx=(12, 0), pady=(5, 0))
        for column in range(4):
            card.columnconfigure(column, weight=1)

        live_card = ttk.Frame(self.live_tab, style="Card.TFrame", padding=22)
        live_card.pack(fill="x")
        ttk.Label(live_card, text="生配信URL／生配信アーカイブURL", style="Card.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        self.live_url_var = tk.StringVar()
        ttk.Entry(live_card, textvariable=self.live_url_var).grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(5, 15)
        )
        ttk.Label(live_card, text="完成動画の長さ（分）", style="Card.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(live_card, text="配信中の取得時間（分）", style="Card.TLabel").grid(
            row=2, column=2, columnspan=2, sticky="w", padx=(12, 0)
        )
        self.live_target_minutes_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(
            live_card,
            textvariable=self.live_target_minutes_var,
            from_=0.5,
            to=30,
            increment=0.5,
            width=10,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 15))
        self.live_capture_minutes_var = tk.IntVar(value=30)
        ttk.Spinbox(
            live_card,
            textvariable=self.live_capture_minutes_var,
            from_=1,
            to=360,
            width=10,
        ).grid(row=3, column=2, columnspan=2, sticky="ew", padx=(12, 0), pady=(5, 15))
        ttk.Label(
            live_card,
            text="今回の編集指示（毎回入力・保存しません）",
            style="Card.TLabel",
        ).grid(row=4, column=0, columnspan=4, sticky="w")
        self.live_edit_prompt = tk.Text(
            live_card,
            height=5,
            relief="solid",
            borderwidth=1,
            bg="#ffffff",
            fg="#172033",
            insertbackground="#172033",
            font=("Yu Gothic UI", 10),
            wrap="word",
        )
        self.live_edit_prompt.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(5, 4))
        ttk.Label(
            live_card,
            text="例：無言と待ち時間を除く／勝負どころ中心／初心者にも分かる流れにする",
            style="Card.TLabel",
            foreground="#667085",
        ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(0, 14))
        self.include_greeting_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            live_card,
            text="冒頭の挨拶を入れる（なければ自然な導入から始める）",
            variable=self.include_greeting_var,
        ).grid(row=7, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.preserve_ending_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            live_card,
            text="締めを優先（指定時間を少し超えても、最後の発言や挨拶を全部入れる）",
            variable=self.preserve_ending_var,
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(0, 12))
        ttk.Label(
            live_card,
            text="配信中は現在位置から指定分だけ取得します。終了済み配信はアーカイブ全体を編集します。",
            style="Card.TLabel",
        ).grid(row=9, column=0, columnspan=4, sticky="w")
        ttk.Label(
            live_card,
            text="OpenRouter・モデル・Whisper・画質は「動画ファイル」タブの設定を使用します。",
            style="Card.TLabel",
            foreground="#667085",
        ).grid(row=10, column=0, columnspan=4, sticky="w", pady=(4, 0))
        for column in range(4):
            live_card.columnconfigure(column, weight=1)

        self._build_candidates_ui()

        action_row = ttk.Frame(root)
        action_row.pack(fill="x", pady=(18, 10))
        self.start_button = ttk.Button(action_row, text="候補を解析", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(
            action_row,
            text="キャンセル",
            command=self._cancel_current,
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=(10, 0))
        self.open_button = ttk.Button(action_row, text="出力フォルダーを開く", command=self._open_output, state="disabled")
        self.open_button.pack(side="left", padx=(10, 0))
        self.mode_notebook.bind("<<NotebookTabChanged>>", self._on_mode_changed)

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 8))
        self.status_var = tk.StringVar(value="動画ファイルを選び、OpenRouter APIキーを入力してください。")
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

    def _build_candidates_ui(self) -> None:
        card = ttk.Frame(self.candidates_tab, style="Card.TFrame", padding=16)
        card.pack(fill="both", expand=True)

        header = ttk.Frame(card, style="Card.TFrame")
        header.pack(fill="x")
        self.candidate_summary_var = tk.StringVar(value="動画ファイルを解析すると、ここに採点済み候補が表示されます。")
        ttk.Label(header, textvariable=self.candidate_summary_var, style="Card.TLabel").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(header, text="保存済み候補を開く", command=self._open_candidate_file).pack(side="right")

        sort_row = ttk.Frame(card, style="Card.TFrame")
        sort_row.pack(fill="x", pady=(10, 6))
        ttk.Label(sort_row, text="並び順", style="Card.TLabel").pack(side="left")
        self.candidate_sort_var = tk.StringVar(value="スコアの高い順")
        sort_combo = ttk.Combobox(
            sort_row,
            textvariable=self.candidate_sort_var,
            state="readonly",
            width=18,
            values=("スコアの高い順", "スコアの低い順"),
        )
        sort_combo.pack(side="left", padx=(8, 16))
        sort_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_candidate_tree())
        self.selected_count_var = tk.StringVar(value="選択: 0本")
        ttk.Label(sort_row, textvariable=self.selected_count_var, style="Card.TLabel").pack(side="left")

        tree_frame = ttk.Frame(card, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True)
        columns = ("checked", "score", "title", "time", "decision")
        self.candidate_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=9,
        )
        self.candidate_tree.heading("checked", text="選択")
        self.candidate_tree.heading("score", text="点数")
        self.candidate_tree.heading("title", text="タイトル候補")
        self.candidate_tree.heading("time", text="開始 ～ 終了")
        self.candidate_tree.heading("decision", text="状態")
        self.candidate_tree.column("checked", width=48, anchor="center", stretch=False)
        self.candidate_tree.column("score", width=58, anchor="center", stretch=False)
        self.candidate_tree.column("title", width=360, anchor="w")
        self.candidate_tree.column("time", width=180, anchor="center", stretch=False)
        self.candidate_tree.column("decision", width=72, anchor="center", stretch=False)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=tree_scroll.set)
        self.candidate_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.candidate_tree.bind("<<TreeviewSelect>>", self._show_candidate_details)
        self.candidate_tree.bind("<Button-1>", self._candidate_click, add="+")
        self.candidate_tree.bind("<Double-1>", self._toggle_candidate_from_event)
        self.candidate_tree.bind("<space>", self._toggle_focused_candidate)

        selection_row = ttk.Frame(card, style="Card.TFrame")
        selection_row.pack(fill="x", pady=(8, 6))
        ttk.Button(selection_row, text="80点以上", command=lambda: self._select_by_score(80)).pack(side="left")
        ttk.Button(selection_row, text="70点以上", command=lambda: self._select_by_score(70)).pack(side="left", padx=(6, 0))
        ttk.Button(selection_row, text="すべて選択", command=self._select_all_candidates).pack(side="left", padx=(6, 0))
        ttk.Button(selection_row, text="すべて解除", command=self._clear_candidate_selection).pack(side="left", padx=(6, 12))
        ttk.Label(selection_row, text="基準", style="Card.TLabel").pack(side="left")
        self.score_threshold_var = tk.IntVar(value=80)
        ttk.Spinbox(
            selection_row,
            textvariable=self.score_threshold_var,
            from_=0,
            to=100,
            width=5,
        ).pack(side="left", padx=(5, 4))
        ttk.Button(selection_row, text="点以上を選択", command=self._select_custom_threshold).pack(side="left")

        action_row = ttk.Frame(card, style="Card.TFrame")
        action_row.pack(fill="x", pady=(0, 8))
        self.preview_button = ttk.Button(
            action_row,
            text="選択中の候補を低画質プレビュー",
            command=self._preview_candidate,
            state="disabled",
        )
        self.preview_button.pack(side="left")
        self.delete_previews_button = ttk.Button(
            action_row,
            text="プレビューを削除",
            command=self._delete_previews,
            state="disabled",
        )
        self.delete_previews_button.pack(side="left", padx=(8, 0))
        self.candidate_encode_button = ttk.Button(
            action_row,
            text="チェックした候補だけ動画にする",
            command=self._start_candidate_encoding,
            state="disabled",
        )
        self.candidate_encode_button.pack(side="left", padx=(8, 0))

        self.candidate_details = tk.Text(
            card,
            height=6,
            relief="solid",
            borderwidth=1,
            bg="#ffffff",
            fg="#344054",
            font=("Yu Gothic UI", 9),
            wrap="word",
            state="disabled",
        )
        self.candidate_details.pack(fill="x")

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
            try:
                if "live_target_minutes" in data:
                    target_minutes = float(data["live_target_minutes"])
                else:
                    target_minutes = float(data.get("live_target_seconds", 60)) / 60
                capture_minutes = int(data.get("live_capture_minutes", 30))
            except (TypeError, ValueError):
                target_minutes, capture_minutes = 1.0, 30
            self.live_target_minutes_var.set(min(30, max(0.5, target_minutes)))
            self.live_capture_minutes_var.set(min(360, max(1, capture_minutes)))
            self.preserve_ending_var.set(bool(data.get("preserve_live_ending", True)))
            self.include_greeting_var.set(bool(data.get("include_live_greeting", True)))
        except SecureStorageError as exc:
            self.after(100, lambda message=str(exc): messagebox.showerror(APP_NAME, message))

    def _save_settings(self) -> None:
        try:
            live_target_minutes = float(self.live_target_minutes_var.get())
        except (tk.TclError, TypeError, ValueError):
            live_target_minutes = 1.0
        data = {
            "whisper": self.whisper_var.get(),
            "resolution": self.resolution_var.get(),
            "clip_count": self.clip_count_var.get(),
            "live_target_minutes": live_target_minutes,
            "live_capture_minutes": self.live_capture_minutes_var.get(),
            "preserve_live_ending": self.preserve_ending_var.get(),
            "include_live_greeting": self.include_greeting_var.get(),
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

    def _select_video_file(self) -> None:
        videos_dir = Path.home() / "Videos"
        selected = filedialog.askopenfilename(
            title="Shortsにする元動画を選択",
            initialdir=str(videos_dir if videos_dir.exists() else Path.home()),
            filetypes=(
                ("動画ファイル", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.wmv *.flv *.ts *.mts *.m2ts"),
                ("MP4動画", "*.mp4"),
                ("すべてのファイル", "*.*"),
            ),
        )
        if selected:
            self.video_file_var.set(selected)
            self.status_var.set("動画を選択しました。APIキーとモデルを確認して候補を解析してください。")

    def _open_candidate_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="保存済みの切り抜き候補を開く",
            initialdir=str(OUTPUT_DIR if OUTPUT_DIR.exists() else APP_DIR),
            filetypes=(("切り抜き候補", "*.json"), ("すべてのファイル", "*.*")),
        )
        if not selected:
            return
        try:
            project = load_candidate_project(Path(selected))
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self._set_candidate_project(Path(selected), project, select_recommended=False)
        self.last_output = Path(selected).parent
        self.open_button.configure(state="normal")
        self.mode_notebook.select(self.candidates_tab)

    def _set_candidate_project(
        self,
        candidate_file: Path,
        project: CandidateProject,
        select_recommended: bool = True,
    ) -> None:
        self.current_candidate_file = candidate_file
        self.candidate_project = project
        if select_recommended:
            self.selected_candidate_ids = {
                item.candidate_id for item in project.candidates if item.score >= 80
            }
        else:
            adopted = {
                item.candidate_id for item in project.candidates if item.decision == "adopted"
            }
            self.selected_candidate_ids = adopted
        self.candidate_summary_var.set(
            f"{project.video_title}｜候補 {len(project.candidates)}本｜採点: {project.scoring_profile_id}"
        )
        self._refresh_candidate_tree()
        has_candidates = bool(project.candidates)
        self.preview_button.configure(state="normal" if has_candidates else "disabled")
        self.delete_previews_button.configure(state="normal" if has_candidates else "disabled")
        self.candidate_encode_button.configure(state="normal" if self.selected_candidate_ids else "disabled")
        self.status_var.set("候補を確認し、動画にする項目をチェックしてください。")

    def _candidate_by_id(self, candidate_id: str):
        if not self.candidate_project:
            return None
        return next(
            (item for item in self.candidate_project.candidates if item.candidate_id == candidate_id),
            None,
        )

    @staticmethod
    def _candidate_time(value: float) -> str:
        return seconds_to_timestamp(value).split(".", 1)[0]

    def _refresh_candidate_tree(self) -> None:
        if not hasattr(self, "candidate_tree"):
            return
        focused = self.candidate_tree.focus()
        children = self.candidate_tree.get_children()
        if children:
            self.candidate_tree.delete(*children)
        if not self.candidate_project:
            self.selected_count_var.set("選択: 0本")
            return
        reverse = self.candidate_sort_var.get() != "スコアの低い順"
        candidates = sorted(
            self.candidate_project.candidates,
            key=lambda item: (item.score, -item.start),
            reverse=reverse,
        )
        decision_labels = {"adopted": "採用済み", "rejected": "未採用", "pending": "未判断"}
        for item in candidates:
            checked = "✓" if item.candidate_id in self.selected_candidate_ids else ""
            time_text = f"{self._candidate_time(item.start)} ～ {self._candidate_time(item.end)}"
            self.candidate_tree.insert(
                "",
                "end",
                iid=item.candidate_id,
                values=(
                    checked,
                    f"{round(item.score)}点",
                    item.title,
                    time_text,
                    decision_labels.get(item.decision, item.decision),
                ),
            )
        if focused and self.candidate_tree.exists(focused):
            self.candidate_tree.selection_set(focused)
            self.candidate_tree.focus(focused)
        self._update_selected_count()

    def _update_selected_count(self) -> None:
        count = len(self.selected_candidate_ids)
        self.selected_count_var.set(f"選択: {count}本")
        if hasattr(self, "candidate_encode_button"):
            self.candidate_encode_button.configure(state="normal" if count else "disabled")

    def _show_candidate_details(self, _event: object | None = None) -> None:
        selection = self.candidate_tree.selection()
        item = self._candidate_by_id(selection[0]) if selection else None
        lines: list[str] = []
        if item:
            lines.append(f"{round(item.score)}点｜{item.title}")
            lines.append(f"総合理由: {item.score_reason}")
            lines.append(
                "評価: " + " / ".join(
                    f"{detail.label or key} {round(detail.score)}点（{detail.reason}）"
                    for key, detail in item.criteria.items()
                )
            )
            lines.append(f"発言内容: {item.transcript or '文字起こしなし'}")
        self.candidate_details.configure(state="normal")
        self.candidate_details.delete("1.0", "end")
        self.candidate_details.insert("1.0", "\n".join(lines))
        self.candidate_details.configure(state="disabled")

    def _toggle_candidate(self, candidate_id: str) -> None:
        if not self._candidate_by_id(candidate_id):
            return
        if candidate_id in self.selected_candidate_ids:
            self.selected_candidate_ids.remove(candidate_id)
        else:
            self.selected_candidate_ids.add(candidate_id)
        self._refresh_candidate_tree()
        self.candidate_tree.selection_set(candidate_id)
        self.candidate_tree.focus(candidate_id)
        self._show_candidate_details()

    def _toggle_candidate_from_event(self, event: tk.Event) -> None:
        if self.candidate_tree.identify_column(event.x) == "#1":
            return
        candidate_id = self.candidate_tree.identify_row(event.y)
        if candidate_id:
            self._toggle_candidate(candidate_id)

    def _candidate_click(self, event: tk.Event) -> str | None:
        if self.candidate_tree.identify_column(event.x) != "#1":
            return None
        candidate_id = self.candidate_tree.identify_row(event.y)
        if candidate_id:
            self._toggle_candidate(candidate_id)
            return "break"
        return None

    def _toggle_focused_candidate(self, _event: object | None = None) -> str:
        candidate_id = self.candidate_tree.focus()
        if candidate_id:
            self._toggle_candidate(candidate_id)
        return "break"

    def _select_by_score(self, threshold: int) -> None:
        if not self.candidate_project:
            return
        self.selected_candidate_ids = {
            item.candidate_id for item in self.candidate_project.candidates if item.score >= threshold
        }
        self._refresh_candidate_tree()

    def _select_custom_threshold(self) -> None:
        try:
            threshold = int(self.score_threshold_var.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showwarning(APP_NAME, "基準点は0〜100で入力してください。")
            return
        if not 0 <= threshold <= 100:
            messagebox.showwarning(APP_NAME, "基準点は0〜100で入力してください。")
            return
        self._select_by_score(threshold)

    def _select_all_candidates(self) -> None:
        if self.candidate_project:
            self.selected_candidate_ids = {
                item.candidate_id for item in self.candidate_project.candidates
            }
            self._refresh_candidate_tree()

    def _clear_candidate_selection(self) -> None:
        self.selected_candidate_ids.clear()
        self._refresh_candidate_tree()

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

    def _cancel_current(self) -> None:
        if str(self.cancel_button["state"]) == "disabled":
            return
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_var.set("キャンセルしています…")
        self._append_log("キャンセルを受け付けました。安全に処理を停止しています。")

    def _on_mode_changed(self, _event: object | None = None) -> None:
        if self.mode_notebook.select() == str(self.live_tab):
            self.start_button.configure(text="生配信の不要部分を除いて作成")
            self.status_var.set("生配信URLと完成動画の長さを入力してください。")
        elif self.mode_notebook.select() == str(self.candidates_tab):
            self.start_button.configure(text="チェックした候補だけ動画にする")
            self.status_var.set("候補をチェックしてから、選択した候補だけ動画にできます。")
        else:
            self.start_button.configure(text="候補を解析")
            self.status_var.set("動画ファイルを選び、OpenRouter APIキーを入力してください。")

    def _start(self) -> None:
        if self.mode_notebook.select() == str(self.live_tab):
            self._start_live_edit()
            return
        if self.mode_notebook.select() == str(self.candidates_tab):
            self._start_candidate_encoding()
            return
        video_file = self.video_file_var.get().strip()
        key = self.key_var.get().strip()
        provider_id = self._provider_id()
        model = self._selected_model_id()
        highlight_prompt = self.highlight_prompt.get("1.0", "end-1c").strip()
        try:
            clip_count = int(self.clip_count_var.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showwarning(APP_NAME, "候補数は1〜20本で選んでください。")
            return
        if not 1 <= clip_count <= 20:
            messagebox.showwarning(APP_NAME, "候補数は1〜20本で選んでください。")
            return
        if not video_file or not key or not model:
            messagebox.showwarning(APP_NAME, "動画ファイル、OpenRouter APIキー、モデルを入力してください。")
            return
        if not Path(video_file).is_file():
            messagebox.showwarning(APP_NAME, "選択した動画ファイルが見つかりません。もう一度選択してください。")
            return
        try:
            self._save_settings()
        except SecureStorageError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.start_button.configure(state="disabled")
        self.cancel_event.clear()
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress["value"] = 0
        self._append_log("動画ファイルの文字起こしと候補採点を開始しました。")

        whisper = {"高速（tiny）": "tiny", "標準（small）": "small", "高精度（medium）": "medium"}[
            self.whisper_var.get()
        ]
        def callback(message: str, progress: float | None) -> None:
            self.events.put(("progress", (message, progress)))

        def worker() -> None:
            try:
                result = analyze_file_candidates(
                    video_file=Path(video_file),
                    api_key=key,
                    output_root=OUTPUT_DIR,
                    whisper_model=whisper,
                    llm_provider=provider_id,
                    llm_model=model,
                    highlight_prompt=highlight_prompt,
                    clip_count=clip_count,
                    callback=callback,
                    cancel_check=self.cancel_event.is_set,
                )
                self.events.put(("candidates_ready", result))
            except AppCancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _start_candidate_encoding(self) -> None:
        if not self.current_candidate_file or not self.candidate_project:
            messagebox.showwarning(APP_NAME, "先に動画を解析するか、保存済み候補を開いてください。")
            return
        if not self.selected_candidate_ids:
            messagebox.showwarning(APP_NAME, "動画にする候補を1本以上チェックしてください。")
            return
        candidate_file = self.current_candidate_file
        selected_ids = sorted(self.selected_candidate_ids)
        resolution = "1080p" if self.resolution_var.get().startswith("1080") else "720p"
        self.start_button.configure(state="disabled")
        self.candidate_encode_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.delete_previews_button.configure(state="disabled")
        self.cancel_event.clear()
        self.cancel_button.configure(state="normal")
        self.progress["value"] = 0
        self._append_log(f"選択した{len(selected_ids)}本だけのエンコードを開始しました。")

        def callback(message: str, progress: float | None) -> None:
            self.events.put(("progress", (message, progress)))

        def worker() -> None:
            try:
                result = encode_selected_candidates(
                    candidate_file=candidate_file,
                    selected_candidate_ids=selected_ids,
                    resolution=resolution,
                    callback=callback,
                    cancel_check=self.cancel_event.is_set,
                )
                self.events.put(("done", result))
            except AppCancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _preview_candidate(self) -> None:
        if not self.current_candidate_file:
            messagebox.showwarning(APP_NAME, "先に切り抜き候補を開いてください。")
            return
        selection = self.candidate_tree.selection()
        if not selection:
            messagebox.showwarning(APP_NAME, "一覧からプレビューする候補を1つ選んでください。")
            return
        candidate_file = self.current_candidate_file
        candidate_id = selection[0]
        self.start_button.configure(state="disabled")
        self.candidate_encode_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.delete_previews_button.configure(state="disabled")
        self.cancel_event.clear()
        self.cancel_button.configure(state="normal")
        self.status_var.set("低画質プレビューを準備しています…")
        self._append_log("選択中の候補の低画質プレビューを準備しています。")

        def worker() -> None:
            try:
                path = create_candidate_preview(
                    candidate_file,
                    candidate_id,
                    self.cancel_event.is_set,
                )
                self.events.put(("preview_ready", path))
            except AppCancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _delete_previews(self) -> None:
        if not self.current_candidate_file:
            messagebox.showwarning(APP_NAME, "先に切り抜き候補を開いてください。")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "この候補用に作成した低画質プレビューをすべて削除しますか？\n"
            "候補データと完成動画は削除されません。",
        ):
            return
        try:
            file_count, total_bytes = delete_candidate_previews(self.current_candidate_file)
        except AppError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        if file_count == 0:
            self.status_var.set("削除するプレビューはありませんでした。")
            messagebox.showinfo(APP_NAME, "削除するプレビューはありませんでした。")
            return
        size_mb = total_bytes / (1024 * 1024)
        self.status_var.set(f"プレビュー{file_count}本を削除しました。")
        self._append_log(f"低画質プレビュー{file_count}本（{size_mb:.1f} MB）を削除しました。")
        messagebox.showinfo(
            APP_NAME,
            f"プレビュー{file_count}本を削除しました。\n空き容量: 約{size_mb:.1f} MB",
        )

    def _start_live_edit(self) -> None:
        url = self.live_url_var.get().strip()
        key = self.key_var.get().strip()
        provider_id = self._provider_id()
        model = self._selected_model_id()
        edit_prompt = self.live_edit_prompt.get("1.0", "end-1c").strip()
        preserve_ending = self.preserve_ending_var.get()
        include_greeting = self.include_greeting_var.get()
        try:
            target_minutes = float(self.live_target_minutes_var.get())
            capture_minutes = int(self.live_capture_minutes_var.get())
        except (tk.TclError, TypeError, ValueError):
            messagebox.showwarning(APP_NAME, "完成動画の長さと取得時間を数字で入力してください。")
            return
        if not 0.5 <= target_minutes <= 30:
            messagebox.showwarning(APP_NAME, "完成動画の長さは0.5〜30分で入力してください。")
            return
        if not 1 <= capture_minutes <= 360:
            messagebox.showwarning(APP_NAME, "配信中の取得時間は1〜360分で入力してください。")
            return
        if not url or not key or not model:
            messagebox.showwarning(
                APP_NAME,
                "生配信URLを入力し、動画ファイルタブでOpenRouter APIキーとモデルを設定してください。",
            )
            return
        target_seconds = int(round(target_minutes * 60))
        try:
            self._save_settings()
        except SecureStorageError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.start_button.configure(state="disabled")
        self.cancel_event.clear()
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress["value"] = 0
        self._append_log("生配信編集を開始しました。")
        whisper = {"高速（tiny）": "tiny", "標準（small）": "small", "高精度（medium）": "medium"}[
            self.whisper_var.get()
        ]
        resolution = "1080p" if self.resolution_var.get().startswith("1080") else "720p"
        def callback(message: str, progress: float | None) -> None:
            self.events.put(("progress", (message, progress)))

        def worker() -> None:
            try:
                result = run_live_edit_pipeline(
                    url=url,
                    api_key=key,
                    output_root=OUTPUT_DIR,
                    target_seconds=target_seconds,
                    live_capture_minutes=capture_minutes,
                    whisper_model=whisper,
                    llm_provider=provider_id,
                    llm_model=model,
                    edit_prompt=edit_prompt,
                    preserve_ending=preserve_ending,
                    include_greeting=include_greeting,
                    resolution=resolution,
                    callback=callback,
                    cancel_check=self.cancel_event.is_set,
                )
                self.events.put(("done", result))
            except AppCancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _restore_controls_after_worker(self) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        has_candidates = bool(self.candidate_project and self.candidate_project.candidates)
        self.preview_button.configure(state="normal" if has_candidates else "disabled")
        self.delete_previews_button.configure(state="normal" if has_candidates else "disabled")
        self.candidate_encode_button.configure(
            state="normal" if has_candidates and self.selected_candidate_ids else "disabled"
        )

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
                elif event == "candidates_ready":
                    result = payload  # type: ignore[assignment]
                    candidate_file = Path(result["candidate_file"])
                    try:
                        project = load_candidate_project(candidate_file)
                    except ValueError as exc:
                        self.events.put(("error", AppError(str(exc))))
                        continue
                    self.last_output = Path(result["output_dir"])
                    self._set_candidate_project(candidate_file, project, select_recommended=True)
                    self._restore_controls_after_worker()
                    self.open_button.configure(state="normal")
                    self.progress["value"] = 100
                    self.mode_notebook.select(self.candidates_tab)
                    selected = len(self.selected_candidate_ids)
                    self.status_var.set(
                        f"候補{len(project.candidates)}本を採点しました。80点以上を{selected}本選択しています。"
                    )
                    messagebox.showinfo(
                        APP_NAME,
                        "候補の採点が完了しました。\nまだ動画は作成していません。\n\n"
                        "内容を確認し、動画にする候補だけチェックしてください。",
                    )
                elif event == "preview_ready":
                    preview_path = Path(payload)  # type: ignore[arg-type]
                    self._restore_controls_after_worker()
                    self.status_var.set("低画質プレビューを開きました。")
                    self._open_media_file(preview_path)
                elif event == "done":
                    result = payload  # type: ignore[assignment]
                    self.last_output = Path(result["output_dir"])
                    self._restore_controls_after_worker()
                    self.open_button.configure(state="normal")
                    self.progress["value"] = 100
                    if result.get("mode") == "live_edit":
                        output_seconds = float(result.get("output_duration") or result["target_duration"])
                        output_minutes = output_seconds / 60
                        duration_label = f"{output_minutes:.1f}".rstrip("0").rstrip(".") + "分"
                        self.status_var.set(f"完成しました。{duration_label}の動画を確認できます。")
                        messagebox.showinfo(
                            APP_NAME,
                            f"{duration_label}の生配信切り抜き動画を作成しました。\n\n{self.last_output}",
                        )
                    elif result.get("mode") == "candidate_encode":
                        clip_count = int(result.get("clip_count", 0))
                        if self.current_candidate_file:
                            try:
                                refreshed = load_candidate_project(self.current_candidate_file)
                                self._set_candidate_project(
                                    self.current_candidate_file,
                                    refreshed,
                                    select_recommended=False,
                                )
                            except ValueError:
                                pass
                        self.status_var.set(f"選択した{clip_count}本の動画が完成しました。")
                        messagebox.showinfo(
                            APP_NAME,
                            f"チェックした{clip_count}本だけ動画にしました。\n\n{self.last_output}",
                        )
                    else:
                        clip_count = int(result.get("clip_count", len(result.get("highlights", []))))
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
                    self._restore_controls_after_worker()
                    message = str(payload)
                    if not isinstance(payload, (AppError, LLMProviderError)):
                        message = f"予期しないエラーが発生しました。\n{message}"
                    self.status_var.set("処理を完了できませんでした。")
                    self._append_log("エラー: " + message)
                    messagebox.showerror(APP_NAME, message)
                elif event == "cancelled":
                    self._restore_controls_after_worker()
                    self.status_var.set("処理をキャンセルしました。")
                    self._append_log("処理をキャンセルしました。途中ファイルは削除されました。")
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _open_media_file(self, target: Path) -> None:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    def _open_output(self) -> None:
        target = self.last_output or OUTPUT_DIR
        target.mkdir(parents=True, exist_ok=True)
        self._open_media_file(target)


if __name__ == "__main__":
    ShortsApp().mainloop()
