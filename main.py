import os
import json
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Welcome to QuizMaster Bot!\n\n"
        "Send me any topic and I'll generate a quiz for you!\n"
        "Example: *Cardiology* or *Python basics*",
        parse_mode="Markdown"
    )

async def generate_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text
    await update.message.reply_text(f"⏳ Generating quiz on *{topic}*...", parse_mode="Markdown")

    prompt = f"""Generate 5 multiple choice questions about {topic}.
Format as JSON array like this:
[{{"question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], "answer": "A"}}]
Return ONLY the JSON, nothing else."""

    response = groq_client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    questions = json.loads(match.group())

    context.user_data["questions"] = questions
    context.user_data["score"] = 0
    context.user_data["current"] = 0

    await send_question(update, context)

async def send_question(update, context):
    questions = context.user_data["questions"]
    idx = context.user_data["current"]

    if idx >= len(questions):
        score = context.user_data["score"]
        await update.message.reply_text(f"✅ Quiz done! Score: {score}/{len(questions)}\n\nSend another topic to play again!")
        return

    q = questions[idx]
    text = f"*Q{idx+1}: {q['question']}*\n\n" + "\n".join(q["options"])
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "questions" not in context.user_data:
        await generate_quiz(update, context)
        return

    questions = context.user_data["questions"]
    idx = context.user_data["current"]
    answer = update.message.text.strip().upper()
    correct = questions[idx]["answer"].upper()

    if answer == correct:
        context.user_data["score"] += 1
        await update.message.reply_text("✅ Correct!")
    else:
        await update.message.reply_text(f"❌ Wrong! Correct answer: {correct}")

    context.user_data["current"] += 1
    await send_question(update, context)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
    app.run_polling()

if __name__ == "__main__":
    main()
