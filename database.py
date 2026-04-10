"""
database.py — SQLite-слой для Workout Tracker Bot
"""
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional


class Database:
    def __init__(self, db_path: str = "workouts.db"):
        self.db_path = db_path
        self._init_db()

    # ─── Подключение ──────────────────────────────────────────────────────────

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ─── Схема ────────────────────────────────────────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id   INTEGER PRIMARY KEY,
                    created_at TEXT DEFAULT (datetime('now')),
                    remind_hour INTEGER DEFAULT NULL,
                    remind_days TEXT DEFAULT NULL
                );

                -- Каждая завершённая тренировка
                CREATE TABLE IF NOT EXISTS workout_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    workout_type TEXT NOT NULL,
                    date         TEXT NOT NULL DEFAULT (datetime('now')),
                    exercises_json TEXT NOT NULL,
                    notes        TEXT DEFAULT '',
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ws_user_date
                    ON workout_sessions(user_id, date DESC);

                -- Кастомные упражнения, добавленные пользователем
                CREATE TABLE IF NOT EXISTS custom_exercises (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    workout_type TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    sets         INTEGER NOT NULL DEFAULT 3,
                    reps         TEXT NOT NULL DEFAULT '10',
                    UNIQUE(user_id, workout_type, name)
                );

                -- Базовые упражнения, которые пользователь скрыл
                CREATE TABLE IF NOT EXISTS removed_exercises (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    workout_type TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    UNIQUE(user_id, workout_type, name)
                );
            """)

    # ─── Пользователи ─────────────────────────────────────────────────────────

    def ensure_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))

    def set_reminder(self, user_id: int, hour: Optional[int], days: Optional[list]):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET remind_hour=?, remind_days=? WHERE user_id=?",
                (hour, json.dumps(days) if days else None, user_id)
            )

    def get_reminder(self, user_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT remind_hour, remind_days FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if not row:
                return {}
            return {
                "hour": row["remind_hour"],
                "days": json.loads(row["remind_days"]) if row["remind_days"] else None,
            }

    # ─── Сохранение тренировки ────────────────────────────────────────────────

    def save_workout_session(
        self, user_id: int, workout_type: str, exercises: list,
        notes: str = "", date_override: Optional[str] = None
    ) -> int:
        serializable = []
        for ex in exercises:
            e = dict(ex)
            if "sets_data" in e:
                e["sets_data"] = [list(s) for s in e["sets_data"]]
            serializable.append(e)

        date_str = date_override or datetime.now().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO workout_sessions(user_id,workout_type,date,exercises_json,notes)"
                " VALUES(?,?,?,?,?)",
                (user_id, workout_type, date_str,
                 json.dumps(serializable, ensure_ascii=False), notes)
            )
            return cur.lastrowid

    def delete_session(self, user_id: int, session_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM workout_sessions WHERE id=? AND user_id=?",
                (session_id, user_id)
            )
            return cur.rowcount > 0

    # ─── Чтение тренировок ────────────────────────────────────────────────────

    def _deserialize_session(self, row) -> dict:
        exercises = json.loads(row["exercises_json"])
        for ex in exercises:
            if "sets_data" in ex and ex["sets_data"]:
                ex["sets_data"] = [tuple(s) for s in ex["sets_data"]]
        return {
            "id":           row["id"],
            "workout_type": row["workout_type"],
            "date":         row["date"],
            "exercises":    exercises,
            "notes":        row["notes"] or "",
        }

    def get_last_workout(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id,workout_type,date,exercises_json,notes FROM workout_sessions"
                " WHERE user_id=? ORDER BY date DESC LIMIT 1",
                (user_id,)
            ).fetchone()
            return self._deserialize_session(row) if row else None

    def get_last_n_workouts(self, user_id: int, n: int = 2) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id,workout_type,date,exercises_json,notes FROM workout_sessions"
                " WHERE user_id=? ORDER BY date DESC LIMIT ?",
                (user_id, n)
            ).fetchall()
            return [self._deserialize_session(r) for r in rows]

    def get_history(self, user_id: int, limit: int = 10, workout_type: Optional[str] = None) -> list:
        with self._conn() as conn:
            if workout_type:
                rows = conn.execute(
                    "SELECT id,workout_type,date,exercises_json,notes FROM workout_sessions"
                    " WHERE user_id=? AND workout_type=? ORDER BY date DESC LIMIT ?",
                    (user_id, workout_type, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,workout_type,date,exercises_json,notes FROM workout_sessions"
                    " WHERE user_id=? ORDER BY date DESC LIMIT ?",
                    (user_id, limit)
                ).fetchall()
            return [self._deserialize_session(r) for r in rows]

    def get_session_by_id(self, user_id: int, session_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id,workout_type,date,exercises_json,notes FROM workout_sessions"
                " WHERE id=? AND user_id=?",
                (session_id, user_id)
            ).fetchone()
            return self._deserialize_session(row) if row else None

    def get_last_exercise_result(self, user_id: int, ex_name: str) -> Optional[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT exercises_json FROM workout_sessions"
                " WHERE user_id=? ORDER BY date DESC LIMIT 30",
                (user_id,)
            ).fetchall()
            for row in rows:
                exercises = json.loads(row["exercises_json"])
                for ex in exercises:
                    if ex["name"] == ex_name and not ex.get("skipped"):
                        if "sets_data" in ex and ex["sets_data"]:
                            ex["sets_data"] = [tuple(s) for s in ex["sets_data"]]
                        return ex
        return None

    def count_sessions(self, user_id: int) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM workout_sessions WHERE user_id=?", (user_id,)
            ).fetchone()[0]

    # ─── Статистика ───────────────────────────────────────────────────────────

    def get_exercise_history(self, user_id: int, ex_name: str, limit: int = 10) -> list:
        """Возвращает список (date, sets_data) для конкретного упражнения."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT date, exercises_json FROM workout_sessions"
                " WHERE user_id=? ORDER BY date DESC LIMIT 50",
                (user_id,)
            ).fetchall()
        results = []
        for row in rows:
            exercises = json.loads(row["exercises_json"])
            for ex in exercises:
                if ex["name"] == ex_name and not ex.get("skipped") and ex.get("sets_data"):
                    sets_data = [tuple(s) for s in ex["sets_data"]]
                    results.append({"date": row["date"], "sets_data": sets_data})
                    break
            if len(results) >= limit:
                break
        return results

    def get_personal_record(self, user_id: int, ex_name: str) -> Optional[dict]:
        """Максимальный вес когда-либо в данном упражнении."""
        history = self.get_exercise_history(user_id, ex_name, limit=100)
        best_weight = None
        best_date = None
        for entry in history:
            for w, r in entry["sets_data"]:
                if isinstance(w, (int, float)) and (best_weight is None or w > best_weight):
                    best_weight = w
                    best_date = entry["date"]
        if best_weight is None:
            return None
        return {"weight": best_weight, "date": best_date}

    def get_volume_per_session(self, user_id: int, limit: int = 20) -> list:
        """Объём (кг×повт) по каждой тренировке."""
        sessions = self.get_history(user_id, limit=limit)
        result = []
        for s in sessions:
            vol = 0
            for ex in s["exercises"]:
                for w, r in ex.get("sets_data", []):
                    if isinstance(w, (int, float)):
                        vol += w * r
            result.append({"date": s["date"], "type": s["workout_type"], "volume": vol})
        return result

    def compare_sessions(self, user_id: int, sid1: int, sid2: int) -> Optional[dict]:
        """Сравнивает две тренировки по id."""
        s1 = self.get_session_by_id(user_id, sid1)
        s2 = self.get_session_by_id(user_id, sid2)
        if not s1 or not s2:
            return None
        return {"s1": s1, "s2": s2}

    def get_streak(self, user_id: int) -> dict:
        """Серия тренировок: текущая и максимальная."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT date FROM workout_sessions WHERE user_id=? ORDER BY date DESC",
                (user_id,)
            ).fetchall()
        if not rows:
            return {"current": 0, "best": 0, "total": 0}

        dates = sorted(
            set(datetime.fromisoformat(r["date"]).date() for r in rows),
            reverse=True
        )
        total = len(dates)

        # Текущая серия
        current = 1
        for i in range(1, len(dates)):
            if (dates[i - 1] - dates[i]).days <= 2:   # даём 1 день отдыха
                current += 1
            else:
                break

        # Максимальная серия
        best = 1
        streak = 1
        for i in range(1, len(dates)):
            if (dates[i - 1] - dates[i]).days <= 2:
                streak += 1
                best = max(best, streak)
            else:
                streak = 1

        return {"current": current, "best": best, "total": total}

    def get_weekly_summary(self, user_id: int, weeks: int = 4) -> list:
        """Количество тренировок по неделям за последние N недель."""
        now = datetime.now()
        result = []
        for w in range(weeks - 1, -1, -1):
            start = (now - timedelta(weeks=w + 1)).date()
            end = (now - timedelta(weeks=w)).date()
            with self._conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM workout_sessions"
                    " WHERE user_id=? AND date(date)>? AND date(date)<=?",
                    (user_id, start.isoformat(), end.isoformat())
                ).fetchone()[0]
            label = f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"
            result.append({"week": label, "count": count})
        return result

    # ─── Кастомные упражнения ─────────────────────────────────────────────────

    def add_custom_exercise(self, user_id: int, workout_type: str, name: str, sets: int, reps: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO custom_exercises(user_id,workout_type,name,sets,reps)"
                " VALUES(?,?,?,?,?)",
                (user_id, workout_type, name, sets, reps)
            )
            conn.execute(
                "DELETE FROM removed_exercises WHERE user_id=? AND workout_type=? AND name=?",
                (user_id, workout_type, name)
            )

    def get_custom_exercises(self, user_id: int, workout_type: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name,sets,reps FROM custom_exercises WHERE user_id=? AND workout_type=?",
                (user_id, workout_type)
            ).fetchall()
            return [{"name": r["name"], "sets": r["sets"], "reps": r["reps"]} for r in rows]

    def remove_exercise(self, user_id: int, workout_type: str, name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO removed_exercises(user_id,workout_type,name) VALUES(?,?,?)",
                (user_id, workout_type, name)
            )

    def remove_custom_exercise(self, user_id: int, workout_type: str, name: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM custom_exercises WHERE user_id=? AND workout_type=? AND name=?",
                (user_id, workout_type, name)
            )

    def get_removed_exercises(self, user_id: int, workout_type: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name FROM removed_exercises WHERE user_id=? AND workout_type=?",
                (user_id, workout_type)
            ).fetchall()
            return [r["name"] for r in rows]

    def restore_exercise(self, user_id: int, workout_type: str, name: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM removed_exercises WHERE user_id=? AND workout_type=? AND name=?",
                (user_id, workout_type, name)
            )
