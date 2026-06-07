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
[{{"question": "...", "options": ["Option 1", "Option 2", "Option 3", "Option 4"], "answer_index": 0}}]
answer_index is 0-based index of correct option.
Return ONLY the JSON array, nothing else."""
    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.choices[0].message.content
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    questions = json.loads(match.group())
    for q in questions:
        await update.message.reply_poll(
            question=q["question"],
            options=q["options"],
            type="quiz",
            correct_option_id=int(q["answer_index"]),
            is_anonymous=False
        )

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
