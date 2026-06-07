import os
import json
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Welcome to QuizMaster Bot!\n\nSend me any topic and I'll generate a quiz!\nExample: Cardiology or Python basics"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text
    await update.message.reply_text(f"⏳ Generating quiz on {topic}...")
    prompt = f"""Generate 5 multiple choice questions about {topic}.
Format as JSON array:
[{{"question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], "answer": "A"}}]
Return ONLY the JSON array, nothing else."""
    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.choices[0].message.content
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    questions = json.loads(match.group())
    context.user_data["questions"] = questions
    context.user_data["score"] = 0
    context.user_data["index"] = 0
    await ask_question(update, context)

async def ask_question(update, context):
    questions = context.user_data["questions"]
    idx = context.user_data["index"]
    if idx >= len(questions):
        score = context.user_data["score"]
        await update.message.reply_text(f"✅ Done! Score: {score}/{len(questions)}\n\nSend another topic to play again!")
        context.user_data.clear()
        return
    q = questions[idx]
    text = f"Q{idx+1}: {q['question']}\n\n" + "\n".join(q["options"])
    await update.message.reply_text(text)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
