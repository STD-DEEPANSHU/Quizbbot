import asyncio
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from config import TELEGRAM_TOKEN, MONGO_URI, DB_NAME

# ----------------- MongoDB Setup -----------------
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
quizzes = db["quizzes"]
results = db["results"]

# ----------------- In-memory states -----------------
user_state = {}
user_current_scores = {}

# ----------------- START COMMAND -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🆕 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")]
    ]
    await update.message.reply_text(
        "This bot will help you create a quiz with multiple choice questions.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ----------------- BUTTON HANDLER -----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "create_quiz":
        user_state[user_id] = {"step": "title"}
        await query.message.reply_text("Send the *title* of your quiz.")

    elif query.data == "view_quizzes":
        user_quizzes = list(quizzes.find({"user_id": user_id}))
        if not user_quizzes:
            await query.message.reply_text("❌ You have no saved quizzes.")
            return
        buttons = [[InlineKeyboardButton(f"▶️ {q['title']}", callback_data=f"play_{q['_id']}")] for q in user_quizzes]
        await query.message.reply_text("📚 Your quizzes:", reply_markup=InlineKeyboardMarkup(buttons))

# ----------------- MESSAGE HANDLER (QUIZ CREATION) -----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text
    if user_id not in user_state:
        return
    state = user_state[user_id]

    if state["step"] == "title":
        state["title"] = text
        state["step"] = "description"
        await update.message.reply_text("Send the *description* of your quiz or type /skip.")

    elif state["step"] == "description":
        state["description"] = text
        state["questions"] = []
        state["step"] = "question"
        await update.message.reply_text("Send the first *question*.")

    elif state["step"] == "question":
        state["current_question"] = {"question": text, "options": []}
        state["step"] = "options"
        await update.message.reply_text("Send option 1:")

    elif state["step"] == "options":
        state["current_question"]["options"].append(text)
        if len(state["current_question"]["options"]) < 2:
            await update.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
        else:
            keyboard = [
                [InlineKeyboardButton("➕ Add More Option", callback_data="add_option")],
                [InlineKeyboardButton("✅ Done", callback_data="done_options")]
            ]
            await update.message.reply_text(
                "Option saved. Choose next action:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

# ----------------- OPTIONS BUTTON HANDLER -----------------
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
        await query.message.reply_text("Send the next question.")

    elif query.data == "finish_quiz":
        state["step"] = "set_timer"
        keyboard = [
            [InlineKeyboardButton("10s", callback_data="timer_10"),
             InlineKeyboardButton("15s", callback_data="timer_15"),
             InlineKeyboardButton("30s", callback_data="timer_30")]
        ]
        await query.message.reply_text(
            "Select time per question:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ----------------- TIMER HANDLER -----------------
async def timer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = user_state[user_id]

    if query.data.startswith("timer_"):
        timer_value = int(query.data.replace("timer_", ""))

        quiz_id = quizzes.insert_one({
            "user_id": user_id,
            "title": state["title"],
            "description": state.get("description", ""),
            "questions": state["questions"],
            "timer": timer_value
        }).inserted_id

        del user_state[user_id]

        await query.message.reply_text(f"✅ Quiz saved! Starting now.")
        await play_quiz_private(query, context, quiz_id)

# ----------------- PLAY QUIZ -----------------
async def play_quiz_private(query, context, quiz_id):
    quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        await query.message.reply_text("❌ Quiz not found!")
        return

    user_current_scores[query.from_user.id] = 0
    total_questions = len(quiz["questions"])
    timer = quiz.get("timer", 10)

    await query.message.reply_text(f"▶️ Starting quiz: {quiz['title']}")

    for idx, q in enumerate(quiz["questions"], start=1):
        await context.bot.send_poll(
            chat_id=query.message.chat_id,
            question=f"Q{idx}: {q['question']} (⏱️ {timer}s)",
            options=q["options"],
            type=Poll.QUIZ,
            correct_option_id=q["correct_index"],
            is_anonymous=False
        )
        await asyncio.sleep(timer)
        # Increment score for demo
        user_current_scores[query.from_user.id] += 1

    # Save result
    results.insert_one({
        "quiz_id": quiz["_id"],
        "user_id": query.from_user.id,
        "username": query.from_user.username or str(query.from_user.id),
        "score": user_current_scores[query.from_user.id],
        "total_questions": total_questions,
        "timestamp": datetime.utcnow()
    })

    # Show leaderboard
    await show_leaderboard(query, context, quiz["_id"])

# ----------------- SHOW LEADERBOARD -----------------
async def show_leaderboard(query, context, quiz_id):
    top_users = list(results.find({"quiz_id": quiz_id}).sort("score", -1).limit(10))
    message = "🏆 Leaderboard - Top 10 Users\n\n"
    for idx, u in enumerate(top_users, start=1):
        message += f"{idx}. @{u['username']} - {u['score']}/{u['total_questions']}\n"
    await query.message.reply_text(message)

# ----------------- MAIN -----------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", lambda u, c: message_handler(u, c)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(create_quiz|view_quizzes|play_(?!timer_).*)$"))
    app.add_handler(CallbackQueryHandler(options_button, pattern="^(add_option|done_options)$"))
    app.add_handler(CallbackQueryHandler(correct_button, pattern="^correct_.*$"))
    app.add_handler(CallbackQueryHandler(more_questions_handler, pattern="^(new_question|finish_quiz)$"))
    app.add_handler(CallbackQueryHandler(timer_handler, pattern="^timer_.*$"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
