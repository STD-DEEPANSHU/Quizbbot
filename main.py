import asyncio
from pymongo import MongoClient
from bson import ObjectId
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Poll
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import random
from config import TELEGRAM_TOKEN, MONGO_URI, DB_NAME

# MongoDB Setup
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
quizzes = db["quizzes"]
analytics = db["analytics"]

# Temporary in-memory state
user_state = {}

# ----------------- START -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🆕 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
        [InlineKeyboardButton("🌍 Language (Default: English)", callback_data="lang_menu")]
    ]
    await update.message.reply_text(
        "This bot will help you create a quiz with a series of multiple choice questions.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ----------------- BUTTON HANDLER -----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "create_quiz":
        user_state[user_id] = {"step": "title"}
        await query.message.reply_text("Let's create a new quiz. Send me the *title* of your quiz.")

    elif query.data == "view_quizzes":
        user_quizzes = list(quizzes.find({"user_id": user_id}))
        if not user_quizzes:
            await query.message.reply_text("❌ You have no saved quizzes.")
            return
        buttons = [[InlineKeyboardButton(f"▶️ {q['title']}", callback_data=f"play_{q['_id']}")] for q in user_quizzes]
        await query.message.reply_text("📚 Your quizzes:", reply_markup=InlineKeyboardMarkup(buttons))

    # ----------------- PLAY EXISTING QUIZ -----------------
    elif query.data.startswith("play_") and not query.data.startswith("play_timer_") and not query.data.startswith("play_shuffle_"):
        quiz_id = query.data.replace("play_", "")
        keyboard = [
            [InlineKeyboardButton("🔀 Shuffle All", callback_data=f"play_shuffle_{quiz_id}_shuffle_all")],
            [InlineKeyboardButton("❌ No Shuffle", callback_data=f"play_shuffle_{quiz_id}_no_shuffle")],
            [InlineKeyboardButton("🔁 Only Answers", callback_data=f"play_shuffle_{quiz_id}_shuffle_answers")],
            [InlineKeyboardButton("🔂 Only Questions", callback_data=f"play_shuffle_{quiz_id}_shuffle_questions")]
        ]
        await query.message.reply_text(
            "Choose how you want to shuffle this quiz:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ----------------- QUIZ CREATION FLOW -----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if user_id not in user_state:
        return

    state = user_state[user_id]

    if state["step"] == "title":
        state["title"] = text
        state["step"] = "description"
        await update.message.reply_text("Good. Now send me a *description* of your quiz. Or type /skip.")

    elif state["step"] == "description":
        state["description"] = text
        state["questions"] = []
        state["step"] = "question"
        await update.message.reply_text("Good. Now send me your first *question*.")

    elif state["step"] == "question":
        state["current_question"] = {"question": text, "options": []}
        state["step"] = "options"
        await update.message.reply_text("Now send option 1 for this question:")

    elif state["step"] == "options":
        state["current_question"]["options"].append(text)
        if len(state["current_question"]["options"]) < 2:
            await update.message.reply_text(f"Now send option {len(state['current_question']['options'])+1}:")
        else:
            keyboard = [
                [InlineKeyboardButton("➕ Add More Option", callback_data="add_option")],
                [InlineKeyboardButton("✅ Done", callback_data="done_options")]
            ]
            await update.message.reply_text(
                f"Option {len(state['current_question']['options'])} saved. What next?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

# ----------------- OPTIONS HANDLER -----------------
async def options_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = user_state[user_id]

    if query.data == "add_option":
        await query.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
    elif query.data == "done_options":
        state["step"] = "correct"
        opts = state["current_question"]["options"]
        keyboard = [[InlineKeyboardButton(o, callback_data=f"correct_{i}")] for i, o in enumerate(opts)]
        await query.message.reply_text("Which one is the correct option?", reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------- CORRECT OPTION HANDLER -----------------
async def correct_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = user_state[user_id]

    if query.data.startswith("correct_"):
        correct_index = int(query.data.replace("correct_", ""))
        state["current_question"]["correct_index"] = correct_index
        state["questions"].append(state["current_question"])

        # Preview Poll
        q = state["current_question"]
        await context.bot.send_poll(
            chat_id=query.message.chat_id,
            question=q["question"],
            options=q["options"],
            type=Poll.QUIZ,
            correct_option_id=correct_index,
            is_anonymous=False
        )

        # Next step
        state["step"] = "more_questions"
        keyboard = [
            [InlineKeyboardButton("➕ Add Another Question", callback_data="new_question")],
            [InlineKeyboardButton("✅ Finish Quiz", callback_data="finish_quiz")]
        ]
        await query.message.reply_text("Question added! What next?", reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------- MORE QUESTIONS HANDLER -----------------
async def more_questions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = user_state[user_id]

    if query.data == "new_question":
        state["step"] = "question"
        await query.message.reply_text("Send me the next *question*.")

    elif query.data == "finish_quiz":
        # Ask shuffle option for new quiz creation
        keyboard = [
            [InlineKeyboardButton("🔀 Shuffle All", callback_data="shuffle_all")],
            [InlineKeyboardButton("❌ No Shuffle", callback_data="no_shuffle")],
            [InlineKeyboardButton("🔁 Only Answers", callback_data="shuffle_answers")],
            [InlineKeyboardButton("🔂 Only Questions", callback_data="shuffle_questions")]
        ]
        await query.message.reply_text(
            "Choose how you want to shuffle your quiz:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ----------------- SHUFFLE SELECTION HANDLER -----------------
async def shuffle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = user_state.get(user_id, {})

    # Determine if it's new quiz or play existing
    if "step" in state:  # new quiz creation
        shuffle_option = query.data
        quizzes.insert_one({
            "user_id": user_id,
            "title": state["title"],
            "description": state.get("description", ""),
            "questions": state["questions"],
            "shuffle": shuffle_option
        })
        del user_state[user_id]
        await query.message.reply_text(f"✅ Your quiz has been saved with option: {shuffle_option.replace('_',' ').title()}")
    else:  # existing quiz play
        quiz_id = query.data.split("_")[2]
        shuffle_option = query.data.split("_")[3]
        # Save in memory for next timer selection
        user_state[user_id] = {"play_quiz": quiz_id, "shuffle": shuffle_option}

        # Ask timer next
        keyboard = [
            [InlineKeyboardButton("10s", callback_data=f"play_timer_{quiz_id}_10"),
             InlineKeyboardButton("15s", callback_data=f"play_timer_{quiz_id}_15"),
             InlineKeyboardButton("30s", callback_data=f"play_timer_{quiz_id}_30")],
            [InlineKeyboardButton("45s", callback_data=f"play_timer_{quiz_id}_45"),
             InlineKeyboardButton("1min", callback_data=f"play_timer_{quiz_id}_60")]
        ]
        await query.message.reply_text(
            "⏱ Select time per question for this quiz:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ----------------- PLAY QUIZ TIMER HANDLER -----------------
async def play_timer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    parts = query.data.split("_")
    quiz_id = parts[2]
    timer = int(parts[3])
    state = user_state.get(user_id, {})
    shuffle_option = state.get("shuffle", "no_shuffle")

    quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        await query.message.reply_text("❌ Quiz not found!")
        return

    await query.message.reply_text(f"▶️ Starting quiz: {quiz['title']}")

    questions = quiz["questions"][:]

    if shuffle_option in ["shuffle_all", "shuffle_questions"]:
        random.shuffle(questions)

    for idx, q in enumerate(questions, start=1):
        options = q["options"][:]
        correct_index = q["correct_index"]

        if shuffle_option in ["shuffle_all", "shuffle_answers"]:
            combined = list(zip(options, range(len(options))))
            random.shuffle(combined)
            options, new_indices = zip(*combined)
            correct_index = new_indices.index(correct_index)

        poll_message = await context.bot.send_poll(
            chat_id=query.message.chat_id,
            question=f"Q{idx}: {q['question']} (⏱️ {timer}s)",
            options=list(options),
            type=Poll.QUIZ,
            correct_option_id=correct_index,
            is_anonymous=False
        )

        await asyncio.sleep(timer)  # smooth countdown

        # Save analytics
        analytics.insert_one({
            "quiz_id": str(quiz['_id']),
            "user_id": query.from_user.id,
            "question_index": idx,
            "correct_option": correct_index
        })

    # Clear temporary state
    if user_id in user_state:
        del user_state[user_id]

# ----------------- MAIN -----------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", lambda u, c: message_handler(u, c)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(create_quiz|view_quizzes|play_(?!timer_|shuffle_).*)$"))
    app.add_handler(CallbackQueryHandler(options_button, pattern="^(add_option|done_options)$"))
    app.add_handler(CallbackQueryHandler(correct_button, pattern="^correct_.*$"))
    app.add_handler(CallbackQueryHandler(more_questions_handler, pattern="^(new_question|finish_quiz)$"))
    app.add_handler(CallbackQueryHandler(shuffle_handler, pattern="^(shuffle_all|no_shuffle|shuffle_answers|shuffle_questions|play_shuffle_.*)$"))
    app.add_handler(CallbackQueryHandler(play_timer_handler, pattern="^play_timer_.*$"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
