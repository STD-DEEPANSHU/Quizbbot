import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "quiz_bot")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# quizzes collection
quizzes = db["quizzes"]

def save_quiz(user_id, title, description, questions, timer, shuffle):
    quiz_data = {
        "user_id": user_id,
        "title": title,
        "description": description,
        "questions": questions,
        "timer": timer,
        "shuffle": shuffle
    }
    quizzes.insert_one(quiz_data)

def get_user_quizzes(user_id):
    return list(quizzes.find({"user_id": user_id}))
