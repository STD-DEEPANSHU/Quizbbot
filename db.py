from pymongo import MongoClient
from config import Config

client = MongoClient(Config.MONGO_URI)
db = client[Config.DB_NAME]

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
