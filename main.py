import logging
import os
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
from dotenv import load_dotenv

load_dotenv()

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
application = Application.builder().token(TOKEN).build()

# Temporary user state in memory
user_state = {}

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
        [InlineKeyboardButton("🌐 Language (English)", callback_data="change_lang")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "This bot will help you create a quiz with a series of multiple choice questions.",
        reply_markup=reply_markup,
    )

# Handle Inline Buttons
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "create_quiz":
        user_state[query.from_user.id] = {"step": "title", "questions": []}
        await query.message.reply_text(
            "Let's create a new quiz.\nFirst, send me the title of your quiz (e.g., ‘Aptitude Test’ or ‘10 questions about bears’)."
        )

    elif query.data == "view_quizzes":
        quizzes = get_user_quizzes(query.from_user.id)
        if not quizzes:
            await query.message.reply_text("📚 You don't have any saved quizzes yet.")
        else:
            response = "📚 Your Quizzes:\n\n"
            for q in quizzes:
                response += f"• {q['title']} ({len(q['questions'])} questions)\n"
            await query.message.reply_text(response)

    elif query.data == "change_lang":
        await query.message.reply_text("🌐 Language feature coming soon!")

# Handle Text Inputs
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid not in user_state:
        return

    state = user_state[uid]

    # Title
    if state["step"] == "title":
        state["title"] = update.message.text
        state["step"] = "description"
        await update.message.reply_text(
            "Good. Now send me a description of your quiz.\nThis is optional, you can /skip this step."
        )

    # Description
    elif state["step"] == "description":
        state["description"] = update.message.text
        state["step"] = "add_question"
        await update.message.reply_text(
            "Good. Now send me a poll with your first question.\nAlternatively, you can /create to add a question."
        )

    elif state["step"] == "add_question":
        await update.message.reply_text("❌ Please use /create to add a question, or /done to finish.")

# Skip Description
async def skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid in user_state and user_state[uid]["step"] == "description":
        user_state[uid]["description"] = ""
        user_state[uid]["step"] = "add_question"
        await update.message.reply_text(
            "Skipped description. Now send me a poll with your first question.\nOr use /create to add one."
        )

# Create a Poll Question
async def create_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid not in user_state or user_state[uid]["step"] != "add_question":
        return

    await update.message.reply_poll(
        question="Sample Question: Who is the Prime Minister of India?",
        options=["Narendra Modi", "Rahul Gandhi", "Amit Shah", "Yogi Adityanath"],
        type=Poll.QUIZ,
        correct_option_id=0,
        is_anonymous=False,
    )

    user_state[uid]["questions"].append("Q1 Poll")
    await update.message.reply_text(
        f"Good. Your quiz '{user_state[uid]['title']}' now has {len(user_state[uid]['questions'])} question(s).\n"
        "Use /create to add another question, /undo to remove last, or /done to finish."
    )

# Undo
async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid in user_state and user_state[uid]["step"] == "add_question" and user_state[uid]["questions"]:
        user_state[uid]["questions"].pop()
        await update.message.reply_text("Last question removed.")
    else:
        await update.message.reply_text("❌ No question to undo.")

# Done (Save Quiz in MongoDB)
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid not in user_state:
        return

    state = user_state[uid]
    if state["step"] != "add_question":
        return

    state["step"] = "timer"
    await update.message.reply_text(
        "Please set a time limit for questions.\n\n"
        "We recommend 10-30 seconds for trivia quizzes."
    )

    keyboard = [
        [InlineKeyboardButton("10 sec", callback_data="timer_10")],
        [InlineKeyboardButton("30 sec", callback_data="timer_30")],
        [InlineKeyboardButton("1 min", callback_data="timer_60")],
    ]
    await update.message.reply_text("Choose a timer:", reply_markup=InlineKeyboardMarkup(keyboard))

# Timer Selection
async def timer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    if uid not in user_state:
        return

    user_state[uid]["timer"] = query.data.split("_")[1]
    user_state[uid]["step"] = "shuffle"

    keyboard = [
        [InlineKeyboardButton("Shuffle All", callback_data="shuffle_all")],
        [InlineKeyboardButton("No Shuffle", callback_data="shuffle_none")],
        [InlineKeyboardButton("Only Questions", callback_data="shuffle_q")],
        [InlineKeyboardButton("Only Answers", callback_data="shuffle_a")],
    ]

    await query.message.reply_text("Shuffle questions and answer options?", reply_markup=InlineKeyboardMarkup(keyboard))

# Shuffle Handler
async def shuffle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    if uid not in user_state:
        return

    user_state[uid]["shuffle"] = query.data
    state = user_state[uid]

    # Save quiz permanently in MongoDB
    save_quiz(uid, state["title"], state.get("description", ""), state["questions"], state["timer"], state["shuffle"])

    await query.message.reply_text("🎉 Quiz created successfully!\nPress Start Quiz to begin.")
    user_state.pop(uid, None)

# Register Handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler, pattern="create_quiz|view_quizzes|change_lang"))
application.add_handler(CallbackQueryHandler(timer_handler, pattern="timer_.*"))
application.add_handler(CallbackQueryHandler(shuffle_handler, pattern="shuffle_.*"))
application.add_handler(CommandHandler("skip", skip))
application.add_handler(CommandHandler("create", create_question))
application.add_handler(CommandHandler("undo", undo))
application.add_handler(CommandHandler("done", done))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

if __name__ == "__main__":
    application.run_polling()
