import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from db import save_quiz, get_user_quizzes
from config import Config

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Dictionary for temporary state
user_state = {}


# ---------------- Commands ---------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
        [InlineKeyboardButton("🌍 Language (English)", callback_data="language")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🤖 This bot will help you create a quiz with a series of multiple-choice questions.",
        reply_markup=reply_markup,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "create_quiz":
        user_state[query.from_user.id] = {"step": "title", "questions": []}
        await query.message.reply_text(
            "Let's create a new quiz.\n\nSend me the title of your quiz (e.g., ‘Aptitude Test’)."
        )

    elif query.data == "view_quizzes":
        quizzes = get_user_quizzes(query.from_user.id)
        if not quizzes:
            await query.message.reply_text("❌ You don’t have any saved quizzes yet.")
        else:
            msg = "📚 Your quizzes:\n"
            for q in quizzes:
                msg += f"➡️ {q['title']} ({len(q['questions'])} questions)\n"
            await query.message.reply_text(msg)

    elif query.data == "language":
        await query.message.reply_text("🌍 Language switching not implemented yet.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in user_state:
        await update.message.reply_text("⚠️ Please start with /start")
        return

    state = user_state[user_id]

    if state["step"] == "title":
        state["title"] = update.message.text
        state["step"] = "description"
        await update.message.reply_text(
            "✅ Good. Now send me a description of your quiz.\nYou can also /skip this step."
        )

    elif state["step"] == "description":
        state["description"] = update.message.text
        state["step"] = "questions"
        await update.message.reply_text(
            "👌 Good. Now send me your first question.\n"
            "⚠️ Note: This bot can't create anonymous polls."
        )

    elif state["step"] == "questions":
        state["questions"].append(update.message.text)
        await update.message.reply_text(
            f"✅ Added question {len(state['questions'])}.\n"
            "Send the next one, or /done if finished. Use /undo to remove last question."
        )


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_state and user_state[user_id]["step"] == "description":
        user_state[user_id]["description"] = ""
        user_state[user_id]["step"] = "questions"
        await update.message.reply_text("👌 Skipped. Now send me your first question.")


async def undo_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_state and user_state[user_id]["step"] == "questions":
        if user_state[user_id]["questions"]:
            removed = user_state[user_id]["questions"].pop()
            await update.message.reply_text(f"❌ Removed last question: {removed}")
        else:
            await update.message.reply_text("⚠️ No questions to undo.")


async def done_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_state and user_state[user_id]["step"] == "questions":
        quiz = user_state[user_id]
        save_quiz(
            user_id,
            quiz["title"],
            quiz.get("description", ""),
            quiz["questions"],
            timer=30,
            shuffle="no_shuffle",
        )
        del user_state[user_id]

        await update.message.reply_text(
            f"🎉 Quiz '{quiz['title']}' saved with {len(quiz['questions'])} questions!"
        )


# ---------------- Main ---------------- #
def main():
    try:
        application = Application.builder().token(Config.TELEGRAM_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("skip", skip_description))
        application.add_handler(CommandHandler("undo", undo_question))
        application.add_handler(CommandHandler("done", done_quiz))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("🤖 Bot started...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        logger.error(f"Bot crashed: {e}")


if __name__ == "__main__":
    main()
