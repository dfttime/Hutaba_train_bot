"""
Workout Tracker Bot — полная версия
Команды: /start /history /stats /compare /records /cancel
"""
import logging
import os
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from database import Database
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Состояния разговора ──────────────────────────────────────────────────────
(
    MAIN_MENU,
    CHOOSE_WORKOUT,
    ENTERING_SET,
    CONFIRM_RESULTS,
    ADD_EX_NAME,
    ADD_EX_SETS_COUNT,
    ADD_EX_REPS,
    MANAGE_EXERCISES,
    COMPARE_PICK_FIRST,
    COMPARE_PICK_SECOND,
    STATS_EXERCISE,
    ADD_NOTE,
) = range(12)

db = Database("workouts.db")

# ─── Программа тренировок ─────────────────────────────────────────────────────
WORKOUTS = {
    "A": {
        "name": "Тренировка 🅰️",
        "exercises": [
            {"name": "Жим ногами",                 "sets": 3, "reps": "8–12"},
            {"name": "Жим лёжа",                   "sets": 3, "reps": "6–10"},
            {"name": "Тяга горизонтального блока", "sets": 3, "reps": "8–12"},
            {"name": "Тяга вертикального блока",   "sets": 3, "reps": "8–12"},
            {"name": "Махи гантелями в стороны",   "sets": 3, "reps": "12–15"},
            {"name": "Сгибание ног лёжа",          "sets": 3, "reps": "10–15"},
            {"name": "Пресс",                      "sets": 3, "reps": "max"},
        ],
    },
    "B": {
        "name": "Тренировка 🅱️",
        "exercises": [
            {"name": "Болгарские выпады",                  "sets": 3, "reps": "8–12"},
            {"name": "Жим гантелей вверх",                 "sets": 3, "reps": "8–12"},
            {"name": "Тяга вертикального блока",           "sets": 3, "reps": "8–12"},
            {"name": "Тяга горизонтального блока",         "sets": 3, "reps": "8–12"},
            {"name": "Гиперэкстензия",                     "sets": 3, "reps": "12–15"},
            {"name": "Разгибание рук на блоке (трицепс)",  "sets": 3, "reps": "10–12"},
            {"name": "Подъёмы на икры",                    "sets": 4, "reps": "12–20"},
        ],
    },
}

# ─── Утилиты ──────────────────────────────────────────────────────────────────

def get_exercises(user_id: int, workout_type: str) -> list:
    base = [ex.copy() for ex in WORKOUTS[workout_type]["exercises"]]
    removed = db.get_removed_exercises(user_id, workout_type)
    custom = db.get_custom_exercises(user_id, workout_type)
    result = [ex for ex in base if ex["name"] not in removed]
    result.extend(custom)
    return result


def parse_set_input(text: str):
    """
    Парсит ввод одного подхода.
    "60 10" / "60кг 10" / "60x10" → (60, 10)
    "10"                           → (None, 10)  — вес из контекста
    "без веса 15" / "б/в 15"       → ("б/в", 15)
    Возвращает (weight, reps) или None.
    """
    t = text.strip().lower()
    no_w = re.match(r"(без\s*веса|б/?в|bodyweight|bw)[^\d]*(\d+)", t)
    if no_w:
        return ("б/в", int(no_w.group(2)))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:кг|kg)?\s*[xхх×/,\s]\s*(\d+)", t)
    if m:
        w = float(m.group(1).replace(",", "."))
        return (int(w) if w == int(w) else w, int(m.group(2)))
    only = re.fullmatch(r"(\d+)", t)
    if only:
        return (None, int(only.group(1)))
    return None


def fmt_set(w, r) -> str:
    if w is None:
        return f"{r} повт."
    return f"{w} кг × {r} повт."


def sets_summary(sets_data: list) -> str:
    if not sets_data:
        return "—"
    return "  |  ".join(fmt_set(w, r) for w, r in sets_data)


def session_volume(exercises: list) -> float:
    vol = 0.0
    for ex in exercises:
        for w, r in ex.get("sets_data", []):
            if isinstance(w, (int, float)):
                vol += w * r
    return vol


def session_label(session: dict) -> str:
    dt = datetime.fromisoformat(session["date"]).strftime("%d.%m.%Y %H:%M")
    return f"Тренировка {session['workout_type']} — {dt}"


def build_bar(current: int, total: int) -> str:
    return "[" + "▓" * current + "░" * (total - current) + f"] {current}/{total}"


def last_hint(user_id: int, ex_name: str) -> str:
    last = db.get_last_exercise_result(user_id, ex_name)
    if not last or not last.get("sets_data"):
        return ""
    return f"\n📖 _Прошлый раз: {sets_summary(last['sets_data'])}_"


# ─── ГЛАВНОЕ МЕНЮ ─────────────────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏋️ Добавить тренировку", callback_data="menu_train"),
        ],
        [
            InlineKeyboardButton("📋 История",     callback_data="menu_history"),
            InlineKeyboardButton("📊 Статистика",  callback_data="menu_stats"),
        ],
        [
            InlineKeyboardButton("🏆 Рекорды",     callback_data="menu_records"),
            InlineKeyboardButton("⚖️ Сравнить",    callback_data="menu_compare"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.ensure_user(user_id)

    streak = db.get_streak(user_id)
    total  = streak["total"]
    cur_s  = streak["current"]

    last_sessions = db.get_last_n_workouts(user_id, 2)
    last_line = ""
    if last_sessions:
        s = last_sessions[0]
        dt = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y")
        last_type = s["workout_type"]
        next_type = "B" if last_type == "A" else "A"
        last_line = (
            f"\n📅 Последняя: *Тренировка {last_type}* ({dt})"
            f"\n💡 Рекомендуется сегодня: *Тренировка {next_type}*"
        )

    streak_line = ""
    if total > 0:
        streak_line = f"\n🔥 Серия: *{cur_s}* тр.  |  Всего: *{total}* тр."

    text = (
        f"💪 *Workout Tracker*"
        f"{last_line}"
        f"{streak_line}\n\n"
        f"Выбери действие:"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    return MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "menu_train":
        return await show_workout_choice(update, context)
    elif action == "menu_history":
        return await show_history_menu(update, context)
    elif action == "menu_stats":
        return await show_stats_menu(update, context)
    elif action == "menu_records":
        return await show_records(update, context)
    elif action == "menu_compare":
        return await compare_start(update, context)
    elif action == "back_main":
        return await start(update, context)


# ─── ВЫБОР ТРЕНИРОВКИ ─────────────────────────────────────────────────────────

async def show_workout_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    last_sessions = db.get_last_n_workouts(user_id, 2)

    def session_block(s: dict, label: str) -> str:
        dt = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y")
        lines = [f"*{label} — Тренировка {s['workout_type']}* ({dt})"]
        for ex in s["exercises"]:
            if ex.get("skipped"):
                lines.append(f"  ⏭ {ex['name']}")
            elif ex.get("sets_data"):
                lines.append(f"  • {ex['name']}: {sets_summary(ex['sets_data'])}")
        vol = session_volume(s["exercises"])
        if vol:
            lines.append(f"  📦 Объём: {vol:,.0f} кг")
        return "\n".join(lines)

    preview = ""
    if len(last_sessions) >= 1:
        preview += "\n\n" + session_block(last_sessions[0], "Последняя")
    if len(last_sessions) >= 2:
        preview += "\n\n" + session_block(last_sessions[1], "Позапрошлая")
    if not preview:
        preview = "\n\n_Тренировок ещё не было. Самое время начать! 💪_"

    keyboard = [
        [
            InlineKeyboardButton("🅰️  Тренировка A", callback_data="workout_A"),
            InlineKeyboardButton("🅱️  Тренировка B", callback_data="workout_B"),
        ],
        [InlineKeyboardButton("↩️ Назад", callback_data="back_main")],
    ]
    await query.edit_message_text(
        f"🏋️ *Выбери тренировку:*{preview}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return CHOOSE_WORKOUT


# ─── СТАРТ ТРЕНИРОВКИ ─────────────────────────────────────────────────────────

async def choose_workout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_main":
        return await start(update, context)

    workout_type = query.data.split("_")[1]
    user_id = query.from_user.id
    exercises = get_exercises(user_id, workout_type)

    context.user_data.update({
        "workout_type": workout_type,
        "exercises": exercises,
        "ex_idx": 0,
        "set_idx": 0,
        "results": [],
        "current_sets": [],
        "user_id": user_id,
    })

    workout_name = WORKOUTS[workout_type]["name"]
    ex_list = "\n".join(
        f"  {i+1}. {ex['name']}  —  {ex['sets']}×{ex['reps']}"
        for i, ex in enumerate(exercises)
    )
    await query.edit_message_text(
        f"*{workout_name}* — {len(exercises)} упражнений\n\n{ex_list}\n\n▶️ Начинаем!",
        parse_mode="Markdown",
    )
    await _ask_set(context, query.message.chat_id)
    return ENTERING_SET


# ─── ЦИКЛ ВВОДА ПОДХОДОВ ──────────────────────────────────────────────────────

async def _ask_set(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    exercises = context.user_data["exercises"]
    ex_idx    = context.user_data["ex_idx"]
    set_idx   = context.user_data["set_idx"]
    user_id   = context.user_data.get("user_id", 0)

    ex          = exercises[ex_idx]
    total_sets  = ex["sets"]
    current_sets = context.user_data["current_sets"]

    # Уже введённые подходы
    done_str = ""
    if current_sets:
        lines = [f"  подход {i+1}: {fmt_set(w, r)}" for i, (w, r) in enumerate(current_sets)]
        done_str = "\n" + "\n".join(lines) + "\n"

    fmt_hint = ""
    if ex_idx == 0 and set_idx == 0:
        fmt_hint = (
            "\n\n📝 *Формат:*\n"
            "`60 10` — 60 кг, 10 повт.\n"
            "`10` — только повторения (вес как в прошлом подходе)\n"
            "`без веса 15` — без отягощения"
        )

    text = (
        f"🏋️ *{ex['name']}*\n"
        f"{build_bar(ex_idx + 1, len(exercises))}\n"
        f"Подход *{set_idx + 1}* из *{total_sets}*  •  цель: {ex['reps']} повт."
        f"{last_hint(user_id, ex['name'])}"
        f"{done_str}"
        f"{fmt_hint}\n\n"
        f"Введи вес и повторения:"
    )

    keyboard = []
    if current_sets:
        lw, lr = current_sets[-1]
        keyboard.append([
            InlineKeyboardButton(f"🔁 Повторить ({fmt_set(lw, lr)})", callback_data="repeat_set")
        ])

    last_ex = db.get_last_exercise_result(user_id, ex["name"])
    if last_ex and last_ex.get("sets_data") and set_idx == 0 and not current_sets:
        keyboard.append([
            InlineKeyboardButton("📋 Как в прошлый раз (все подходы)", callback_data="copy_last")
        ])

    keyboard.append([InlineKeyboardButton("⏭ Пропустить упражнение", callback_data="skip_ex")])

    await context.bot.send_message(
        chat_id, text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def receive_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data["user_id"] = user_id
    text = update.message.text.strip()
    parsed = parse_set_input(text)

    if parsed is None:
        await update.message.reply_text(
            "⚠️ *Не понял.* Примеры:\n"
            "`60 10` — 60 кг, 10 повторений\n"
            "`10` — повторения (вес сохранится)\n"
            "`без веса 15` — без отягощения",
            parse_mode="Markdown",
        )
        return ENTERING_SET

    w, r = parsed
    if w is None:
        cs = context.user_data["current_sets"]
        if cs:
            w = cs[-1][0]
        else:
            ex = context.user_data["exercises"][context.user_data["ex_idx"]]
            le = db.get_last_exercise_result(user_id, ex["name"])
            if le and le.get("sets_data"):
                w = le["sets_data"][0][0]
            else:
                await update.message.reply_text(
                    "⚠️ Для первого подхода укажи вес: `60 10`", parse_mode="Markdown"
                )
                return ENTERING_SET

    await _record_set(context, update.effective_chat.id, w, r)
    return ENTERING_SET


async def repeat_set_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cs = context.user_data["current_sets"]
    if cs:
        w, r = cs[-1]
        await _record_set(context, q.message.chat_id, w, r)
    return ENTERING_SET


async def copy_last_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    ex = context.user_data["exercises"][context.user_data["ex_idx"]]
    le = db.get_last_exercise_result(user_id, ex["name"])
    if not le or not le.get("sets_data"):
        await q.message.reply_text("Нет данных прошлой тренировки.")
        return ENTERING_SET

    context.user_data["results"].append({
        "name": ex["name"], "sets_plan": f"{ex['sets']}×{ex['reps']}",
        "sets_data": le["sets_data"],
    })
    await q.message.reply_text(
        f"✅ *{ex['name']}*\n📋 {sets_summary(le['sets_data'])}",
        parse_mode="Markdown",
    )
    return await _next_exercise(context, q.message.chat_id)


async def skip_ex_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Пропущено ⏭")
    ex = context.user_data["exercises"][context.user_data["ex_idx"]]
    context.user_data["results"].append({
        "name": ex["name"], "sets_plan": f"{ex['sets']}×{ex['reps']}",
        "sets_data": [], "skipped": True,
    })
    await q.message.reply_text(f"⏭ *{ex['name']}* — пропущено.", parse_mode="Markdown")
    return await _next_exercise(context, q.message.chat_id)


async def _record_set(context: ContextTypes.DEFAULT_TYPE, chat_id: int, w, r: int):
    context.user_data["current_sets"].append((w, r))
    exercises   = context.user_data["exercises"]
    ex_idx      = context.user_data["ex_idx"]
    ex          = exercises[ex_idx]
    set_num     = context.user_data["set_idx"] + 1
    total_sets  = ex["sets"]

    await context.bot.send_message(
        chat_id, f"✔️ Подход {set_num}: *{fmt_set(w, r)}*", parse_mode="Markdown"
    )

    if set_num >= total_sets:
        sets_data = list(context.user_data["current_sets"])
        context.user_data["results"].append({
            "name": ex["name"], "sets_plan": f"{ex['sets']}×{ex['reps']}",
            "sets_data": sets_data,
        })
        await context.bot.send_message(
            chat_id, f"🏁 *{ex['name']}* — готово!\n{sets_summary(sets_data)}",
            parse_mode="Markdown",
        )
        context.user_data["ex_idx"]      += 1
        context.user_data["set_idx"]      = 0
        context.user_data["current_sets"] = []

        if context.user_data["ex_idx"] >= len(exercises):
            await show_confirm_chat(context, chat_id)
        else:
            nxt = exercises[context.user_data["ex_idx"]]
            await context.bot.send_message(
                chat_id,
                f"──────────────\n➡️ Следующее: *{nxt['name']}*",
                parse_mode="Markdown",
            )
            await _ask_set(context, chat_id)
    else:
        context.user_data["set_idx"] = set_num
        await _ask_set(context, chat_id)


async def _next_exercise(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    context.user_data["ex_idx"]      += 1
    context.user_data["set_idx"]      = 0
    context.user_data["current_sets"] = []
    exercises = context.user_data["exercises"]

    if context.user_data["ex_idx"] >= len(exercises):
        await show_confirm_chat(context, chat_id)
        return CONFIRM_RESULTS

    nxt = exercises[context.user_data["ex_idx"]]
    await context.bot.send_message(
        chat_id, f"──────────────\n➡️ Следующее: *{nxt['name']}*", parse_mode="Markdown"
    )
    await _ask_set(context, chat_id)
    return ENTERING_SET


# ─── ИТОГОВЫЙ ЭКРАН ───────────────────────────────────────────────────────────

def _summary_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    results      = context.user_data["results"]
    workout_type = context.user_data["workout_type"]
    workout_name = WORKOUTS[workout_type]["name"]

    lines = [f"*{workout_name} — итоги:*\n"]
    for r in results:
        if r.get("skipped"):
            lines.append(f"⏭ {r['name']} — пропущено")
        elif r["sets_data"]:
            lines.append(f"✅ *{r['name']}*  ({r['sets_plan']})\n   {sets_summary(r['sets_data'])}")
        else:
            lines.append(f"⬜ *{r['name']}* — нет данных")

    vol = session_volume(results)
    if vol:
        lines.append(f"\n📦 Объём тренировки: *{vol:,.0f} кг*")
    return "\n".join(lines)


async def show_confirm_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    keyboard = [
        [InlineKeyboardButton("✅ Сохранить", callback_data="save"),
         InlineKeyboardButton("📝 Добавить заметку", callback_data="add_note")],
        [InlineKeyboardButton("➕ Добавить упражнение", callback_data="add_exercise"),
         InlineKeyboardButton("🗑 Управление списком",  callback_data="manage_exercises")],
        [InlineKeyboardButton("🔄 Начать заново", callback_data="restart")],
    ]
    await context.bot.send_message(
        chat_id, _summary_text(context),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def save_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id      = q.from_user.id
    workout_type = context.user_data.get("workout_type")
    results      = context.user_data.get("results", [])
    notes        = context.user_data.get("notes", "")

    if not workout_type or not results:
        await q.message.reply_text("⚠️ Нет данных для сохранения. Начни тренировку заново: /start")
        return ConversationHandler.END

    db.save_workout_session(user_id, workout_type, results, notes=notes)

    total_sets = sum(len(r["sets_data"]) for r in results)
    # Защита: пропускаем упражнения без данных
    total_reps = sum(r for ex in results for _, r in ex.get("sets_data", []))
    vol        = session_volume(results)
    streak     = db.get_streak(user_id)

    text = (
        f"🎉 *Тренировка сохранена!*\n\n"
        f"📊 Подходов: *{total_sets}*\n"
        f"🔁 Повторений: *{total_reps}*\n"
    )
    if vol:
        text += f"📦 Объём: *{vol:,.0f} кг*\n"
    text += f"🔥 Серия: *{streak['current']}* тренировок\n\n"
    if notes:
        text += f"📝 _{notes}_\n\n"
    text += "Отличная работа! 💪\n\nНажми /start для следующей тренировки."

    await q.edit_message_text(text, parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


async def restart_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id      = q.from_user.id
    workout_type = context.user_data["workout_type"]
    context.user_data.update({
        "exercises": get_exercises(user_id, workout_type),
        "ex_idx": 0, "set_idx": 0,
        "results": [], "current_sets": [], "user_id": user_id,
    })
    await q.edit_message_text("🔄 Начинаем заново!")
    await _ask_set(context, q.message.chat_id)
    return ENTERING_SET


# ─── ЗАМЕТКА К ТРЕНИРОВКЕ ────────────────────────────────────────────────────

async def add_note_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "📝 Введи заметку к тренировке (самочувствие, наблюдения и т.д.):"
    )
    return ADD_NOTE


async def receive_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["notes"] = update.message.text.strip()
    await update.message.reply_text(f"📝 Заметка сохранена: _{context.user_data['notes']}_",
                                    parse_mode="Markdown")
    await show_confirm_chat(context, update.effective_chat.id)
    return CONFIRM_RESULTS


# ─── ДОБАВИТЬ УПРАЖНЕНИЕ ──────────────────────────────────────────────────────

async def add_exercise_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("➕ *Новое упражнение*\n\nВведи название:", parse_mode="Markdown")
    return ADD_EX_NAME


async def add_ex_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_ex_name"] = update.message.text.strip()
    await update.message.reply_text("Сколько подходов? Например `3`:", parse_mode="Markdown")
    return ADD_EX_SETS_COUNT


async def add_ex_sets_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if not t.isdigit() or int(t) < 1:
        await update.message.reply_text("Введи целое число, например `3`:", parse_mode="Markdown")
        return ADD_EX_SETS_COUNT
    context.user_data["new_ex_sets"] = int(t)
    await update.message.reply_text("Цель по повторениям? Например `10–12`:", parse_mode="Markdown")
    return ADD_EX_REPS


async def add_ex_reps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id      = update.effective_user.id
    name         = context.user_data["new_ex_name"]
    sets         = context.user_data["new_ex_sets"]
    reps         = update.message.text.strip()
    workout_type = context.user_data["workout_type"]

    db.add_custom_exercise(user_id, workout_type, name, sets, reps)
    context.user_data["exercises"].append({"name": name, "sets": sets, "reps": reps})

    await update.message.reply_text(
        f"✅ *{name}* добавлено ({sets}×{reps}). Сохранено навсегда.",
        parse_mode="Markdown",
    )
    await show_confirm_chat(context, update.effective_chat.id)
    return CONFIRM_RESULTS


# ─── УПРАВЛЕНИЕ УПРАЖНЕНИЯМИ ──────────────────────────────────────────────────

async def manage_exercises_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    exercises    = context.user_data["exercises"]
    user_id      = q.from_user.id
    workout_type = context.user_data["workout_type"]
    removed      = db.get_removed_exercises(user_id, workout_type)

    keyboard = []
    for i, ex in enumerate(exercises):
        keyboard.append([InlineKeyboardButton(f"🗑 {ex['name']}", callback_data=f"rmex_{i}")])

    base_names = [e["name"] for e in WORKOUTS[workout_type]["exercises"]]
    for name in removed:
        if name in base_names:
            keyboard.append([InlineKeyboardButton(f"♻️ Вернуть: {name}", callback_data=f"rstore_{name}")])

    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="back_confirm")])
    await q.message.reply_text(
        "🗑 Нажми чтобы *убрать* упражнение из тренировки навсегда.\n"
        "♻️ *Вернуть* — восстановить удалённое.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return MANAGE_EXERCISES


async def do_remove_exercise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx          = int(q.data.split("_")[1])
    user_id      = q.from_user.id
    workout_type = context.user_data["workout_type"]
    exercises    = context.user_data["exercises"]

    if idx < len(exercises):
        name = exercises[idx]["name"]
        exercises.pop(idx)
        context.user_data["results"] = [r for r in context.user_data["results"] if r["name"] != name]
        base_names = [e["name"] for e in WORKOUTS[workout_type]["exercises"]]
        if name in base_names:
            db.remove_exercise(user_id, workout_type, name)
        else:
            db.remove_custom_exercise(user_id, workout_type, name)
        await q.edit_message_text(f"🗑 *{name}* убрано.", parse_mode="Markdown")

    await show_confirm_chat(context, q.message.chat_id)
    return CONFIRM_RESULTS


async def restore_exercise_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    name         = q.data[len("rstore_"):]
    user_id      = q.from_user.id
    workout_type = context.user_data["workout_type"]
    db.restore_exercise(user_id, workout_type, name)
    base_ex = next((e for e in WORKOUTS[workout_type]["exercises"] if e["name"] == name), None)
    if base_ex:
        context.user_data["exercises"].append(base_ex.copy())
    await q.edit_message_text(f"♻️ *{name}* возвращено!", parse_mode="Markdown")
    await show_confirm_chat(context, q.message.chat_id)
    return CONFIRM_RESULTS


async def back_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_confirm_chat(context, q.message.chat_id)
    return CONFIRM_RESULTS


# ─── ИСТОРИЯ ──────────────────────────────────────────────────────────────────

async def show_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id
    sessions = db.get_history(user_id, limit=10)

    if not sessions:
        await q.edit_message_text(
            "📋 *История пуста*\n\nЗапиши первую тренировку!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="back_main")]]),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    keyboard = []
    for s in sessions:
        dt    = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y %H:%M")
        vol   = session_volume(s["exercises"])
        vol_s = f"  📦{vol:,.0f}кг" if vol else ""
        label = f"[{s['workout_type']}] {dt}{vol_s}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"view_session_{s['id']}")])

    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="back_main")])
    await q.edit_message_text(
        "📋 *История тренировок:*\nНажми на дату для подробностей.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return MAIN_MENU


async def view_session_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    session_id = int(q.data.split("_")[-1])
    user_id    = q.from_user.id
    s          = db.get_session_by_id(user_id, session_id)

    if not s:
        await q.message.reply_text("Тренировка не найдена.")
        return MAIN_MENU

    dt   = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y %H:%M")
    vol  = session_volume(s["exercises"])
    lines = [f"*Тренировка {s['workout_type']} — {dt}*\n"]

    for ex in s["exercises"]:
        if ex.get("skipped"):
            lines.append(f"⏭ {ex['name']}")
        elif ex.get("sets_data"):
            lines.append(f"✅ *{ex['name']}* ({ex.get('sets_plan','')})\n   {sets_summary(ex['sets_data'])}")
        else:
            lines.append(f"⬜ {ex['name']}")

    if vol:
        lines.append(f"\n📦 Объём: *{vol:,.0f} кг*")
    if s.get("notes"):
        lines.append(f"📝 _{s['notes']}_")

    keyboard = [
        [InlineKeyboardButton("🗑 Удалить тренировку", callback_data=f"del_session_{session_id}")],
        [InlineKeyboardButton("↩️ К списку", callback_data="menu_history")],
    ]
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return MAIN_MENU


async def delete_session_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    session_id = int(q.data.split("_")[-1])
    user_id    = q.from_user.id

    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_del_{session_id}"),
            InlineKeyboardButton("❌ Нет", callback_data="menu_history"),
        ]
    ]
    await q.edit_message_text(
        "⚠️ Удалить эту тренировку? Это действие нельзя отменить.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MAIN_MENU


async def confirm_delete_session_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    session_id = int(q.data.split("_")[-1])
    user_id    = q.from_user.id
    ok = db.delete_session(user_id, session_id)

    msg = "✅ Тренировка удалена." if ok else "⚠️ Не удалось удалить."
    await q.edit_message_text(
        msg,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ К истории", callback_data="menu_history")]]),
    )
    return MAIN_MENU


# ─── СТАТИСТИКА ───────────────────────────────────────────────────────────────

async def show_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id

    streak  = db.get_streak(user_id)
    weekly  = db.get_weekly_summary(user_id, weeks=4)
    volumes = db.get_volume_per_session(user_id, limit=5)

    # Еженедельные тренировки текстовым баром
    week_lines = []
    for w in weekly:
        bar = "█" * w["count"] + "░" * max(0, 4 - w["count"])
        week_lines.append(f"  {w['week']}: {bar} {w['count']}")

    # Объём последних тренировок
    vol_lines = []
    for v in volumes:
        dt = datetime.fromisoformat(v["date"]).strftime("%d.%m")
        bar = "▓" * min(10, int(v["volume"] / 2000)) if v["volume"] else "░"
        vol_lines.append(f"  [{v['type']}] {dt}: {v['volume']:,.0f} кг  {bar}")

    text = (
        f"📊 *Статистика*\n\n"
        f"🔥 Серия: *{streak['current']}* тренировок подряд\n"
        f"🏆 Лучшая серия: *{streak['best']}*\n"
        f"📅 Всего тренировок: *{streak['total']}*\n\n"
        f"*Активность по неделям:*\n" + "\n".join(week_lines) + "\n\n"
        f"*Объём последних тренировок:*\n" + ("\n".join(vol_lines) or "  Нет данных")
    )

    keyboard = [
        [InlineKeyboardButton("📈 Прогресс по упражнению", callback_data="stats_exercise")],
        [InlineKeyboardButton("↩️ Назад", callback_data="back_main")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return MAIN_MENU


async def stats_exercise_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Собрать все упражнения из истории
    user_id = q.from_user.id
    sessions = db.get_history(user_id, limit=30)
    all_names = []
    seen = set()
    for s in sessions:
        for ex in s["exercises"]:
            if ex["name"] not in seen and not ex.get("skipped"):
                seen.add(ex["name"])
                all_names.append(ex["name"])

    if not all_names:
        await q.message.reply_text("Нет данных по упражнениям.")
        return MAIN_MENU

    keyboard = []
    row = []
    for i, name in enumerate(all_names):
        row.append(InlineKeyboardButton(name, callback_data=f"exstat_{name}"))
        if len(row) == 2 or i == len(all_names) - 1:
            keyboard.append(row)
            row = []
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="menu_stats")])
    await q.edit_message_text(
        "📈 Выбери упражнение:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return STATS_EXERCISE


async def stats_exercise_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "menu_stats":
        return await show_stats_menu(update, context)

    ex_name = q.data[len("exstat_"):]
    user_id = q.from_user.id
    history = db.get_exercise_history(user_id, ex_name, limit=8)
    pr      = db.get_personal_record(user_id, ex_name)

    if not history:
        await q.edit_message_text(
            f"Нет данных по упражнению *{ex_name}*.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="menu_stats")]]),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    lines = [f"📈 *{ex_name}*\n"]
    if pr:
        pr_dt = datetime.fromisoformat(pr["date"]).strftime("%d.%m.%Y")
        lines.append(f"🏆 Рекорд: *{pr['weight']} кг* ({pr_dt})\n")

    for entry in reversed(history):
        dt  = datetime.fromisoformat(entry["date"]).strftime("%d.%m")
        max_w = max((w for w, r in entry["sets_data"] if isinstance(w, (int, float))), default=None)
        bar   = "▓" * min(10, int(max_w / 10)) if max_w else "░"
        lines.append(f"  {dt}: {sets_summary(entry['sets_data'])} {bar}")

    keyboard = [
        [InlineKeyboardButton("↩️ Назад", callback_data="menu_stats")],
    ]
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return MAIN_MENU


# ─── РЕКОРДЫ ──────────────────────────────────────────────────────────────────

async def show_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id
    sessions = db.get_history(user_id, limit=50)

    # Собираем все имена упражнений
    all_names = set()
    for s in sessions:
        for ex in s["exercises"]:
            if not ex.get("skipped") and ex.get("sets_data"):
                all_names.add(ex["name"])

    if not all_names:
        await q.edit_message_text(
            "🏆 *Рекорды*\n\nНет данных. Запиши хотя бы одну тренировку!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="back_main")]]),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    lines = ["🏆 *Личные рекорды по весу:*\n"]
    for name in sorted(all_names):
        pr = db.get_personal_record(user_id, name)
        if pr:
            dt = datetime.fromisoformat(pr["date"]).strftime("%d.%m.%Y")
            lines.append(f"  *{name}*: {pr['weight']} кг ({dt})")

    keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_main")]]
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return MAIN_MENU


# ─── СРАВНЕНИЕ ТРЕНИРОВОК ─────────────────────────────────────────────────────

async def compare_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = q.from_user.id
    sessions = db.get_history(user_id, limit=10)

    if len(sessions) < 2:
        await q.edit_message_text(
            "⚖️ Для сравнения нужно минимум *2 тренировки*.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="back_main")]]),
            parse_mode="Markdown",
        )
        return MAIN_MENU

    keyboard = []
    for s in sessions:
        dt    = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y %H:%M")
        label = f"[{s['workout_type']}] {dt}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"cmp1_{s['id']}")])
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="back_main")])

    await q.edit_message_text(
        "⚖️ *Сравнение тренировок*\n\nВыбери *первую* тренировку:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return COMPARE_PICK_FIRST


async def compare_pick_first(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "back_main":
        return await start(update, context)

    session_id = int(q.data.split("_")[1])
    context.user_data["cmp_s1"] = session_id
    user_id = q.from_user.id
    sessions = db.get_history(user_id, limit=10)

    keyboard = []
    for s in sessions:
        if s["id"] == session_id:
            continue
        dt    = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y %H:%M")
        label = f"[{s['workout_type']}] {dt}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"cmp2_{s['id']}")])
    keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="back_main")])

    await q.edit_message_text(
        "Теперь выбери *вторую* тренировку:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return COMPARE_PICK_SECOND


async def compare_pick_second(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "back_main":
        return await start(update, context)

    sid2    = int(q.data.split("_")[1])
    sid1    = context.user_data["cmp_s1"]
    user_id = q.from_user.id
    cmp     = db.compare_sessions(user_id, sid1, sid2)

    if not cmp:
        await q.message.reply_text("Не удалось загрузить тренировки.")
        return MAIN_MENU

    s1, s2 = cmp["s1"], cmp["s2"]
    dt1    = datetime.fromisoformat(s1["date"]).strftime("%d.%m.%Y")
    dt2    = datetime.fromisoformat(s2["date"]).strftime("%d.%m.%Y")
    v1     = session_volume(s1["exercises"])
    v2     = session_volume(s2["exercises"])
    diff   = v1 - v2
    sign   = "+" if diff >= 0 else ""

    # Общие упражнения
    map1 = {ex["name"]: ex for ex in s1["exercises"] if ex.get("sets_data")}
    map2 = {ex["name"]: ex for ex in s2["exercises"] if ex.get("sets_data")}
    common = set(map1) & set(map2)

    lines = [
        f"⚖️ *Сравнение:*\n",
        f"  🔵 {dt1} — Тренировка {s1['workout_type']}: *{v1:,.0f} кг*",
        f"  🟠 {dt2} — Тренировка {s2['workout_type']}: *{v2:,.0f} кг*",
        f"  Разница объёма: *{sign}{diff:,.0f} кг*\n",
    ]

    if common:
        lines.append("*Сравнение по упражнениям:*")
        for name in sorted(common):
            e1 = map1[name]
            e2 = map2[name]
            mw1 = max((w for w, r in e1["sets_data"] if isinstance(w, (int, float))), default=None)
            mw2 = max((w for w, r in e2["sets_data"] if isinstance(w, (int, float))), default=None)
            if mw1 is not None and mw2 is not None:
                d = mw1 - mw2
                sign2 = "+" if d >= 0 else ""
                icon  = "📈" if d > 0 else ("📉" if d < 0 else "➡️")
                lines.append(f"  {icon} *{name}*: {mw2}→{mw1} кг ({sign2}{d})")

    keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="back_main")]]
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return MAIN_MENU


# ─── /history /stats /records команды ────────────────────────────────────────

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    sessions = db.get_history(user_id, limit=5)
    if not sessions:
        await update.message.reply_text("📋 История пуста. Начни тренировку! /start")
        return
    text = "📋 *Последние тренировки:*\n\n"
    for s in sessions:
        dt  = datetime.fromisoformat(s["date"]).strftime("%d.%m.%Y %H:%M")
        vol = session_volume(s["exercises"])
        text += f"*Тренировка {s['workout_type']} — {dt}*"
        if vol:
            text += f"  📦{vol:,.0f}кг"
        text += "\n"
        for ex in s["exercises"]:
            if ex.get("skipped"):
                text += f"  ⏭ {ex['name']}\n"
            elif ex.get("sets_data"):
                text += f"  ✅ {ex['name']}: {sets_summary(ex['sets_data'])}\n"
        text += "\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Отменено. Данные не сохранены.\n/start — начать заново."
    )
    return ConversationHandler.END


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    token = os.getenv("BOT_TOKEN")
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler,        pattern="^(menu_|back_main)"),
                CallbackQueryHandler(view_session_cb,          pattern="^view_session_"),
                CallbackQueryHandler(delete_session_cb,        pattern="^del_session_"),
                CallbackQueryHandler(confirm_delete_session_cb,pattern="^confirm_del_"),
                CallbackQueryHandler(stats_exercise_prompt,    pattern="^stats_exercise$"),
                CallbackQueryHandler(stats_exercise_show,      pattern="^(exstat_|menu_stats)"),
            ],
            CHOOSE_WORKOUT: [
                CallbackQueryHandler(choose_workout, pattern="^(workout_|back_main)"),
            ],
            ENTERING_SET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_set),
                CallbackQueryHandler(repeat_set_cb, pattern="^repeat_set$"),
                CallbackQueryHandler(copy_last_cb,  pattern="^copy_last$"),
                CallbackQueryHandler(skip_ex_cb,    pattern="^skip_ex$"),
            ],
            CONFIRM_RESULTS: [
                CallbackQueryHandler(save_cb,               pattern="^save$"),
                CallbackQueryHandler(restart_cb,            pattern="^restart$"),
                CallbackQueryHandler(add_note_prompt,       pattern="^add_note$"),
                CallbackQueryHandler(add_exercise_prompt,   pattern="^add_exercise$"),
                CallbackQueryHandler(manage_exercises_menu, pattern="^manage_exercises$"),
                CallbackQueryHandler(back_confirm_cb,       pattern="^back_confirm$"),
            ],
            ADD_EX_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ex_name)],
            ADD_EX_SETS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ex_sets_count)],
            ADD_EX_REPS:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_ex_reps)],
            MANAGE_EXERCISES: [
                CallbackQueryHandler(do_remove_exercise,   pattern="^rmex_\\d+$"),
                CallbackQueryHandler(restore_exercise_cb,  pattern="^rstore_"),
                CallbackQueryHandler(back_confirm_cb,      pattern="^back_confirm$"),
            ],
            ADD_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_note),
            ],
            COMPARE_PICK_FIRST: [
                CallbackQueryHandler(compare_pick_first, pattern="^(cmp1_|back_main)"),
            ],
            COMPARE_PICK_SECOND: [
                CallbackQueryHandler(compare_pick_second, pattern="^(cmp2_|back_main)"),
            ],
            STATS_EXERCISE: [
                CallbackQueryHandler(stats_exercise_show, pattern="^(exstat_|menu_stats)"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    async def get_db(update, context):
        await update.message.reply_document(open("workouts.db", "rb"))

    app.add_handler(CommandHandler("getdb", get_db))
    app.add_handler(conv)
    app.add_handler(CommandHandler("history", history_command))

    print("🤖 Бот запущен! Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
