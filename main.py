import asyncio
import copy
import random
import logging
import time
from collections import defaultdict
from pymongo import MongoClient
from bson import ObjectId
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)
# Make sure you have a config.py file with your tokens
from config import TELEGRAM_TOKEN, MONGO_URI, DB_NAME

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# LOCKS FOR ASYNC SAFETY
user_locks = defaultdict(asyncio.Lock)

# MONGO SETUP
try:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    quizzes = db["quizzes"]
    users_answers = db["users_answers"]
    logger.info("MongoDB Connected Successfully!")
except Exception as e:
    logger.error(f"Error connecting to MongoDB: {e}")
    exit()

# --- HANDLER FUNCTIONS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and main menu."""
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("🆕 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
    ]
    await update.message.reply_text(
        "This bot will help you create and play quizzes with multiple choice questions.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles main menu button presses."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = context.user_data

    if query.data == "create_quiz":
        user_data.clear()
        user_data["step"] = "title"
        await query.message.reply_text("Send me the *title* of your quiz.", parse_mode='Markdown')
    elif query.data == "view_quizzes":
        user_quizzes = list(quizzes.find({"user_id": user_id}))
        if not user_quizzes:
            await query.message.reply_text("❌ You have no saved quizzes.")
            return
        buttons = [[InlineKeyboardButton(f"▶️ {q['title']}", callback_data=f"play_{q['_id']}")] for q in user_quizzes]
        await query.message.reply_text("📚 Your quizzes:", reply_markup=InlineKeyboardMarkup(buttons))
    elif query.data.startswith("play_") and not query.data.startswith("play_timer_") and not query.data.startswith("play_shuffle_"):
        quiz_id = query.data.replace("play_", "")
        keyboard = [
            [InlineKeyboardButton("🔀 Shuffle All", callback_data=f"play_shuffle_{quiz_id}_shuffle_all")],
            [InlineKeyboardButton("❌ No Shuffle", callback_data=f"play_shuffle_{quiz_id}_no_shuffle")],
            [InlineKeyboardButton("🔁 Only Answers", callback_data=f"play_shuffle_{quiz_id}_shuffle_answers")],
            [InlineKeyboardButton("🔂 Only Questions", callback_data=f"play_shuffle_{quiz_id}_shuffle_questions")],
        ]
        await query.message.reply_text("Choose how you want to shuffle this quiz:", reply_markup=InlineKeyboardMarkup(keyboard))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles messages during the quiz creation process."""
    user_data = context.user_data
    text = update.message.text
    if "step" not in user_data: return
    state = user_data
    if text == "/skip" and state.get("step") == "description":
        state["description"], state["questions"], state["step"] = "", [], "question"
        await update.message.reply_text("Send your first *question*.", parse_mode='Markdown')
        return
    if state["step"] == "title":
        state["title"], state["step"] = text, "description"
        await update.message.reply_text("Send me a *description*. Or type /skip.", parse_mode='Markdown')
    elif state["step"] == "description":
        state["description"], state["questions"], state["step"] = text, [], "question"
        await update.message.reply_text("Send your first *question*.", parse_mode='Markdown')
    elif state["step"] == "question":
        state["current_question"], state["step"] = {"question": text, "options": []}, "options"
        await update.message.reply_text("Send option 1 for this question:")
    elif state["step"] == "options":
        state["current_question"]["options"].append(text)
        if len(state["current_question"]["options"]) < 2:
            await update.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
        else:
            keyboard = [[InlineKeyboardButton("➕ Add More Option", callback_data="add_option")], [InlineKeyboardButton("✅ Done", callback_data="done_options")]]
            await update.message.reply_text(f"Option {len(state['current_question']['options'])} saved. What next?", reply_markup=InlineKeyboardMarkup(keyboard))

async def options_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Add More Option' and 'Done' buttons."""
    query = update.callback_query
    await query.answer()
    state = context.user_data
    if query.data == "add_option":
        await query.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
    elif query.data == "done_options":
        state["step"] = "correct"
        opts = state["current_question"]["options"]
        keyboard = [[InlineKeyboardButton(o, callback_data=f"correct_{i}")] for i, o in enumerate(opts)]
        await query.message.reply_text("Which one is the correct option?", reply_markup=InlineKeyboardMarkup(keyboard))

async def correct_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of the correct answer."""
    query = update.callback_query
    await query.answer()
    state = context.user_data
    if query.data.startswith("correct_"):
        state["current_question"]["correct_index"] = int(query.data.replace("correct_", ""))
        if "questions" not in state: state["questions"] = []
        state["questions"].append(state["current_question"])
        state.pop("current_question", None)
        state["step"] = "more_questions"
        keyboard = [[InlineKeyboardButton("➕ Add Another Question", callback_data="new_question")], [InlineKeyboardButton("✅ Finish & Save Quiz", callback_data="finish_quiz")]]
        await query.message.reply_text("Question added! What next?", reply_markup=InlineKeyboardMarkup(keyboard))

async def more_questions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles adding more questions or finishing the quiz creation."""
    query = update.callback_query
    await query.answer()
    state = context.user_data
    if query.data == "new_question":
        state["step"] = "question"
        await query.message.reply_text("Send me the next *question*.", parse_mode='Markdown')
    elif query.data == "finish_quiz":
        quiz_data = {"user_id": query.from_user.id, "title": state.get("title", "Untitled Quiz"), "description": state.get("description", ""), "questions": state.get("questions", [])}
        if not quiz_data["questions"]:
            await query.message.reply_text("❌ Cannot save a quiz with no questions!")
            return
        quizzes.insert_one(quiz_data)
        await query.message.reply_text(f"✅ Your quiz '{quiz_data['title']}' has been saved successfully!")
        state.clear()

async def shuffle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the shuffle option selection."""
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    parts = query.data.split("_")
    quiz_id, shuffle_option = parts[2], "_".join(parts[3:])
    user_data["play_shuffle_option"] = shuffle_option
    keyboard = [
        [InlineKeyboardButton("10s", callback_data=f"play_timer_{quiz_id}_10"), InlineKeyboardButton("15s", callback_data=f"play_timer_{quiz_id}_15"), InlineKeyboardButton("30s", callback_data=f"play_timer_{quiz_id}_30")],
        [InlineKeyboardButton("45s", callback_data=f"play_timer_{quiz_id}_45"), InlineKeyboardButton("1min", callback_data=f"play_timer_{quiz_id}_60")]
    ]
    await query.message.reply_text("⏱ Select time per question:", reply_markup=InlineKeyboardMarkup(keyboard))

async def send_quiz_results(context: ContextTypes.DEFAULT_TYPE):
    """Calculates and sends the final quiz results after the quiz time is over."""
    job = context.job
    user_id = job.user_id
    quiz_title = job.data["quiz_title"]
    total_questions = job.data["total_questions"]
    start_time = job.data["start_time"]
    session_poll_ids = job.data["session_poll_ids"]
    user_lock = user_locks[user_id]

    logger.info(f"Job triggered to send results for user {user_id}")

    async with user_lock:
        final_user_data = context.application.user_data.get(user_id, {})
        correct_count = final_user_data.get("correct_count", 0)
        wrong_count = final_user_data.get("wrong_count", 0)
        missed_count = total_questions - (correct_count + wrong_count)
        duration = int(time.time() - start_time)

        leaderboard_text = (
            f"🏁 The quiz '{quiz_title}' has finished!\n\n"
            f"You answered {correct_count + wrong_count} out of {total_questions} questions:\n\n"
            f"✅ Correct – {correct_count}\n❌ Wrong – {wrong_count}\n⌛️ Missed – {missed_count}\n"
            f"⏱️ {duration} sec\n\n🥇1st place out of 1."
        )
        await context.bot.send_message(chat_id=user_id, text=leaderboard_text)

        # Clear user data for the next quiz
        final_user_data.clear()

    # Clean up poll IDs from bot_data to prevent memory leak
    if 'poll_to_user' in context.bot_data:
        for poll_id in session_poll_ids:
            context.bot_data['poll_to_user'].pop(poll_id, None)

    # Clean up the user lock
    if user_id in user_locks:
        del user_locks[user_id]

async def play_timer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the quiz setup and schedules the result message."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    start_time = time.time()
    user_data = context.user_data
    parts = query.data.split("_")
    quiz_id, timer = parts[2], int(parts[3])

    quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        await query.message.reply_text("❌ Quiz not found!")
        return

    shuffle_option = user_data.get("play_shuffle_option", "no_shuffle")
    await query.message.reply_text(f"▶️ Starting quiz: *{quiz['title']}*", parse_mode='Markdown')

    questions = copy.deepcopy(quiz["questions"])
    if shuffle_option in ["shuffle_all", "shuffle_questions"]:
        random.shuffle(questions)

    user_lock = user_locks[user_id]
    session_poll_ids = []

    async with user_lock:
        user_data['correct_count'], user_data['wrong_count'] = 0, 0
        user_data["session_correct_answers"] = {}

    if 'poll_to_user' not in context.bot_data:
        context.bot_data['poll_to_user'] = {}

    for idx, q in enumerate(questions, start=1):
        options, correct_index = q["options"][:], q["correct_index"]
        if shuffle_option in ["shuffle_all", "shuffle_answers"]:
            paired = list(enumerate(options))
            random.shuffle(paired)
            new_indices, new_options = zip(*paired)
            options, correct_index = list(new_options), list(new_indices).index(correct_index)

        async with user_lock:
            user_data["session_correct_answers"][idx] = correct_index

        poll_message = await context.bot.send_poll(
            chat_id=query.message.chat_id, question=f"Q{idx}: {q['question']}", options=options,
            type=Poll.QUIZ, correct_option_id=correct_index, open_period=timer, is_anonymous=False
        )
        session_poll_ids.append(poll_message.poll.id)
        context.bot_data['poll_to_user'][poll_message.poll.id] = {"user_id": user_id, "question_idx": idx}
        await asyncio.sleep(timer)

    # Schedule the results to be sent after the last poll's timer is up + a buffer
    delay = timer + 5  # 5 second buffer for Telegram's network latency

    job_data = {
        "quiz_title": quiz['title'],
        "total_questions": len(questions),
        "start_time": start_time,
        "session_poll_ids": session_poll_ids,
    }

    context.job_queue.run_once(
        send_quiz_results,
        when=delay,
        user_id=user_id,
        data=job_data,
        name=f"quiz_results_{user_id}_{quiz_id}"
    )

    await query.message.reply_text("<i>Quiz is running... Results will be shown at the end.</i>", parse_mode='HTML')


async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles a user's vote in a poll and updates the score in real-time."""
    poll_id = update.poll_answer.poll_id
    poll_map = context.bot_data.get('poll_to_user', {})

    if poll_id in poll_map:
        poll_info = poll_map.get(poll_id)
        if not poll_info: return

        user_id = poll_info["user_id"]
        question_idx = poll_info["question_idx"]
        user_lock = user_locks[user_id]

        async with user_lock:
            user_data = context.application.user_data.get(user_id)
            if not user_data: return

            # Check if user has answered already. A poll update is sent on vote and retraction.
            # We only care about the first vote.
            if update.poll_answer.option_ids:
                selected_option = update.poll_answer.option_ids[0]

                correct_answers = user_data.get("session_correct_answers", {})
                if selected_option == correct_answers.get(question_idx):
                    user_data['correct_count'] = user_data.get('correct_count', 0) + 1
                else:
                    user_data['wrong_count'] = user_data.get('wrong_count', 0) + 1
                
                # Once answered correctly, we can remove it from the map so it's not processed again
                poll_map.pop(poll_id, None)


# --- MAIN FUNCTION ---
def main():
    """Starts the bot."""
    persistence = PicklePersistence(filepath="bot_state_data")
    app = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(create_quiz|view_quizzes|play_(?!timer_|shuffle_).*)$"))
    app.add_handler(CallbackQueryHandler(options_button, pattern="^(add_option|done_options)$"))
    app.add_handler(CallbackQueryHandler(correct_button, pattern="^correct_.*$"))
    app.add_handler(CallbackQueryHandler(more_questions_handler, pattern="^(new_question|finish_quiz)$"))
    app.add_handler(CallbackQueryHandler(shuffle_handler, pattern="^play_shuffle_.*$"))
    app.add_handler(CallbackQueryHandler(play_timer_handler, pattern="^play_timer_.*$"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    # Run the bot
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.POLL_ANSWER])

if __name__ == "__main__":
    main()
