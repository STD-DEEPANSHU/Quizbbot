import asyncio
import copy
import random
import logging
import os
import time
from collections import defaultdict

from pymongo import MongoClient
from bson import ObjectId

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, Poll, PollAnswer
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties

# --- SETUP ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Aiogram Setup ---
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher(storage=storage)
router = Router()

# --- LOCKS FOR ASYNC SAFETY ---
user_locks = defaultdict(asyncio.Lock)
# Maps poll_id to user_id for poll_answer_handler
poll_to_user_map = {}


# --- MONGO SETUP ---
try:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    quizzes = db["quizzes"]
    logger.info("MongoDB Connected Successfully!")
except Exception as e:
    logger.error(f"Error connecting to MongoDB: {e}")
    exit()

# --- FSM States for Quiz Creation ---
class QuizCreate(StatesGroup):
    title = State()
    description = State()
    question = State()
    options = State()
    correct_option = State()

# --- FSM States for Quiz Play ---
class QuizPlay(StatesGroup):
    in_progress = State()


# -------------------- START COMMAND --------------------
@router.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🆕 Create New Quiz", callback_data="create_quiz")],
            [types.InlineKeyboardButton(text="📚 View My Quizzes", callback_data="view_quizzes")],
        ]
    )
    await message.answer(
        "This bot will help you create and play quizzes with multiple choice questions.",
        reply_markup=keyboard,
    )

# -------------------- MAIN MENU BUTTONS --------------------
@router.callback_query(F.data == "create_quiz")
async def start_quiz_creation(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(QuizCreate.title)
    await query.message.answer("Send me the *title* of your quiz.")
    await query.answer()

@router.callback_query(F.data == "view_quizzes")
async def view_my_quizzes(query: CallbackQuery):
    user_id = query.from_user.id
    try:
        user_quizzes = list(quizzes.find({"user_id": user_id}))
        if not user_quizzes:
            await query.message.answer("❌ You have no saved quizzes.")
            await query.answer()
            return

        buttons = [
            [types.InlineKeyboardButton(text=f"▶️ {q['title']}", callback_data=f"play_{q['_id']}")]
            for q in user_quizzes
        ]
        await query.message.answer("📚 Your quizzes:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        logger.error(f"Error fetching quizzes from DB: {e}")
        await query.message.answer("❌ Could not fetch your quizzes. Please try again later.")
    await query.answer()

# -------------------- QUIZ CREATION FLOW (USING FSM) --------------------
@router.message(QuizCreate.title)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(QuizCreate.description)
    await message.answer("Send me a *description* of your quiz. Or type /skip.")

@router.message(QuizCreate.description, Command("skip"))
async def process_skip_description(message: Message, state: FSMContext):
    await state.update_data(description="")
    await state.update_data(questions=[])
    await state.set_state(QuizCreate.question)
    await message.answer("Send your first *question*.")

@router.message(QuizCreate.description)
async def process_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.update_data(questions=[])
    await state.set_state(QuizCreate.question)
    await message.answer("Send your first *question*.")

@router.message(QuizCreate.question)
async def process_question(message: Message, state: FSMContext):
    await state.update_data(current_question={"question": message.text, "options": []})
    await state.set_state(QuizCreate.options)
    await message.answer("Send option 1 for this question:")

@router.message(QuizCreate.options)
async def process_options(message: Message, state: FSMContext):
    user_data = await state.get_data()
    current_question = user_data.get("current_question", {})
    current_question["options"].append(message.text)
    await state.update_data(current_question=current_question)

    if len(current_question["options"]) < 2:
        await message.answer(f"Send option {len(current_question['options'])+1}:")
    else:
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="➕ Add More Option", callback_data="add_option")],
                [types.InlineKeyboardButton(text="✅ Done", callback_data="done_options")],
            ]
        )
        await message.answer(
            f"Option {len(current_question['options'])} saved. What next?",
            reply_markup=keyboard,
        )

@router.callback_query(QuizCreate.options, F.data == "add_option")
async def add_option_button(query: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    options_count = len(user_data.get("current_question", {}).get("options", []))
    await query.message.answer(f"Send option {options_count + 1}:")
    await query.answer()

@router.callback_query(QuizCreate.options, F.data == "done_options")
async def done_options_button(query: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    opts = user_data.get("current_question", {}).get("options", [])
    buttons = [[types.InlineKeyboardButton(text=o, callback_data=f"correct_{i}")] for i, o in enumerate(opts)]
    await state.set_state(QuizCreate.correct_option)
    await query.message.answer(
        "Which one is the correct option?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await query.answer()

@router.callback_query(QuizCreate.correct_option, F.data.startswith("correct_"))
async def process_correct_option(query: CallbackQuery, state: FSMContext):
    correct_index = int(query.data.split("_")[1])
    user_data = await state.get_data()
    
    current_question = user_data.get("current_question", {})
    current_question["correct_index"] = correct_index
    
    questions = user_data.get("questions", [])
    questions.append(current_question)
    
    await state.update_data(questions=questions)
    await state.update_data(current_question=None) # Clear current question
    
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="➕ Add Another Question", callback_data="new_question")],
            [types.InlineKeyboardButton(text="✅ Finish & Save Quiz", callback_data="finish_quiz")],
        ]
    )
    await query.message.answer("Question added! What next?", reply_markup=keyboard)
    await query.answer()

@router.callback_query(QuizCreate.correct_option, F.data == "new_question")
async def new_question_handler(query: CallbackQuery, state: FSMContext):
    await state.set_state(QuizCreate.question)
    await query.message.answer("Send me the next *question*.")
    await query.answer()

@router.callback_query(QuizCreate.correct_option, F.data == "finish_quiz")
async def finish_quiz_handler(query: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    try:
        quiz_data = {
            "user_id": query.from_user.id,
            "title": user_data.get("title", "Untitled Quiz"),
            "description": user_data.get("description", ""),
            "questions": user_data.get("questions", []),
        }
        if not quiz_data["questions"]:
            await query.message.answer("❌ Cannot save a quiz with no questions!")
            await query.answer()
            return
            
        quizzes.insert_one(quiz_data)
        await query.message.answer(f"✅ Your quiz '{quiz_data['title']}' has been saved successfully!")
        await state.clear()
    except Exception as e:
        logger.error(f"Error saving new quiz to DB: {e}")
        await query.message.answer("❌ Could not save your quiz. Please try again later.")
    await query.answer()

# -------------------- QUIZ PLAY FLOW --------------------
@router.callback_query(F.data.regexp(r"^play_(?!shuffle_|timer_).+$"))
async def play_quiz_start(query: CallbackQuery, state: FSMContext):
    quiz_id = query.data.replace("play_", "")
    await state.update_data(quiz_id=quiz_id)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔀 Shuffle All", callback_data=f"play_shuffle_shuffle_all")],
            [types.InlineKeyboardButton(text="❌ No Shuffle", callback_data=f"play_shuffle_no_shuffle")],
            [types.InlineKeyboardButton(text="🔁 Only Answers", callback_data=f"play_shuffle_shuffle_answers")],
            [types.InlineKeyboardButton(text="🔂 Only Questions", callback_data=f"play_shuffle_shuffle_questions")],
        ]
    )
    await query.message.answer(
        "Choose how you want to shuffle this quiz:",
        reply_markup=keyboard,
    )
    await query.answer()


@router.callback_query(F.data.startswith("play_shuffle_"))
async def shuffle_handler(query: CallbackQuery, state: FSMContext):
    shuffle_option = query.data.replace("play_shuffle_", "")
    await state.update_data(shuffle_option=shuffle_option)
    
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="10s", callback_data=f"play_timer_10"),
             types.InlineKeyboardButton(text="15s", callback_data=f"play_timer_15"),
             types.InlineKeyboardButton(text="30s", callback_data=f"play_timer_30")],
            [types.InlineKeyboardButton(text="45s", callback_data=f"play_timer_45"),
             types.InlineKeyboardButton(text="1min", callback_data=f"play_timer_60")],
        ]
    )
    await query.message.answer(
        "⏱ Select time per question for this quiz:",
        reply_markup=keyboard,
    )
    await query.answer()

@router.callback_query(F.data.startswith("play_timer_"))
async def play_timer_handler(query: CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    timer = int(query.data.replace("play_timer_", ""))
    user_data = await state.get_data()
    quiz_id = user_data.get("quiz_id")

    if not quiz_id:
        await query.message.answer("❌ Error: Quiz ID not found. Please start over.")
        await query.answer()
        return

    try:
        quiz = quizzes.find_one({"_id": ObjectId(quiz_id)})
        # YAHAN PAR NAYA SAFETY CHECK ADD KIYA GAYA HAI
        if not quiz or not quiz.get("questions"):
            await query.message.answer("❌ This quiz was not found or is empty. Cannot start.")
            await query.answer()
            return
            
    except Exception as e:
        logger.error(f"Error finding quiz {quiz_id}: {e}")
        await query.message.answer("❌ Could not start quiz. Please try again later.")
        await query.answer()
        return
    
    await state.set_state(QuizPlay.in_progress)
    start_time = time.time()
    shuffle_option = user_data.get("shuffle_option", "no_shuffle")
    await query.message.answer(f"▶️ Starting quiz: *{quiz['title']}*")
    await query.answer()

    questions = copy.deepcopy(quiz["questions"])
    if shuffle_option in ["shuffle_all", "shuffle_questions"]:
        random.shuffle(questions)

    user_lock = user_locks[user_id]
    async with user_lock:
        await state.update_data(
            correct_count=0,
            wrong_count=0,
            session_correct_answers={},
            start_time=start_time
        )
    
    for idx, q in enumerate(questions, start=1):
        options = q["options"][:]
        correct_index = q["correct_index"]
        
        if shuffle_option in ["shuffle_all", "shuffle_answers"]:
            paired = list(enumerate(options))
            random.shuffle(paired)
            new_indices, new_options = zip(*paired)
            options = list(new_options)
            shuffled_correct_index = list(new_indices).index(correct_index)
        else:
            shuffled_correct_index = correct_index

        async with user_lock:
            current_fsm_data = await state.get_data()
            session_answers = current_fsm_data.get("session_correct_answers", {})
            session_answers[str(idx)] = shuffled_correct_index
            await state.update_data(session_correct_answers=session_answers)
            
        poll_message = await bot.send_poll(
            chat_id=query.message.chat.id,
            question=f"Q{idx}: {q['question']}",
            options=options,
            type=Poll.QUIZ,
            correct_option_id=int(shuffled_correct_index),
            open_period=timer,
            is_anonymous=False,
        )
        poll_to_user_map[poll_message.poll.id] = {"user_id": user_id, "question_idx": str(idx)}
        await asyncio.sleep(timer)
    
    # Wait for a moment to ensure last poll answer can be processed
    await asyncio.sleep(2)

    async with user_lock:
        final_user_data = await state.get_data()
        total_questions = len(questions)
        correct_count = final_user_data.get("correct_count", 0)
        wrong_count = final_user_data.get("wrong_count", 0)
        missed_count = total_questions - (correct_count + wrong_count)
        
        end_time = time.time()
        duration = int(end_time - final_user_data.get("start_time", start_time))

        leaderboard_text = (
            f"🏁 The quiz '{quiz['title']}' has finished!\n\n"
            f"You answered {correct_count + wrong_count} out of {total_questions} questions:\n\n"
            f"✅ Correct – {correct_count}\n"
            f"❌ Wrong – {wrong_count}\n"
            f"⌛️ Missed – {missed_count}\n"
            f"⏱️ {duration} sec\n\n"
            f"🥇1st place out of 1."
        )

        await bot.send_message(chat_id=user_id, text=leaderboard_text)
        await state.clear()
        
    if user_id in user_locks:
        del user_locks[user_id]


@router.poll_answer()
async def poll_answer_handler(poll_answer: PollAnswer):
    poll_id = poll_answer.poll_id
    poll_info = poll_to_user_map.pop(poll_id, None)

    if not poll_info:
        return

    user_id = poll_info["user_id"]
    question_idx = poll_info["question_idx"]
    user_lock = user_locks[user_id]

    # Create a temporary FSMContext to access the user's state
    user_state = FSMContext(storage, key=storage.get_key(bot, user_id))

    async with user_lock:
        user_data = await user_state.get_data()
        if not user_data: return

        if poll_answer.option_ids:
            selected_option = poll_answer.option_ids[0]
            correct_answers = user_data.get("session_correct_answers", {})
            
            if selected_option == correct_answers.get(question_idx):
                await user_state.update_data(correct_count=user_data.get('correct_count', 0) + 1)
            else:
                await user_state.update_data(wrong_count=user_data.get('wrong_count', 0) + 1)

# -------------------- MAIN --------------------
async def main():
    dp.include_router(router)
    # Start polling
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
