import os
import random
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# ======================
# CONFIG
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

BIOCHEM_LINK = "https://t.me/biochemquizi"

# ======================
# SAMPLE MCQs (you can expand later)
# ======================
MCQS = [
    {
        "q": "Rate limiting enzyme of ketogenesis is:",
        "options": ["HMG CoA synthase", "HMG CoA reductase", "Citrate synthase", "Pyruvate carboxylase"],
        "answer": "HMG CoA synthase"
    },
    {
        "q": "G6PD deficiency leads to:",
        "options": ["Hemolysis", "Polycythemia", "Thrombocytosis", "Leukocytosis"],
        "answer": "Hemolysis"
    }
]

# ======================
# START COMMAND
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Biochem MCQ Bot!\nType /mcq to get a question."
    )

# ======================
# MCQ GENERATOR
# ======================
async def mcq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = random.choice(MCQS)

    text = f"""
🧪 *Biochemistry MCQ*

{q['q']}

A) {q['options'][0]}
B) {q['options'][1]}
C) {q['options'][2]}
D) {q['options'][3]}
"""

    context.user_data["current_answer"] = q["answer"]

    await update.message.reply_text(text, parse_mode="Markdown")

# ======================
# ANSWER HANDLER
# ======================
async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_ans = update.message.text.strip()
    correct = context.user_data.get("current_answer")

    if not correct:
        await update.message.reply_text("Send /mcq first.")
        return

    prompt = f"""
You are a medical tutor for biochemistry.

Question correct answer: {correct}
User answer: {user_ans}

Give:
1. Correct explanation
2. Why other options are wrong
Keep it concise.
"""

    response = client.chat.completions.create(
        model="llama-3.1-70b-versatile",
        messages=[{"role": "user", "content": prompt}]
    )

    explanation = response.choices[0].message.content

    # ======================
    # SPOILER FORMAT OUTPUT
    # ======================
    final_text = f"""||🧪 Answer: {correct}

📘 Explanation:
{explanation}

🔗 Join for more: {BIOCHEM_LINK}||"""

    await update.message.reply_text(final_text, parse_mode="MarkdownV2")


# ======================
# MAIN APP
# ======================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mcq", mcq))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
