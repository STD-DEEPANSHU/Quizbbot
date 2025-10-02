import asyncio
import copy
import random
import logging
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
from config import TELEGRAM_TOKEN, MONGO_URI, DB_NAME

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- MONGO SETUP --------------------
try:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    quizzes = db["quizzes"]
    users_answers = db["users_answers"]
    logger.info("MongoDB Connected Successfully!")
except Exception as e:
    logger.error(f"Error connecting to MongoDB: {e}")
    exit()

# -------------------- START --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear() # Start se state clear karna aacha hai
    keyboard = [
        [InlineKeyboardButton("🆕 Create New Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("📚 View My Quizzes", callback_data="view_quizzes")],
    ]
    await update.message.reply_text(
        "This bot will help you create and play quizzes with multiple choice questions.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# -------------------- BUTTON HANDLER --------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = context.user_data

    if query.data == "create_quiz":
        user_data.clear()
        user_data["step"] = "title"
        await query.message.reply_text("Send me the *title* of your quiz.")

    elif query.data == "view_quizzes":
        try:
            user_quizzes = list(quizzes.find({"user_id": user_id}))
            if not user_quizzes:
                await query.message.reply_text("❌ You have no saved quizzes.")
                return
            buttons = [
                [InlineKeyboardButton(f"▶️ {q['title']}", callback_data=f"play_{q['_id']}")]
                for q in user_quizzes
            ]
            await query.message.reply_text("📚 Your quizzes:", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            logger.error(f"Error fetching quizzes from DB: {e}")
            await query.message.reply_text("❌ Could not fetch your quizzes. Please try again later.")

    elif query.data.startswith("play_") and not query.data.startswith("play_timer_") and not query.data.startswith("play_shuffle_"):
        quiz_id = query.data.replace("play_", "")
        keyboard = [
            [InlineKeyboardButton("🔀 Shuffle All", callback_data=f"play_shuffle_{quiz_id}_shuffle_all")],
            [InlineKeyboardButton("❌ No Shuffle", callback_data=f"play_shuffle_{quiz_id}_no_shuffle")],
            [InlineKeyboardButton("🔁 Only Answers", callback_data=f"play_shuffle_{quiz_id}_shuffle_answers")],
            [InlineKeyboardButton("🔂 Only Questions", callback_data=f"play_shuffle_{quiz_id}_shuffle_questions")],
        ]
        await query.message.reply_text(
            "Choose how you want to shuffle this quiz:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# -------------------- MESSAGE HANDLER --------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    text = update.message.text

    if "step" not in user_data:
        return

    state = user_data

    if text == "/skip" and state.get("step") == "description":
        state["description"] = ""
        state["questions"] = []
        state["step"] = "question"
        await update.message.reply_text("Send your first *question*.")
        return

    if state["step"] == "title":
        state["title"] = text
        state["step"] = "description"
        await update.message.reply_text("Send me a *description* of your quiz. Or type /skip.")
    elif state["step"] == "description":
        state["description"] = text
        state["questions"] = []
        state["step"] = "question"
        await update.message.reply_text("Send your first *question*.")
    elif state["step"] == "question":
        state["current_question"] = {"question": text, "options": []}
        state["step"] = "options"
        await update.message.reply_text("Send option 1 for this question:")
    elif state["step"] == "options":
        state["current_question"]["options"].append(text)
        if len(state["current_question"]["options"]) < 2:
            await update.message.reply_text(f"Send option {len(state['current_question']['options'])+1}:")
        else:
            keyboard = [
                [InlineKeyboardButton("➕ Add More Option", callback_data="add_option")],
                [InlineKeyboardButton("✅ Done", callback_data="done_options")],
            ]
            await update.message.reply_text(
                f"Option {len(state['current_question']['options'])} saved. What next?",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

# -------------------- OPTIONS HANDLER --------------------
async def options_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# -------------------- CORRECT OPTION --------------------
async def correct_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = context.user_data

    if query.data.startswith("correct_"):
        correct_index = int(query.data.replace("correct_", ""))
        state["current_question"]["correct_index"] = correct_index
        state["questions"].append(state["current_question"])
        state["step"] = "more_questions"
        keyboard = [
            [InlineKeyboardButton("➕ Add Another Question", callback_data="new_question")],
            [InlineKeyboardButton("✅ Finish Quiz", callback_data="finish_quiz")],
        ]
        await query.message.reply_text("Question added! What next?", reply_markup=InlineKeyboardMarkup(keyboard))

# -------------------- MORE QUESTIONS HANDLER --------------------
async def more_questions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = context.user_data

    if query.data == "new_question":
        state["step"] = "question"
        await query.message.reply_text("Send me the next *question*.")
    elif query.data == "finish_quiz":
        try:
            quizzes.insert_one({
                "user_id": query.from_user.id,
                "title": state.get("title", "Untitled Quiz"),
                "description": state.get("description", ""),
                "questions": state.get("questions", []),
            })
            await query.message.reply_text(f"✅ Your quiz has been saved successfully!")
            state.clear()
        except Exception as e:
            logger.error(f"Error saving new quiz to DB: {e}")
            await query.message.reply_text("❌ Could not save your quiz. Please try again later.")

# -------------------- SHUFFLE HANDLER --------------------
async def shuffle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data = context.user_data

    parts = query.data.split("_")
    quiz_id = parts[2]
    shuffle_option = "_".join(parts[3:])
    user_data["play_shuffle_option"] = shuffle_option
    keyboard = [
        [InlineKeyboardButton("10s", callback_data=f"play_timer_{quiz_id}_10"),
         InlineKeyboardButton("15s", callback_data=f"play_timer_{quiz_id}_15"),
         InlineKeyboardButton("30s", callback_data=f"play_timer_{quiz_id}_30")],
        [InlineKeyboardButton("45s", callback_data=f"play_timer_{quiz_id}_45"),
         InlineKeyboardButton("1min", callback_data=f"play_timer_{quiz_id}_60")],
    ]
    await query.message.reply_text(
        "⏱ Select time per question for this quiz:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# -------------------- PLAY QUIZ (FINAL FIXED VERSION) --------------------
async def play_timer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = context.user_data 
    
    parts = query.data.split("_")
    quiz_id = parts[2]
    timer = int(parts[3])

    try:
        quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
        if not quiz:
            await query.message.reply_text("❌ Quiz not found!")
            return
    except Exception as e:
        logger.error(f"Error finding quiz {quiz_id}: {e}")
        await query.message.reply_text("❌ Could not start quiz. Please try again later.")
        return

    shuffle_option = user_data.get("play_shuffle_option", "no_shuffle")
    await query.message.reply_text(f"▶️ Starting quiz: *{quiz['title']}*", parse_mode='Markdown')
    
    questions = copy.deepcopy(quiz["questions"])
    if shuffle_option in ["shuffle_all", "shuffle_questions"]:
        random.shuffle(questions)

    user_data["quiz_answers"] = {}
    user_data["session_correct_answers"] = {}
    
    if 'poll_to_user' not in context.bot_data:
        context.bot_data['poll_to_user'] = {}

    for idx, q in enumerate(questions, start=1):
        options = q["options"][:]
        correct_index = q["correct_index"]
        
        if shuffle_option in ["shuffle_all", "shuffle_answers"]:
            paired = list(enumerate(options))
            random.shuffle(paired)
            new_indices, options = zip(*paired)
            options = list(options)
            shuffled_correct_index = list(new_indices).index(correct_index)
        else:
            shuffled_correct_index = correct_index

        user_data["session_correct_answers"][idx] = shuffled_correct_index
        
        poll_message = await context.bot.send_poll(
            chat_id=query.message.chat_id,
            question=f"Q{idx}: {q['question']}",
            options=options,
            type=Poll.QUIZ,
            correct_option_id=int(shuffled_correct_index),
            open_period=timer,
            is_anonymous=False,
        )
        
        context.bot_data['poll_to_user'][poll_message.poll.id] = {
            "user_id": user_id,
            "question_idx": idx,
        }
        
        await asyncio.sleep(timer)

    await asyncio.sleep(2) 

    final_user_data = context.application.user_data[user_id]
    quiz_answers = final_user_data.get("quiz_answers", {})
    session_correct_answers = final_user_data.get("session_correct_answers", {})
    total_questions = len(questions)
    
    correct_count = sum(1 for q_idx, sel_ans in quiz_answers.items() if sel_ans == session_correct_answers.get(q_idx))

    leaderboard_text = f"🏆 Quiz Finished!\n\nYour Score: *{correct_count} / {total_questions}*"
    await context.bot.send_message(chat_id=user_id, text=leaderboard_text, parse_mode='Markdown')
    
    try:
        answers_to_save = [{"question_index": k, "selected_option": v} for k, v in quiz_answers.items()]
        if answers_to_save:
            users_answers.insert_one({
                "user_id": user_id,
                "quiz_id": str(quiz["_id"]),
                "answers": answers_to_save,
            })
    except Exception as e:
        logger.error(f"Error saving user answers to DB: {e}")
    
    final_user_data.clear()

# -------------------- POLL ANSWER HANDLER --------------------
async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_id = update.poll_answer.poll_id
    selected_option = update.poll_answer.option_ids[0] if update.poll_answer.option_ids else None

    if selected_option is None:
        return

    poll_map = context.bot_data.get('poll_to_user', {})
    if poll_id in poll_map:
        poll_info = poll_map[poll_id]
        user_id = poll_info["user_id"]
        question_idx = poll_info["question_idx"]

        user_data_for_quiz_player = context.application.user_data[user_id]
        
        if "quiz_answers" not in user_data_for_quiz_player:
            user_data_for_quiz_player["quiz_answers"] = {}
            
        user_data_for_quiz_player["quiz_answers"][question_idx] = selected_option
        
        del context.bot_data['poll_to_user'][poll_id]

# -------------------- MAIN --------------------
def main():
    persistence = PicklePersistence(filepath="bot_state_data")
    app = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(create_quiz|view_quizzes|play_(?!timer_|shuffle_).*)$"))
    app.add_handler(CallbackQueryHandler(options_button, pattern="^(add_option|done_options)$"))
    app.add_handler(CallbackQueryHandler(correct_button, pattern="^correct_.*$"))
    app.add_handler(CallbackQueryHandler(more_questions_handler, pattern="^(new_question|finish_quiz)$"))
    app.add_handler(CallbackQueryHandler(shuffle_handler, pattern="^play_shuffle_.*$"))
    app.add_handler(CallbackQueryHandler(play_timer_handler, pattern="^play_timer_.*$"))
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
