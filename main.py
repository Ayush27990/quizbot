import os
import json
import re
import PyPDF2
import io
import base64
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Welcome to QuizMaster Bot!\n\nSend me:\n📝 A topic → e.g. Cardiology\n📄 A PDF → quiz from PDF\n🖼 An image → quiz from image!"
    )

async def generate_and_send_quiz(update, topic_or_text):
    prompt = f"""Generate 5 multiple choice questions based on this content:
{topic_or_text}
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
            question=q["question"][:300],
            options=q["options"],
            type="quiz",
            correct_option_id=int(q["answer_index"]),
            is_anonymous=False
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text
    await update.message.reply_text(f"⏳ Generating quiz on {topic}...")
    await generate_and_send_quiz(update, topic)

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📄 PDF received! Extracting text...")
    file = await update.message.document.get_file()
    file_bytes = await file.download_as_bytearray()
    pdf_reader = PyPDF2.PdfReader(io.BytesIO(bytes(file_bytes)))
    text = ""
    for page in pdf_reader.pages[:5]:
        text += page.extract_text() or ""
    if not text.strip():
        await update.message.reply_text("❌ Could not extract text from PDF.")
        return
    text = text[:3000]
    await update.message.reply_text("⏳ Generating quiz from your PDF...")
    await generate_and_send_quiz(update, text)

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🖼 Image received! Analyzing...")
    photo = update.message.photo[-1] if update.message.photo else None
    doc = update.message.document if update.message.document else None
    if photo:
        file = await photo.get_file()
    else:
        file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()
    base64_image = base64.b64encode(bytes(file_bytes)).decode("utf-8")
    response = groq_client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    },
                    {
                        "type": "text",
                        "text": "Extract all text, MCQs, and key information from this image. Return everything you see."
                    }
                ]
            }
        ]
    )
    extracted = response.choices[0].message.content
    if not extracted.strip():
        await update.message.reply_text("❌ Could not extract information from image.")
        return
    await update.message.reply_text("⏳ Generating quiz from image...")
    await generate_and_send_quiz(update, extracted)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
