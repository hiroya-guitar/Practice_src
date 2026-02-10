#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jazz Code Trainer (5弦/6弦ルート)
- 1〜12フレットのルートからコード（maj7 / 7 / m7 / m7b5）をランダム出題
- ルート弦（5 or 6）は必ず表示（設定で出題対象の弦を選択可）
- 問いは「ルート以外の構成音」＝ 3rd / 5th / 7th を別問題として出題
- 表示は必ず：メジャー3度/マイナー3度/完全5度/♭5/メジャー7度/マイナー7度
- 重み付け：
    - 通常コード（maj7/7/m7）：3rd 50%, 7th 25%, 5th 25%
    - m7b5：3rd/5th/7th 均等
- 回答は音名1つ（#寄せ）。大文字小文字/空白は無視。A#のみ正解（Bbは不正解）
- 制限時間なし（ただし反応時間はログに保存）
- PASSなし
- ログ保存：
    code_trainer_logs/questions.csv（1問ごと）
    code_trainer_logs/summary.csv（セッション要約）
    code_trainer_logs/best.json（ベスト：正解数→平均秒/問→合計秒で更新）
"""

import csv
import json
import os
import random
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from tkinter import ttk, messagebox

# ===== 音名・チューニング（#寄せ固定）=====
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
OPEN_STRINGS = {6: "E", 5: "A"}  # 5弦/6弦のみ使用

# ===== 出題条件（固定：1〜12フレット）=====
FRET_MIN = 1
FRET_MAX = 12

# ===== ログ保存 =====
LOG_DIR = "code_trainer_logs"
SUMMARY_CSV = os.path.join(LOG_DIR, "summary.csv")
QUESTIONS_CSV = os.path.join(LOG_DIR, "questions.csv")
BEST_JSON = os.path.join(LOG_DIR, "best.json")


def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def note_to_pc(note: str) -> int:
    note = note.strip().upper()
    if note not in NOTE_NAMES:
        raise ValueError(note)
    return NOTE_NAMES.index(note)


def pc_to_note(pc: int) -> str:
    return NOTE_NAMES[pc % 12]


def calc_root_note(string_no: int, fret: int) -> str:
    base = note_to_pc(OPEN_STRINGS[string_no])
    return pc_to_note(base + fret)


def normalize_answer(s: str) -> str:
    # 大文字/小文字、空白、全角シャープを吸収
    s = (s or "").strip().upper().replace(" ", "").replace("　", "").replace("♯", "#")
    return s


def grade(correct: int, total: int) -> str:
    if total <= 0:
        return "-"
    r = correct / total
    if r >= 0.9:
        return "S"
    if r >= 0.75:
        return "A"
    if r >= 0.6:
        return "B"
    return "C"


def read_best():
    try:
        with open(BEST_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_best(best_obj: dict):
    ensure_log_dir()
    with open(BEST_JSON, "w", encoding="utf-8") as f:
        json.dump(best_obj, f, ensure_ascii=False, indent=2)


def append_csv(path: str, fieldnames: list[str], row: dict):
    ensure_log_dir()
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        w.writerow(row)


def is_new_best(candidate: dict, best: dict | None) -> bool:
    """
    ベスト判定：まず正解数、同点なら平均秒が速い方、さらに同点なら合計が短い方
    """
    if best is None:
        return True
    if candidate["correct"] != best.get("correct", -1):
        return candidate["correct"] > best.get("correct", -1)
    if abs(candidate["avg_sec_per_q"] - best.get("avg_sec_per_q", 1e9)) > 1e-9:
        return candidate["avg_sec_per_q"] < best.get("avg_sec_per_q", 1e9)
    return candidate["elapsed_sec"] < best.get("elapsed_sec", 1e9)


# ===== 出題ロジック =====
CHORD_TYPES = ["maj7", "7", "m7", "m7b5"]  # 表示もこのまま
# インターバル（半音）
INTERVALS = {
    "M3": 4,
    "m3": 3,
    "P5": 7,
    "b5": 6,
    "M7": 11,
    "m7": 10,
}
# 表示名（日本語）
QUALITY_LABEL = {
    "M3": "メジャー3度",
    "m3": "マイナー3度",
    "P5": "完全5度",
    "b5": "♭5",
    "M7": "メジャー7度",
    "m7": "マイナー7度",
}


def chord_tone_quality(chord_type: str, degree: int) -> str:
    """
    chord_type と degree(3/5/7) から、問うべき品質（M3/m3/P5/b5/M7/m7）を返す。
    """
    if degree == 3:
        return "M3" if chord_type in ("maj7", "7") else "m3"
    if degree == 5:
        return "b5" if chord_type == "m7b5" else "P5"
    if degree == 7:
        return "M7" if chord_type == "maj7" else "m7"
    raise ValueError(degree)


def pick_degree(chord_type: str) -> int:
    """
    出題度数を重み付けで選ぶ。
    - 通常：3rd 50%, 7th 25%, 5th 25%
    - m7b5：均等
    """
    if chord_type == "m7b5":
        return random.choice([3, 5, 7])
    # 3:0.5, 7:0.25, 5:0.25
    x = random.random()
    if x < 0.5:
        return 3
    if x < 0.75:
        return 7
    return 5


def make_chord_name(root_note: str, chord_type: str) -> str:
    if chord_type == "7":
        return f"{root_note}7"
    if chord_type == "maj7":
        return f"{root_note}maj7"
    if chord_type == "m7":
        return f"{root_note}m7"
    if chord_type == "m7b5":
        return f"{root_note}m7b5"
    return f"{root_note}{chord_type}"


@dataclass
class QuestionRecord:
    session_id: str
    q_index: int
    root_string: int
    root_fret: int
    chord_name: str
    chord_type: str
    asked_degree: int
    asked_quality: str
    correct_note: str
    user_input: str
    is_correct: int
    response_time_sec: float


@dataclass
class SessionSummary:
    session_id: str
    timestamp_local: str
    mode: str
    enabled_strings: str  # "6,5"
    fret_range: str       # "1-12"
    total_q: int
    correct: int
    wrong: int
    accuracy: float
    elapsed_sec: float
    avg_sec_per_q: float
    grade: str


class CodeTrainerUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jazzコード構成音トレーナー（5弦/6弦ルート）")
        self.geometry("860x560")
        self.minsize(820, 520)

        # state
        self.in_quiz = False
        self.review_mode = False
        self.mode = "通常"
        self.enabled_strings = [6, 5]
        self.total_q = 20

        self.q_index = 0
        self.correct = 0
        self.wrong_pool = []  # list[tuple] for review: (root_string, root_fret, chord_type, degree)
        self.review_queue = []
        self.records: list[QuestionRecord] = []

        self.session_id = None
        self.session_start = 0.0
        self.q_start = 0.0
        self.current = None  # dict of current question

        self._build_ui()
        self._set_idle()
        self._show_best_on_start()

    # -------- UI --------
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        ctl = ttk.LabelFrame(root, text="設定", padding=10)
        ctl.pack(fill="x")

        top_row = ttk.Frame(ctl)
        top_row.pack(fill="x", pady=(0, 8))

        ttk.Label(top_row, text="モード").pack(side="left")
        self.mode_var = tk.StringVar(value="通常（ランダム）")
        ttk.Combobox(
            top_row,
            textvariable=self.mode_var,
            values=["通常（ランダム）", "復習（間違いのみ）"],
            state="readonly",
            width=18
        ).pack(side="left", padx=(6, 16))

        ttk.Label(top_row, text="出題するルート弦:").pack(side="left")
        self.string_vars = {}
        for s in [6, 5]:
            v = tk.BooleanVar(value=True)
            self.string_vars[s] = v
            ttk.Checkbutton(top_row, text=f"{s}弦ルート", variable=v).pack(side="left", padx=6)

        params = ttk.Frame(ctl)
        params.pack(fill="x")

        ttk.Label(params, text="問題数").pack(side="left")
        self.total_var = tk.StringVar(value="20")
        ttk.Entry(params, textvariable=self.total_var, width=6).pack(side="left", padx=(6, 14))

        # 表示はシンプルに：フレット範囲は固定
        ttk.Label(params, text=f"ルート範囲: {FRET_MIN}〜{FRET_MAX}f（表示はコード名のみ）").pack(side="left")

        self.start_btn = ttk.Button(params, text="開始", command=self.start)
        self.start_btn.pack(side="left", padx=(10, 6))
        self.stop_btn = ttk.Button(params, text="中断", command=self.stop)
        self.stop_btn.pack(side="left")

        best_frame = ttk.LabelFrame(root, text="ベスト記録（起動時/終了時に更新）", padding=10)
        best_frame.pack(fill="x", pady=(10, 0))
        self.best_label = ttk.Label(best_frame, text="ベスト: -", font=("Helvetica", 12, "bold"))
        self.best_label.pack(anchor="w")

        qbox = ttk.LabelFrame(root, text="出題", padding=10)
        qbox.pack(fill="x", pady=(10, 0))

        self.q_label = ttk.Label(qbox, text="開始を押してください", font=("Helvetica", 20, "bold"))
        self.q_label.pack(anchor="w")

        ans_row = ttk.Frame(qbox)
        ans_row.pack(fill="x", pady=(10, 0))

        ttk.Label(ans_row, text="答え（例: F# / A#）", font=("Helvetica", 12)).pack(side="left")
        self.ans_var = tk.StringVar()
        self.ans_entry = ttk.Entry(ans_row, textvariable=self.ans_var, width=12, font=("Helvetica", 16))
        self.ans_entry.pack(side="left", padx=(10, 10))
        self.ans_entry.bind("<Return>", lambda e: self.submit())

        self.submit_btn = ttk.Button(ans_row, text="決定", command=self.submit)
        self.submit_btn.pack(side="left")

        self.feedback = ttk.Label(qbox, text="", font=("Helvetica", 12))
        self.feedback.pack(anchor="w", pady=(8, 0))

        stat = ttk.Frame(root, padding=(0, 10))
        stat.pack(fill="x")

        self.progress = ttk.Label(stat, text="進捗: -", font=("Helvetica", 12, "bold"))
        self.progress.pack(side="left")
        self.score = ttk.Label(stat, text="正解: -", font=("Helvetica", 12, "bold"))
        self.score.pack(side="left", padx=(20, 0))
        self.timer = ttk.Label(stat, text="経過: -", font=("Helvetica", 12, "bold"))
        self.timer.pack(side="left", padx=(20, 0))
        self.wrong_label = ttk.Label(stat, text="間違い: -", font=("Helvetica", 12, "bold"))
        self.wrong_label.pack(side="left", padx=(20, 0))

        log_frame = ttk.LabelFrame(root, text="ログ（セッション完走で自動保存）", padding=10)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, wrap="word", height=10)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        self.bind("<Escape>", lambda e: self.stop())

    def log_append(self, s: str):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_idle(self):
        self.in_quiz = False
        self.q_label.configure(text="開始を押してください")
        self.feedback.configure(text="")
        self.progress.configure(text="進捗: -")
        self.score.configure(text="正解: -")
        self.timer.configure(text="経過: -")
        self.wrong_label.configure(text="間違い: -")
        self.ans_var.set("")
        self.ans_entry.configure(state="disabled")
        self.submit_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.start_btn.configure(state="normal")

    def _set_running(self):
        self.ans_entry.configure(state="normal")
        self.submit_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        self.start_btn.configure(state="disabled")
        self.ans_entry.focus_set()

    def _read_settings(self):
        enabled = [s for s, v in self.string_vars.items() if v.get()]
        if not enabled:
            raise ValueError("出題するルート弦を選んでください（5弦/6弦）。")

        try:
            total = int(self.total_var.get())
            if total <= 0:
                raise ValueError
        except Exception:
            raise ValueError("問題数は1以上の整数で。")

        mode_text = self.mode_var.get()
        return enabled, total, mode_text

    # -------- best display --------
    def _show_best_on_start(self):
        best = read_best()
        if not best:
            self.best_label.configure(text="ベスト: （まだ記録がありません）")
            return
        self.best_label.configure(
            text=(
                f"ベスト: 正解 {best['correct']}/{best['total_q']}  "
                f"平均 {best['avg_sec_per_q']:.2f}s/問  "
                f"合計 {best['elapsed_sec']:.2f}s  "
                f"({best['timestamp_local']})"
            )
        )

    # -------- session control --------
    def start(self):
        if self.in_quiz:
            return

        try:
            enabled, total, mode_text = self._read_settings()
        except ValueError as e:
            messagebox.showerror("設定エラー", str(e))
            return

        if mode_text.startswith("復習"):
            if not self.wrong_pool:
                messagebox.showinfo("復習", "間違いがまだありません。まず通常で解いてください。")
                return
            self.review_mode = True
            self.review_queue = self.wrong_pool[:]
            random.shuffle(self.review_queue)
            self.total_q = len(self.review_queue)
            self.enabled_strings = enabled
            self.mode = "復習"
        else:
            self.review_mode = False
            self.enabled_strings = enabled
            self.total_q = total
            self.mode = "通常"
            self.wrong_pool = []

        self.q_index = 0
        self.correct = 0
        self.records = []

        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_start = time.time()

        self.log_append(f"\n=== セッション開始 [{self.session_id}] mode={self.mode} ===\n")
        self._set_running()
        self.next_q()
        self._tick()

    def stop(self):
        if not self.in_quiz:
            return
        self.in_quiz = False
        self.log_append("\n=== 中断（保存しません） ===\n")
        self._set_idle()

    # -------- quiz engine --------
    def _generate_question(self):
        if self.review_mode:
            root_string, root_fret, chord_type, degree = self.review_queue[self.q_index - 1]
        else:
            root_string = random.choice(self.enabled_strings)
            root_fret = random.randint(FRET_MIN, FRET_MAX)
            chord_type = random.choice(CHORD_TYPES)
            degree = pick_degree(chord_type)

        root_note = calc_root_note(root_string, root_fret)
        chord_name = make_chord_name(root_note, chord_type)
        asked_quality = chord_tone_quality(chord_type, degree)
        correct_note = pc_to_note(note_to_pc(root_note) + INTERVALS[asked_quality])

        return {
            "root_string": root_string,
            "root_fret": root_fret,
            "root_note": root_note,
            "chord_type": chord_type,
            "chord_name": chord_name,
            "asked_degree": degree,
            "asked_quality": asked_quality,
            "correct_note": correct_note,
        }

    def next_q(self):
        self.in_quiz = True
        self.q_index += 1
        if self.q_index > self.total_q:
            self.finish_and_save()
            return

        q = self._generate_question()
        self.current = q
        self.q_start = time.time()

        tag = "[復習] " if self.review_mode else ""
        q_text = (
            f"{tag}Q{self.q_index}: {q['root_string']}弦ルート / {q['chord_name']}  "
            f"→ {QUALITY_LABEL[q['asked_quality']]} は？"
        )
        self.q_label.configure(text=q_text)
        self.feedback.configure(text="")
        self.ans_var.set("")
        self._update_stat()

    def submit(self):
        if not self.in_quiz or not self.current:
            return

        q = self.current
        user = normalize_answer(self.ans_var.get())
        rt = time.time() - self.q_start

        # 入力が NOTE_NAMES でなければ即ミス（Bbはここで弾かれる）
        is_correct = 1 if user == q["correct_note"] else 0

        if is_correct:
            self.correct += 1
            self.feedback.configure(text=f"OK  ({rt:.2f}s)")
            self.log_append(
                f"OK  : {q['root_string']}弦 {q['chord_name']} / {QUALITY_LABEL[q['asked_quality']]} = "
                f"{q['correct_note']}  ({rt:.2f}s)\n"
            )
        else:
            self.feedback.configure(text=f"MISS 正解={q['correct_note']}  ({rt:.2f}s)")
            self.log_append(
                f"MISS: {q['root_string']}弦 {q['chord_name']} / {QUALITY_LABEL[q['asked_quality']]}  "
                f"入力={user or '∅'}  正解={q['correct_note']}  ({rt:.2f}s)\n"
            )
            if not self.review_mode:
                self.wrong_pool.append((q["root_string"], q["root_fret"], q["chord_type"], q["asked_degree"]))

        self.records.append(QuestionRecord(
            session_id=self.session_id,
            q_index=self.q_index,
            root_string=q["root_string"],
            root_fret=q["root_fret"],
            chord_name=q["chord_name"],
            chord_type=q["chord_type"],
            asked_degree=q["asked_degree"],
            asked_quality=q["asked_quality"],
            correct_note=q["correct_note"],
            user_input=user,
            is_correct=is_correct,
            response_time_sec=rt
        ))

        self._update_stat()
        self.after(200, self.next_q)

    def _update_stat(self):
        elapsed = time.time() - self.session_start if self.session_start else 0.0
        wrong = (self.q_index - self.correct) if self.in_quiz else (self.total_q - self.correct)
        if not self.review_mode:
            wrong = len(self.wrong_pool)
        else:
            wrong = max(0, self.q_index - self.correct)

        self.progress.configure(text=f"進捗: {self.q_index}/{self.total_q}")
        self.score.configure(text=f"正解: {self.correct}")
        self.timer.configure(text=f"経過: {elapsed:.1f}s")
        self.wrong_label.configure(text=f"間違い: {wrong}")

    def _tick(self):
        if not self.in_quiz:
            return
        self._update_stat()
        self.after(200, self._tick)

    # -------- saving & best --------
    def finish_and_save(self):
        self.in_quiz = False
        elapsed = time.time() - self.session_start
        avg = elapsed / max(1, self.total_q)
        wrong = self.total_q - self.correct
        acc = self.correct / max(1, self.total_q)
        g = grade(self.correct, self.total_q)

        enabled_str = ",".join(map(str, sorted(self.enabled_strings, reverse=True)))
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = SessionSummary(
            session_id=self.session_id,
            timestamp_local=ts,
            mode=self.mode,
            enabled_strings=enabled_str,
            fret_range=f"{FRET_MIN}-{FRET_MAX}",
            total_q=self.total_q,
            correct=self.correct,
            wrong=wrong,
            accuracy=acc,
            elapsed_sec=elapsed,
            avg_sec_per_q=avg,
            grade=g
        )

        ensure_log_dir()

        # 1問ログ
        q_fields = list(asdict(self.records[0]).keys()) if self.records else [
            "session_id", "q_index", "root_string", "root_fret", "chord_name", "chord_type",
            "asked_degree", "asked_quality", "correct_note", "user_input", "is_correct", "response_time_sec"
        ]
        for r in self.records:
            append_csv(QUESTIONS_CSV, q_fields, asdict(r))

        # 要約ログ
        s_fields = list(asdict(summary).keys())
        append_csv(SUMMARY_CSV, s_fields, asdict(summary))

        # ベスト判定
        candidate_best = {
            "session_id": summary.session_id,
            "timestamp_local": summary.timestamp_local,
            "mode": summary.mode,
            "total_q": summary.total_q,
            "correct": summary.correct,
            "accuracy": summary.accuracy,
            "elapsed_sec": summary.elapsed_sec,
            "avg_sec_per_q": summary.avg_sec_per_q,
            "enabled_strings": summary.enabled_strings,
        }
        best = read_best()
        if is_new_best(candidate_best, best):
            write_best(candidate_best)
            self.log_append("\n★ ベスト更新！\n")
        self._show_best_on_start()

        self.log_append("\n=== 完走：保存しました ===\n")
        self.log_append(f"summary.csv / questions.csv に追記（{LOG_DIR}/）\n")
        self.log_append(
            f"正解 {self.correct}/{self.total_q}  "
            f"平均 {avg:.2f}s/問  合計 {elapsed:.2f}s  Grade {g}\n"
        )

        self.q_label.configure(text=f"完走！ 正解 {self.correct}/{self.total_q}（平均 {avg:.2f}s/問）")
        self.feedback.configure(text=f"保存: {LOG_DIR}/  | ベストは上部に表示")
        self._set_idle()


if __name__ == "__main__":
    app = CodeTrainerUI()
    app.mainloop()
