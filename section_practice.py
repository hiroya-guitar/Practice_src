#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
反復練習トラッカー（CSV / 曲インデックス管理 / 小節範囲 / BPM提案 / 拍子 / メトロノーム）
- 曲登録時に拍子（例: 4/4）も保存
- BPM入力時に「試聴メトロノーム（ビープ）」でテンポ確認できる
- 練習中は常にそのテンポ音（メトロノーム）が鳴る
- Windows想定：winsound.Beep を使用

保存:
- practice_logs/songs.csv
  song_index, song_name, beats_per_bar, beat_unit, created_at
- practice_logs/sessions.csv
  session_id, timestamp_start, timestamp_end, duration_sec, song_index, song_name,
  bar_start, bar_end, bpm, reps, success, note

互換:
- 既存 songs.csv が旧形式でも、起動時に列を補完（無い行は 4/4 扱い）
"""

import csv
import os
import threading
import time
import tkinter as tk
import winsound
from dataclasses import dataclass, asdict
from datetime import datetime
from tkinter import ttk, messagebox

LOG_DIR = "practice_logs"
SONGS_CSV = os.path.join(LOG_DIR, "songs.csv")
SESSIONS_CSV = os.path.join(LOG_DIR, "sessions.csv")

SUCCESS_THRESHOLD = 0.90

SONGS_FIELDS = ["song_index", "song_name", "beats_per_bar", "beat_unit", "created_at"]
SESSIONS_FIELDS = [
    "session_id",
    "timestamp_start",
    "timestamp_end",
    "duration_sec",
    "song_index",
    "song_name",
    "bar_start",
    "bar_end",
    "bpm",
    "reps",
    "success",
    "note",
]


def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_session_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def safe_int(x, default=0) -> int:
    try:
        return int(str(x).strip())
    except Exception:
        return default


def overlap(a1: int, a2: int, b1: int, b2: int) -> bool:
    return max(a1, b1) <= min(a2, b2)


def parse_timestamp(ts: str) -> datetime | None:
    ts = (ts or "").strip()
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except Exception:
            pass
    return None


def read_csv_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r)


def write_csv_rows(path: str, fieldnames: list[str], rows: list[dict]):
    ensure_log_dir()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            out = {k: row.get(k, "") for k in fieldnames}
            w.writerow(out)


def append_csv(path: str, fieldnames: list[str], row: dict):
    ensure_log_dir()
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        out = {k: row.get(k, "") for k in fieldnames}
        w.writerow(out)


def ensure_songs_schema():
    """
    songs.csv が旧形式でも、beats_per_bar/beat_unit列を追加して補完する。
    既存ヘッダが不足している場合は、同ファイルを新ヘッダで書き直す。
    """
    ensure_log_dir()
    if not os.path.exists(SONGS_CSV):
        write_csv_rows(SONGS_CSV, SONGS_FIELDS, [])
        return

    # 現行ヘッダを読み取る
    with open(SONGS_CSV, "r", newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r, [])

    header_set = set(header)
    need_rewrite = any(col not in header_set for col in SONGS_FIELDS)

    rows = read_csv_rows(SONGS_CSV)

    # 行の補完（無ければ 4/4）
    for row in rows:
        if not (row.get("beats_per_bar") or "").strip():
            row["beats_per_bar"] = "4"
        if not (row.get("beat_unit") or "").strip():
            row["beat_unit"] = "4"
        # song_index/song_name/created_at が欠けていても空のままにする（ユーザーが手修正している可能性）
        if not (row.get("created_at") or "").strip():
            row["created_at"] = now_iso()

    if need_rewrite:
        write_csv_rows(SONGS_CSV, SONGS_FIELDS, rows)


def ensure_sessions_schema():
    """sessions.csv がなければ新規作成（既存の互換変換は今回は行わない想定）"""
    ensure_log_dir()
    if not os.path.exists(SESSIONS_CSV):
        write_csv_rows(SESSIONS_CSV, SESSIONS_FIELDS, [])


class BeepMetronome:
    """
    winsound.Beep を使った簡易メトロノーム。
    - 1拍目（小節頭）は高い音、他は低い音
    - start/stop でスレッド制御
    """

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.bpm = 120
        self.beats_per_bar = 4

        self.freq_accent = 1100
        self.freq_normal = 800
        self.beep_ms = 30

    def update(self, bpm: int, beats_per_bar: int):
        with self._lock:
            self.bpm = max(1, min(400, int(bpm)))
            self.beats_per_bar = max(1, min(32, int(beats_per_bar)))

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, bpm: int, beats_per_bar: int):
        self.update(bpm, beats_per_bar)
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        beat = 0
        next_t = time.perf_counter()
        while not self._stop.is_set():
            with self._lock:
                bpm = self.bpm
                bpb = self.beats_per_bar

            interval = 60.0 / max(1, bpm)

            now = time.perf_counter()
            if now < next_t:
                time.sleep(min(0.01, next_t - now))
                continue

            freq = self.freq_accent if (beat % bpb == 0) else self.freq_normal
            try:
                winsound.Beep(freq, self.beep_ms)
            except Exception:
                pass

            beat = (beat + 1) % bpb
            next_t += interval


@dataclass
class SessionRow:
    session_id: str
    timestamp_start: str
    timestamp_end: str
    duration_sec: float
    song_index: int
    song_name: str
    bar_start: int
    bar_end: int
    bpm: int
    reps: int
    success: int
    note: str


class PracticeTracker(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("反復練習トラッカー（拍子 / メトロノーム付き）")
        self.geometry("1100x720")
        self.minsize(980, 640)

        ensure_songs_schema()
        ensure_sessions_schema()

        # songs
        self.songs: list[dict] = []
        self.filtered_song_indices: list[int | None] = []
        self.load_songs()

        # sessions cache
        self.sessions: list[dict] = []
        self.load_sessions()

        # selection state
        self.selected_song_index = None
        self.selected_song_name = None
        self.selected_beats_per_bar = 4
        self.selected_beat_unit = 4

        # session runtime state
        self.active = False
        self.session_id = None
        self.t_start_iso = None
        self.t_start_epoch = None
        self.reps = 0
        self.success = 0
        self.locked_bar_start = None
        self.locked_bar_end = None
        self.locked_bpm = None

        # metronome
        self.metro = BeepMetronome()

        self._build_ui()
        self.refresh_song_list()
        self._set_idle_state()
        self._update_suggestion()

    # ---------- UI ----------
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="both", expand=True)

        left = ttk.LabelFrame(top, text="曲（インデックス表）", padding=10)
        left.pack(side="left", fill="both", expand=False, padx=(0, 10))

        right = ttk.LabelFrame(top, text="練習セッション", padding=10)
        right.pack(side="left", fill="both", expand=True)

        bottom = ttk.LabelFrame(root, text="表示ログ（保存は「終了して保存」のみ）", padding=10)
        bottom.pack(fill="both", expand=True, pady=(10, 0))

        # ----- left: songs -----
        search_row = ttk.Frame(left)
        search_row.pack(fill="x")

        ttk.Label(search_row, text="検索").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.search_var, width=22)
        search_entry.pack(side="left", padx=(6, 6))
        search_entry.bind("<KeyRelease>", lambda e: self.refresh_song_list())

        ttk.Button(search_row, text="更新", command=self.refresh_song_list).pack(side="left")
        ttk.Button(search_row, text="保存先", command=self.show_paths).pack(side="left", padx=(8, 0))

        list_row = ttk.Frame(left)
        list_row.pack(fill="both", expand=True, pady=(10, 0))

        self.song_listbox = tk.Listbox(list_row, height=18)
        self.song_listbox.pack(side="left", fill="both", expand=True)
        self.song_listbox.bind("<<ListboxSelect>>", lambda e: self.on_song_select())

        sb = ttk.Scrollbar(list_row, orient="vertical", command=self.song_listbox.yview)
        sb.pack(side="right", fill="y")
        self.song_listbox.configure(yscrollcommand=sb.set)

        add_row = ttk.Frame(left)
        add_row.pack(fill="x", pady=(10, 0))

        ttk.Label(add_row, text="新規追加 曲名").pack(side="left")
        self.new_song_name_var = tk.StringVar()
        ttk.Entry(add_row, textvariable=self.new_song_name_var).pack(side="left", padx=(6, 6), fill="x", expand=True)

        ttk.Label(add_row, text="拍子").pack(side="left", padx=(10, 0))
        self.new_bpb_var = tk.StringVar(value="4")
        self.new_bu_var = tk.StringVar(value="4")
        bpb = ttk.Combobox(add_row, textvariable=self.new_bpb_var, width=3,
                           values=[str(i) for i in range(1, 13)], state="readonly")
        bpb.pack(side="left", padx=(4, 2))
        ttk.Label(add_row, text="/").pack(side="left")
        bu = ttk.Combobox(add_row, textvariable=self.new_bu_var, width=3,
                          values=["2", "4", "8"], state="readonly")
        bu.pack(side="left", padx=(2, 6))

        ttk.Button(add_row, text="追加", command=self.add_song).pack(side="left")

        pick_row = ttk.Frame(left)
        pick_row.pack(fill="x", pady=(10, 0))

        ttk.Label(pick_row, text="インデックスで選択").pack(side="left")
        self.pick_index_var = tk.StringVar()
        pick_entry = ttk.Entry(pick_row, textvariable=self.pick_index_var, width=8)
        pick_entry.pack(side="left", padx=(6, 6))
        pick_entry.bind("<Return>", lambda e: self.pick_by_index())
        ttk.Button(pick_row, text="選択", command=self.pick_by_index).pack(side="left")

        self.selected_label = ttk.Label(left, text="選択中: （なし）", font=("Helvetica", 11, "bold"))
        self.selected_label.pack(anchor="w", pady=(10, 0))

        # ----- right: inputs -----
        form = ttk.Frame(right)
        form.pack(fill="x")

        row1 = ttk.Frame(form)
        row1.pack(fill="x", pady=(0, 8))

        ttk.Label(row1, text="開始小節").pack(side="left")
        self.bar_start_var = tk.StringVar()
        self.bar_start_entry = ttk.Entry(row1, textvariable=self.bar_start_var, width=8)
        self.bar_start_entry.pack(side="left", padx=(6, 12))
        self.bar_start_entry.bind("<KeyRelease>", lambda e: self._update_suggestion())

        ttk.Label(row1, text="終了小節").pack(side="left")
        self.bar_end_var = tk.StringVar()
        self.bar_end_entry = ttk.Entry(row1, textvariable=self.bar_end_var, width=8)
        self.bar_end_entry.pack(side="left", padx=(6, 12))
        self.bar_end_entry.bind("<KeyRelease>", lambda e: self._update_suggestion())

        ttk.Label(row1, text="備考（任意）").pack(side="left")
        self.note_var = tk.StringVar()
        self.note_entry = ttk.Entry(row1, textvariable=self.note_var)
        self.note_entry.pack(side="left", padx=(6, 0), fill="x", expand=True)

        # Suggestion box
        sug = ttk.LabelFrame(right, text="BPM提案（曲 + 小節を選ぶと更新）", padding=10)
        sug.pack(fill="x", pady=(10, 0))

        self.suggestion_label = ttk.Label(sug, text="提案: -", font=("Helvetica", 11, "bold"))
        self.suggestion_label.pack(anchor="w")

        # BPM input + metronome preview
        bpm_row = ttk.Frame(right)
        bpm_row.pack(fill="x", pady=(10, 0))

        ttk.Label(bpm_row, text="BPM（開始前に必須）").pack(side="left")
        self.bpm_var = tk.StringVar()
        self.bpm_entry = ttk.Entry(bpm_row, textvariable=self.bpm_var, width=10)
        self.bpm_entry.pack(side="left", padx=(6, 12))
        self.bpm_entry.bind("<KeyRelease>", lambda e: self._sync_preview_if_running())

        self.apply_suggest_btn = ttk.Button(bpm_row, text="提案BPMをセット", command=self.apply_suggested_bpm)
        self.apply_suggest_btn.pack(side="left")

        self.preview_btn = ttk.Button(bpm_row, text="試聴 ▶", command=self.toggle_preview_metronome)
        self.preview_btn.pack(side="left", padx=(10, 0))

        self.meter_label = ttk.Label(bpm_row, text="拍子: -", font=("Helvetica", 10, "bold"))
        self.meter_label.pack(side="left", padx=(12, 0))

        # counters
        counters = ttk.LabelFrame(right, text="カウンター（開始後に操作）", padding=10)
        counters.pack(fill="x", pady=(10, 0))

        c_row = ttk.Frame(counters)
        c_row.pack(fill="x")

        self.reps_label = ttk.Label(c_row, text="回数: 0", font=("Helvetica", 14, "bold"))
        self.reps_label.pack(side="left", padx=(0, 18))

        self.success_label = ttk.Label(c_row, text="成功: 0", font=("Helvetica", 14, "bold"))
        self.success_label.pack(side="left", padx=(0, 18))

        self.rate_label = ttk.Label(c_row, text="成功率: -", font=("Helvetica", 12, "bold"))
        self.rate_label.pack(side="left", padx=(0, 18))

        btns = ttk.Frame(counters)
        btns.pack(fill="x", pady=(10, 0))

        self.rep_plus = ttk.Button(btns, text="+1 回数", command=lambda: self.add_rep(1))
        self.rep_plus.pack(side="left")
        self.rep_minus = ttk.Button(btns, text="-1 回数", command=lambda: self.add_rep(-1))
        self.rep_minus.pack(side="left", padx=(6, 18))

        self.succ_plus = ttk.Button(btns, text="+1 成功", command=lambda: self.add_success(1))
        self.succ_plus.pack(side="left")
        self.succ_minus = ttk.Button(btns, text="-1 成功", command=lambda: self.add_success(-1))
        self.succ_minus.pack(side="left", padx=(6, 18))

        self.reset_counts_btn = ttk.Button(btns, text="回数/成功をリセット", command=self.reset_counts)
        self.reset_counts_btn.pack(side="left")

        # actions
        actions = ttk.LabelFrame(right, text="操作", padding=10)
        actions.pack(fill="x", pady=(10, 0))

        arow = ttk.Frame(actions)
        arow.pack(fill="x")

        self.start_btn = ttk.Button(arow, text="開始", command=self.start_session)
        self.start_btn.pack(side="left")

        self.finish_btn = ttk.Button(arow, text="終了して保存（完走のみ）", command=self.finish_and_save)
        self.finish_btn.pack(side="left", padx=(8, 0))

        self.cancel_btn = ttk.Button(arow, text="中断（保存しない）", command=self.cancel_session)
        self.cancel_btn.pack(side="left", padx=(8, 0))

        self.status_label = ttk.Label(actions, text="状態: -", font=("Helvetica", 11, "bold"))
        self.status_label.pack(anchor="w", pady=(10, 0))

        # ----- bottom log -----
        self.log = tk.Text(bottom, wrap="word", height=10)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        self.bind("<Escape>", lambda e: self.cancel_session())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        # 終了時に必ず止める
        try:
            self.metro.stop()
        except Exception:
            pass
        self.destroy()

    def log_append(self, s: str):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_paths(self):
        ensure_log_dir()
        messagebox.showinfo(
            "保存先",
            f"曲インデックス: {SONGS_CSV}\n練習ログ: {SESSIONS_CSV}\n（フォルダ: {os.path.abspath(LOG_DIR)}）"
        )

    # ---------- songs ----------
    def load_songs(self):
        self.songs = read_csv_rows(SONGS_CSV)

        # 互換補完（メモリ上）
        for r in self.songs:
            if not (r.get("beats_per_bar") or "").strip():
                r["beats_per_bar"] = "4"
            if not (r.get("beat_unit") or "").strip():
                r["beat_unit"] = "4"

        def keyfun(r):
            try:
                return int(r.get("song_index", ""))
            except Exception:
                return 10**9

        self.songs.sort(key=keyfun)

    def refresh_song_list(self):
        self.load_songs()
        q = self.search_var.get().strip().lower()

        self.song_listbox.delete(0, tk.END)
        self.filtered_song_indices = []

        for row in self.songs:
            idx = (row.get("song_index") or "").strip()
            name = (row.get("song_name") or "").strip()
            bpb = (row.get("beats_per_bar") or "4").strip()
            bu = (row.get("beat_unit") or "4").strip()

            if q and (q not in idx.lower()) and (q not in name.lower()):
                continue

            self.song_listbox.insert(tk.END, f"[{idx}] {name} ({bpb}/{bu})")
            try:
                self.filtered_song_indices.append(int(idx))
            except Exception:
                self.filtered_song_indices.append(None)

    def next_song_index(self) -> int:
        max_idx = 0
        for r in self.songs:
            try:
                max_idx = max(max_idx, int(r.get("song_index", 0)))
            except Exception:
                pass
        return max_idx + 1

    def add_song(self):
        name = self.new_song_name_var.get().strip()
        if not name:
            messagebox.showerror("入力エラー", "曲名を入力してください。")
            return

        bpb = safe_int(self.new_bpb_var.get(), 4)
        bu = safe_int(self.new_bu_var.get(), 4)
        if bpb <= 0 or bpb > 32:
            messagebox.showerror("入力エラー", "拍子（分子）は 1〜32 で指定してください。")
            return
        if bu not in (2, 4, 8):
            messagebox.showerror("入力エラー", "拍子（分母）は 2/4/8 のいずれかにしてください。")
            return

        idx = self.next_song_index()
        append_csv(SONGS_CSV, SONGS_FIELDS, {
            "song_index": idx,
            "song_name": name,
            "beats_per_bar": bpb,
            "beat_unit": bu,
            "created_at": now_iso(),
        })

        self.new_song_name_var.set("")
        self.refresh_song_list()
        self.select_song_by_index(idx)
        self.log_append(f"\n曲を追加: [{idx}] {name} ({bpb}/{bu})\n")

    def pick_by_index(self):
        s = self.pick_index_var.get().strip()
        if not s:
            return
        try:
            idx = int(s)
        except ValueError:
            messagebox.showerror("入力エラー", "インデックスは整数で。")
            return
        if not self.select_song_by_index(idx):
            messagebox.showerror("見つかりません", f"インデックス {idx} の曲が songs.csv にありません。")

    def select_song_by_index(self, idx: int) -> bool:
        found = None
        for r in self.songs:
            try:
                if int(r.get("song_index", -1)) == idx:
                    found = r
                    break
            except Exception:
                continue
        if not found:
            return False

        name = (found.get("song_name") or "").strip()
        bpb = safe_int(found.get("beats_per_bar"), 4)
        bu = safe_int(found.get("beat_unit"), 4)

        self.selected_song_index = idx
        self.selected_song_name = name
        self.selected_beats_per_bar = bpb
        self.selected_beat_unit = bu

        self.selected_label.configure(text=f"選択中: [{idx}] {name} ({bpb}/{bu})")
        self.meter_label.configure(text=f"拍子: {bpb}/{bu}")

        # highlight if visible
        for i, v in enumerate(self.filtered_song_indices):
            if v == idx:
                self.song_listbox.selection_clear(0, tk.END)
                self.song_listbox.selection_set(i)
                self.song_listbox.see(i)
                break

        self._update_suggestion()
        self._sync_preview_if_running()
        return True

    def on_song_select(self):
        sel = self.song_listbox.curselection()
        if not sel:
            return
        i = sel[0]
        idx = self.filtered_song_indices[i]
        if idx is None:
            return
        self.select_song_by_index(idx)

    # ---------- sessions cache ----------
    def load_sessions(self):
        self.sessions = read_csv_rows(SESSIONS_CSV)

    def reload_sessions(self):
        self.load_sessions()
        self._update_suggestion()

    # ---------- metronome preview ----------
    def _get_preview_params(self):
        bpm_s = self.bpm_var.get().strip()
        bpm = safe_int(bpm_s, 0)
        if bpm <= 0:
            return None
        bpb = int(getattr(self, "selected_beats_per_bar", 4) or 4)
        return bpm, bpb

    def toggle_preview_metronome(self):
        # 練習中は試聴トグルしない（常時鳴らしているため）
        if self.active:
            return

        if self.metro.is_running():
            self.metro.stop()
            self.preview_btn.configure(text="試聴 ▶")
            self.status_label.configure(text="状態: 待機中（曲→小節→提案→BPM→開始）")
            return

        params = self._get_preview_params()
        if params is None:
            messagebox.showerror("試聴できません", "BPMを入力してください（整数）。")
            return

        bpm, bpb = params
        self.metro.start(bpm=bpm, beats_per_bar=bpb)
        self.preview_btn.configure(text="試聴 ■")

    def _sync_preview_if_running(self):
        # 試聴中 or 練習中のテンポ変更（練習中はBPM入力欄がロックされるので基本呼ばれない）
        if not self.metro.is_running():
            return
        params = self._get_preview_params()
        if params is None:
            return
        bpm, bpb = params
        self.metro.update(bpm=bpm, beats_per_bar=bpb)

    # ---------- suggestion logic ----------
    def _parse_bar_inputs(self):
        s1 = self.bar_start_var.get().strip()
        s2 = self.bar_end_var.get().strip()
        if not s1 or not s2:
            return None
        try:
            a = int(s1)
            b = int(s2)
        except Exception:
            return None
        if a <= 0 or b <= 0:
            return None
        if a > b:
            return None
        return a, b

    def _compute_suggestion(self, song_index: int, t_start: int, t_end: int):
        """
        returns dict with fields:
        - kind: "none" | "achieved" | "latest"
        - suggested_bpm_value: int|None
        - details: str
        """
        rows = []
        for r in self.sessions:
            if safe_int(r.get("song_index"), -1) != song_index:
                continue

            bs = safe_int(r.get("bar_start"), None)
            be = safe_int(r.get("bar_end"), None)
            if bs is None or be is None:
                continue
            if bs > be:
                bs, be = be, bs

            if overlap(t_start, t_end, bs, be):
                rows.append(r)

        if not rows:
            return {
                "kind": "none",
                "suggested_bpm_value": None,
                "details": "過去ログがありません（この曲・この小節に重なる記録なし）",
            }

        achieved_per_bar = {}
        for bar in range(t_start, t_end + 1):
            best_min_bpm = None
            for r in rows:
                bs = safe_int(r.get("bar_start"), 0)
                be = safe_int(r.get("bar_end"), 0)
                if bs > be:
                    bs, be = be, bs
                if not (bs <= bar <= be):
                    continue

                reps = safe_int(r.get("reps"), 0)
                succ = safe_int(r.get("success"), 0)
                if reps <= 0:
                    continue
                acc = succ / reps
                bpm = safe_int(r.get("bpm"), 0)
                if bpm <= 0:
                    continue
                if acc >= SUCCESS_THRESHOLD:
                    if best_min_bpm is None or bpm < best_min_bpm:
                        best_min_bpm = bpm

            if best_min_bpm is not None:
                achieved_per_bar[bar] = best_min_bpm

        if achieved_per_bar:
            suggested = min(achieved_per_bar.values())
            bottlenecks = [b for b, v in achieved_per_bar.items() if v == suggested]
            missing = [b for b in range(t_start, t_end + 1) if b not in achieved_per_bar]
            bn_str = ",".join(map(str, bottlenecks[:10])) + ("…" if len(bottlenecks) > 10 else "")
            detail = f"提案BPM（90%達成ログより）: {suggested}  | ボトルネック小節: {bn_str}"
            if missing:
                mp = missing[:10]
                detail += f"\n※ 90%達成ログが無い小節あり（例: {','.join(map(str, mp))}{'…' if len(missing)>10 else ''}）"
            return {
                "kind": "achieved",
                "suggested_bpm_value": suggested,
                "details": detail,
            }

        parsed = []
        for r in rows:
            ts = parse_timestamp(r.get("timestamp_start") or "")
            if ts is None:
                continue
            parsed.append((ts, r))
        if not parsed:
            parsed = [(datetime.min, r) for r in rows]

        max_ts = max(ts for ts, _ in parsed)
        latest_group = [r for ts, r in parsed if ts == max_ts]

        chosen = None
        for r in latest_group:
            bpm = safe_int(r.get("bpm"), 0)
            if bpm <= 0:
                continue
            if chosen is None or bpm < safe_int(chosen.get("bpm"), 10**9):
                chosen = r

        if chosen is None:
            return {
                "kind": "latest",
                "suggested_bpm_value": None,
                "details": "重なる過去ログはありますが、BPM/回数が不正で提案できません。",
            }

        reps = safe_int(chosen.get("reps"), 0)
        succ = safe_int(chosen.get("success"), 0)
        acc = (succ / reps) if reps > 0 else 0.0
        bpm = safe_int(chosen.get("bpm"), 0)
        ts_s = (chosen.get("timestamp_start") or "").strip()
        bs = safe_int(chosen.get("bar_start"), 0)
        be = safe_int(chosen.get("bar_end"), 0)
        detail = (
            "90%達成ログがありません。\n"
            f"直近の練習（遅いほう）: BPM {bpm} / 正解率 {acc:.1%} / 日時 {ts_s} / 区間 {bs}-{be}"
        )
        return {
            "kind": "latest",
            "suggested_bpm_value": bpm,
            "details": detail,
        }

    def _update_suggestion(self):
        if self.active:
            return

        if self.selected_song_index is None:
            self.suggestion_label.configure(text="提案: 曲を選択してください")
            self.apply_suggest_btn.configure(state="disabled")
            self._last_suggested_bpm = None
            return

        bars = self._parse_bar_inputs()
        if bars is None:
            self.suggestion_label.configure(text="提案: 小節（開始・終了）を入力してください")
            self.apply_suggest_btn.configure(state="disabled")
            self._last_suggested_bpm = None
            return

        t1, t2 = bars
        self.load_sessions()
        res = self._compute_suggestion(int(self.selected_song_index), t1, t2)

        self.suggestion_label.configure(text=f"提案: {res['details']}")
        self._last_suggested_bpm = res["suggested_bpm_value"]
        if self._last_suggested_bpm is not None:
            self.apply_suggest_btn.configure(state="normal")
        else:
            self.apply_suggest_btn.configure(state="disabled")

    def apply_suggested_bpm(self):
        if self.active:
            return
        if getattr(self, "_last_suggested_bpm", None) is None:
            return
        self.bpm_var.set(str(self._last_suggested_bpm))
        self._sync_preview_if_running()

    # ---------- session state ----------
    def _set_idle_state(self):
        self.active = False
        self.status_label.configure(text="状態: 待機中（曲→小節→提案→BPM→開始）")

        self.start_btn.configure(state="normal")
        self.finish_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")

        self.bar_start_entry.configure(state="normal")
        self.bar_end_entry.configure(state="normal")
        self.bpm_entry.configure(state="normal")
        self.note_entry.configure(state="normal")

        self.song_listbox.configure(state="normal")

        self.rep_plus.configure(state="disabled")
        self.rep_minus.configure(state="disabled")
        self.succ_plus.configure(state="disabled")
        self.succ_minus.configure(state="disabled")
        self.reset_counts_btn.configure(state="disabled")

        self._update_counter_labels()

        # 練習が終わったら、試聴ボタンは「停止状態」表示に寄せる
        if not self.metro.is_running():
            self.preview_btn.configure(text="試聴 ▶")

    def _set_active_state(self):
        self.active = True
        self.start_btn.configure(state="disabled")
        self.finish_btn.configure(state="normal")
        self.cancel_btn.configure(state="normal")

        self.bar_start_entry.configure(state="disabled")
        self.bar_end_entry.configure(state="disabled")
        self.bpm_entry.configure(state="disabled")
        self.song_listbox.configure(state="disabled")

        self.rep_plus.configure(state="normal")
        self.rep_minus.configure(state="normal")
        self.succ_plus.configure(state="normal")
        self.succ_minus.configure(state="normal")
        self.reset_counts_btn.configure(state="normal")

        self.status_label.configure(text=f"状態: 計測中… session_id={self.session_id}")

    def _validate_before_start(self):
        if self.selected_song_index is None or not self.selected_song_name:
            raise ValueError("曲を選択してください。")

        s1 = self.bar_start_var.get().strip()
        s2 = self.bar_end_var.get().strip()
        if not s1 or not s2:
            raise ValueError("開始小節・終了小節を入力してください。")
        try:
            bar_start = int(s1)
            bar_end = int(s2)
        except Exception:
            raise ValueError("小節は整数で入力してください。")
        if bar_start <= 0 or bar_end <= 0:
            raise ValueError("小節は1以上の整数で入力してください。")
        if bar_start > bar_end:
            raise ValueError("開始小節 <= 終了小節 になるよう入力してください。")

        bpm_s = self.bpm_var.get().strip()
        if not bpm_s:
            raise ValueError("BPMを入力してください（開始前に必須）。")
        try:
            bpm = int(bpm_s)
        except Exception:
            raise ValueError("BPMは整数で入力してください。")
        if bpm <= 0 or bpm > 400:
            raise ValueError("BPMは 1〜400 の整数で入力してください。")

        return bar_start, bar_end, bpm

    def start_session(self):
        if self.active:
            return
        try:
            bar_start, bar_end, bpm = self._validate_before_start()
        except ValueError as e:
            messagebox.showerror("開始できません", str(e))
            return

        # もし試聴が鳴っていたら、練習用にそのまま使う（停止せず update だけ）
        bpb = int(getattr(self, "selected_beats_per_bar", 4) or 4)

        self.session_id = new_session_id()
        self.t_start_iso = now_iso()
        self.t_start_epoch = time.time()

        self.locked_bar_start = bar_start
        self.locked_bar_end = bar_end
        self.locked_bpm = bpm

        self.reps = 0
        self.success = 0
        self._update_counter_labels()

        self._set_active_state()

        # 練習中メトロノーム開始（常時）
        if self.metro.is_running():
            self.metro.update(bpm=bpm, beats_per_bar=bpb)
        else:
            self.metro.start(bpm=bpm, beats_per_bar=bpb)

        self.preview_btn.configure(text="試聴 ■")  # 実際は練習中も鳴っているので「停止」表示

        self.log_append(
            f"\n=== 開始 [{self.session_id}] 曲=[{self.selected_song_index}] {self.selected_song_name} "
            f"({bpb}/{self.selected_beat_unit}) 小節={bar_start}-{bar_end} BPM={bpm} ===\n"
        )

    def cancel_session(self):
        if not self.active:
            return

        self.log_append(f"=== 中断（保存しない） session_id={self.session_id} ===\n")

        # 練習中メトロノーム停止
        self.metro.stop()
        self.preview_btn.configure(text="試聴 ▶")

        self.session_id = None
        self.t_start_iso = None
        self.t_start_epoch = None
        self.locked_bar_start = None
        self.locked_bar_end = None
        self.locked_bpm = None
        self.reps = 0
        self.success = 0

        self._set_idle_state()
        self._update_suggestion()

    def finish_and_save(self):
        if not self.active:
            return
        if self.reps <= 0:
            messagebox.showerror("保存できません", "回数が0です。最低1回はカウントしてください。")
            return

        self.success = min(self.success, self.reps)

        t_end_iso = now_iso()
        duration = time.time() - self.t_start_epoch

        row = SessionRow(
            session_id=self.session_id,
            timestamp_start=self.t_start_iso,
            timestamp_end=t_end_iso,
            duration_sec=round(duration, 3),
            song_index=int(self.selected_song_index),
            song_name=str(self.selected_song_name),
            bar_start=int(self.locked_bar_start),
            bar_end=int(self.locked_bar_end),
            bpm=int(self.locked_bpm),
            reps=int(self.reps),
            success=int(self.success),
            note=self.note_var.get()
        )

        append_csv(SESSIONS_CSV, SESSIONS_FIELDS, asdict(row))

        rate = (self.success / self.reps) if self.reps else 0.0
        self.log_append(
            f"=== 保存しました ===\n"
            f"時間 {row.duration_sec:.1f}s / 回数 {row.reps} / 成功 {row.success} / 正解率 {rate:.1%}\n"
            f"-> {SESSIONS_CSV}\n"
        )

        # 練習中メトロノーム停止
        self.metro.stop()
        self.preview_btn.configure(text="試聴 ▶")

        self.session_id = None
        self.t_start_iso = None
        self.t_start_epoch = None
        self.locked_bar_start = None
        self.locked_bar_end = None
        self.locked_bpm = None
        self.reps = 0
        self.success = 0

        self._set_idle_state()
        self.reload_sessions()

    # ---------- counters ----------
    def _update_counter_labels(self):
        self.reps_label.configure(text=f"回数: {self.reps}")
        self.success_label.configure(text=f"成功: {self.success}")
        if self.reps > 0:
            self.rate_label.configure(text=f"正解率: {self.success / self.reps:.1%}")
        else:
            self.rate_label.configure(text="正解率: -")

    def add_rep(self, delta: int):
        if not self.active:
            return
        self.reps = max(0, self.reps + delta)
        if self.success > self.reps:
            self.success = self.reps
        self._update_counter_labels()

    def add_success(self, delta: int):
        if not self.active:
            return
        self.success = max(0, self.success + delta)
        if self.success > self.reps:
            self.success = self.reps
        self._update_counter_labels()

    def reset_counts(self):
        if not self.active:
            return
        self.reps = 0
        self.success = 0
        self._update_counter_labels()


if __name__ == "__main__":
    # 起動時に schema を整える（旧songs.csvを自動補完）
    ensure_songs_schema()
    ensure_sessions_schema()

    app = PracticeTracker()
    app.mainloop()
