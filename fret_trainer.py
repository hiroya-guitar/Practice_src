#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import random
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime
from tkinter import ttk, messagebox

# ===== 音名・チューニング =====
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
OPEN_STRINGS = {6: "E", 5: "A", 4: "D", 3: "G", 2: "B", 1: "E"}

# ===== ログ保存 =====
LOG_DIR = "fret_trainer_logs"
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

def calc_note(string_no: int, fret: int) -> str:
    base = note_to_pc(OPEN_STRINGS[string_no])
    return pc_to_note(base + fret)

def normalize_answer(s: str) -> str:
    return s.strip().upper().replace("♯", "#")

def grade(correct: int, total: int) -> str:
    if total <= 0:
        return "-"
    r = correct / total
    if r >= 0.9: return "S"
    if r >= 0.75: return "A"
    if r >= 0.6: return "B"
    return "C"

@dataclass
class QuestionRecord:
    session_id: str
    q_index: int
    string: int
    fret: int
    correct_note: str
    user_input: str
    is_correct: int   # 1/0
    is_pass: int      # 1/0
    response_time_sec: float

@dataclass
class SessionSummary:
    session_id: str
    timestamp_local: str
    mode: str
    enabled_strings: str      # "6,5,4,3,2,1"
    fret_max: int
    total_q: int
    time_limit_sec: float
    correct: int
    wrong: int
    passed: int
    accuracy: float
    elapsed_sec: float
    avg_sec_per_q: float
    grade: str

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
    # tie-break: avg_sec_per_q lower is better
    if abs(candidate["avg_sec_per_q"] - best.get("avg_sec_per_q", 1e9)) > 1e-9:
        return candidate["avg_sec_per_q"] < best.get("avg_sec_per_q", 1e9)
    return candidate["elapsed_sec"] < best.get("elapsed_sec", 1e9)

class FretTrainerUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("指板音名トレーナー")
        self.geometry("860x560")
        self.minsize(800, 520)

        # session state
        self.in_quiz = False
        self.mode = "通常"
        self.enabled_strings = [6, 5, 4, 3, 2, 1]
        self.total_q = 20
        self.time_limit = 2.0
        self.fret_max = 12

        self.q_index = 0
        self.correct = 0
        self.passed = 0

        self.session_start = 0.0
        self.q_start = 0.0
        self.current = None  # (s, f, ans)

        self.wrong_pool = []     # list[(s,f,ans)] for review mode
        self.records: list[QuestionRecord] = []

        self.session_id = None
        self.review_mode = False
        self.review_queue = []

        self._build_ui()
        self._set_idle()
        self._show_best_on_start()

    # -------- UI --------
    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # ===== Controls =====
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

        ttk.Label(top_row, text="出題する弦:").pack(side="left")
        self.string_vars = {}
        for s in [6, 5, 4, 3, 2, 1]:
            v = tk.BooleanVar(value=True)
            self.string_vars[s] = v
            ttk.Checkbutton(top_row, text=f"{s}弦", variable=v).pack(side="left", padx=5)

        ttk.Button(top_row, text="全選択", command=self.select_all_strings).pack(side="left", padx=(16, 4))
        ttk.Button(top_row, text="全解除", command=self.clear_all_strings).pack(side="left", padx=4)

        params = ttk.Frame(ctl)
        params.pack(fill="x")

        ttk.Label(params, text="問題数").pack(side="left")
        self.total_var = tk.StringVar(value="20")
        ttk.Entry(params, textvariable=self.total_var, width=6).pack(side="left", padx=(6, 14))

        ttk.Label(params, text="制限秒/問").pack(side="left")
        self.limit_var = tk.StringVar(value="2.0")
        ttk.Entry(params, textvariable=self.limit_var, width=6).pack(side="left", padx=(6, 14))

        ttk.Label(params, text="フレット上限(0〜)").pack(side="left")
        self.fret_var = tk.StringVar(value="12")
        ttk.Entry(params, textvariable=self.fret_var, width=6).pack(side="left", padx=(6, 14))

        self.start_btn = ttk.Button(params, text="開始", command=self.start)
        self.start_btn.pack(side="left", padx=(10, 6))
        self.stop_btn = ttk.Button(params, text="中断", command=self.stop)
        self.stop_btn.pack(side="left")

        # ===== Best =====
        best_frame = ttk.LabelFrame(root, text="ベスト記録（起動時/終了時に更新）", padding=10)
        best_frame.pack(fill="x", pady=(10, 0))
        self.best_label = ttk.Label(best_frame, text="ベスト: -", font=("Helvetica", 12, "bold"))
        self.best_label.pack(anchor="w")

        # ===== Question =====
        qbox = ttk.LabelFrame(root, text="出題", padding=10)
        qbox.pack(fill="x", pady=(10, 0))

        self.q_label = ttk.Label(qbox, text="開始を押してください", font=("Helvetica", 22, "bold"))
        self.q_label.pack(anchor="w")

        ans_row = ttk.Frame(qbox)
        ans_row.pack(fill="x", pady=(10, 0))

        ttk.Label(ans_row, text="答え（例: F#）", font=("Helvetica", 12)).pack(side="left")
        self.ans_var = tk.StringVar()
        self.ans_entry = ttk.Entry(ans_row, textvariable=self.ans_var, width=12, font=("Helvetica", 16))
        self.ans_entry.pack(side="left", padx=(10, 10))
        self.ans_entry.bind("<Return>", lambda e: self.submit())

        self.submit_btn = ttk.Button(ans_row, text="決定", command=self.submit)
        self.submit_btn.pack(side="left")
        self.pass_btn = ttk.Button(ans_row, text="パス", command=self.pass_q)
        self.pass_btn.pack(side="left", padx=(8, 0))

        self.feedback = ttk.Label(qbox, text="", font=("Helvetica", 12))
        self.feedback.pack(anchor="w", pady=(8, 0))

        # ===== Status + log =====
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

    def select_all_strings(self):
        for v in self.string_vars.values():
            v.set(True)

    def clear_all_strings(self):
        for v in self.string_vars.values():
            v.set(False)

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
        self.pass_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.start_btn.configure(state="normal")

    def _set_running(self):
        self.ans_entry.configure(state="normal")
        self.submit_btn.configure(state="normal")
        self.pass_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        self.start_btn.configure(state="disabled")
        self.ans_entry.focus_set()

    def _read_settings(self):
        enabled_strings = [s for s, v in self.string_vars.items() if v.get()]
        if not enabled_strings:
            raise ValueError("出題する弦が選ばれていません。")

        try:
            total = int(self.total_var.get())
            if total <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("問題数は1以上の整数で。")

        try:
            limit = float(self.limit_var.get())
            if limit <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("制限秒は0より大きい数で（例: 2.0）")

        try:
            fret_max = int(self.fret_var.get())
            if fret_max < 0 or fret_max > 24:
                raise ValueError
        except ValueError:
            raise ValueError("フレット上限は0〜24の整数で。")

        mode = self.mode_var.get()
        return enabled_strings, total, limit, fret_max, mode

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
            enabled_strings, total, limit, fret_max, mode_text = self._read_settings()
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
            self.enabled_strings = enabled_strings  # 表示用に残す
            self.mode = "復習"
        else:
            self.review_mode = False
            self.enabled_strings = enabled_strings
            self.total_q = total
            self.mode = "通常"

        self.time_limit = limit
        self.fret_max = fret_max

        self.q_index = 0
        self.correct = 0
        self.passed = 0
        self.records = []
        if not self.review_mode:
            self.wrong_pool = []

        # session id
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
    def next_q(self):
        self.in_quiz = True
        self.q_index += 1
        if self.q_index > self.total_q:
            self.finish_and_save()
            return

        if self.review_mode:
            s, f, a = self.review_queue[self.q_index - 1]
        else:
            s = random.choice(self.enabled_strings)
            f = random.randint(0, self.fret_max)
            a = calc_note(s, f)

        self.current = (s, f, a)
        self.q_start = time.time()
        tag = "[復習] " if self.review_mode else ""
        self.q_label.configure(text=f"{tag}Q{self.q_index}: {s}弦  {f}f は？")
        self.feedback.configure(text="")
        self.ans_var.set("")
        self._update_stat()

    def submit(self):
        if not self.in_quiz or not self.current:
            return

        s, f, a = self.current
        user = normalize_answer(self.ans_var.get())
        rt = time.time() - self.q_start

        is_pass = 0
        is_correct = 1 if user == a else 0
        if is_correct:
            self.correct += 1
            self.feedback.configure(text=f"OK  ({rt:.2f}s)")
            self.log_append(f"OK  : {s}弦{f}f = {a}  ({rt:.2f}s)\n")
        else:
            self.feedback.configure(text=f"MISS 正解={a}  ({rt:.2f}s)")
            self.log_append(f"MISS: {s}弦{f}f  入力={user or '∅'}  正解={a}  ({rt:.2f}s)\n")
            if not self.review_mode:
                self.wrong_pool.append((s, f, a))

        self.records.append(QuestionRecord(
            session_id=self.session_id,
            q_index=self.q_index,
            string=s,
            fret=f,
            correct_note=a,
            user_input=user,
            is_correct=is_correct,
            is_pass=is_pass,
            response_time_sec=rt
        ))

        self._update_stat()
        self.after(200, self.next_q)

    def pass_q(self):
        if not self.in_quiz or not self.current:
            return

        s, f, a = self.current
        rt = time.time() - self.q_start
        self.passed += 1

        self.feedback.configure(text=f"PASS 正解={a}  ({rt:.2f}s)")
        self.log_append(f"PASS: {s}弦{f}f  正解={a}  ({rt:.2f}s)\n")

        if not self.review_mode:
            self.wrong_pool.append((s, f, a))

        self.records.append(QuestionRecord(
            session_id=self.session_id,
            q_index=self.q_index,
            string=s,
            fret=f,
            correct_note=a,
            user_input="",
            is_correct=0,
            is_pass=1,
            response_time_sec=rt
        ))

        self._update_stat()
        self.after(200, self.next_q)

    def _update_stat(self):
        elapsed = time.time() - self.session_start if self.session_start else 0.0
        wrong = len(self.wrong_pool) if not self.review_mode else max(0, self.q_index - self.correct - self.passed)
        self.progress.configure(text=f"進捗: {self.q_index}/{self.total_q}")
        self.score.configure(text=f"正解: {self.correct}")
        self.timer.configure(text=f"経過: {elapsed:.1f}s")
        # 間違い表示：通常は wrong_pool、復習は概算
        if not self.review_mode:
            self.wrong_label.configure(text=f"間違い: {len(self.wrong_pool)}")
        else:
            self.wrong_label.configure(text=f"間違い: {max(0, self.q_index - self.correct)}")

    def _tick(self):
        if not self.in_quiz:
            return
        # 制限超過の表示をしない（ログ・採点・保存はそのまま）
        self._update_stat()
        self.after(100, self._tick)

    # -------- saving & best --------
    def finish_and_save(self):
        self.in_quiz = False
        elapsed = time.time() - self.session_start
        avg = elapsed / max(1, self.total_q)
        wrong = self.total_q - self.correct - self.passed
        acc = self.correct / max(1, self.total_q)
        g = grade(self.correct, self.total_q)

        # セッション要約を作る（統計処理向け）
        enabled_str = ",".join(map(str, sorted(self.enabled_strings, reverse=True)))
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = SessionSummary(
            session_id=self.session_id,
            timestamp_local=ts,
            mode=self.mode,
            enabled_strings=enabled_str,
            fret_max=self.fret_max,
            total_q=self.total_q,
            time_limit_sec=self.time_limit,
            correct=self.correct,
            wrong=wrong,
            passed=self.passed,
            accuracy=acc,
            elapsed_sec=elapsed,
            avg_sec_per_q=avg,
            grade=g
        )

        # 保存（完走したときだけ）
        ensure_log_dir()

        # 1問ログ
        q_fields = list(asdict(self.records[0]).keys()) if self.records else [
            "session_id","q_index","string","fret","correct_note","user_input",
            "is_correct","is_pass","response_time_sec"
        ]
        for r in self.records:
            append_csv(QUESTIONS_CSV, q_fields, asdict(r))

        # 要約ログ
        s_fields = list(asdict(summary).keys())
        append_csv(SUMMARY_CSV, s_fields, asdict(summary))

        # ベスト判定（通常/復習どちらも記録する。嫌なら通常だけに変更可）
        candidate_best = {
            "session_id": summary.session_id,
            "timestamp_local": summary.timestamp_local,
            "mode": summary.mode,
            "total_q": summary.total_q,
            "correct": summary.correct,
            "accuracy": summary.accuracy,
            "elapsed_sec": summary.elapsed_sec,
            "avg_sec_per_q": summary.avg_sec_per_q,
            "fret_max": summary.fret_max,
            "enabled_strings": summary.enabled_strings,
        }
        best = read_best()
        if is_new_best(candidate_best, best):
            write_best(candidate_best)
            best = candidate_best
            self.log_append("\n★ ベスト更新！\n")
        self._show_best_on_start()  # 表示更新

        # UI表示
        self.log_append("\n=== 完走：保存しました ===\n")
        self.log_append(f"summary.csv / questions.csv に追記（{LOG_DIR}/）\n")
        self.log_append(
            f"正解 {self.correct}/{self.total_q}  "
            f"PASS {self.passed}  "
            f"平均 {avg:.2f}s/問  合計 {elapsed:.2f}s  Grade {g}\n"
        )

        self.q_label.configure(text=f"完走！ 正解 {self.correct}/{self.total_q}（平均 {avg:.2f}s/問）")
        self.feedback.configure(text=f"保存: {LOG_DIR}/  | ベストは上部に表示")
        self._set_idle()

if __name__ == "__main__":
    app = FretTrainerUI()
    app.mainloop()
