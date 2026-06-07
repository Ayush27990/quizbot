import os
import json
import re
import io
import base64
import logging
import PyPDF2

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)

from groq import Groq

# =========================
# CONFIG
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")

groq_client = Groq(api_key=GROQ_API_KEY)

poll_store = {}

# =========================
# HELPERS
# =========================

def extract_json(text):
    try:
        match = re.search(r"\[.*\]", text, re.DOTALL)

        if not match:
            return []

        return json.loads(match.group())

    except Exception as e:
        logger.error(f"JSON extraction error: {e}")
        return []


def build_prompt(content):
    return f"""
You are an expert educator.

Generate EXACTLY 5 high-quality multiple-choice questions.

Rules:
- Four options per question
- One correct answer
- Clinical style if medical content
- Reasoning-based if general content
- Detailed explanation
- Explain why wrong options are wrong

Return ONLY JSON.

Format:

[
 {{
   "question":"...",
   "options":["A","B","C","D"],
   "answer_index":0,
   "explanation":"..."
 }}
]

Content:

{content}
"""


async def generate_questions(content):
    try:
        prompt = build_prompt(content)

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        raw = response.choices[0].message.content

        questions = extract_json(raw)

        return questions

    except Exception as e:
        logger.error(f"Question generation failed: {e}")
        return []


# =========================
# TELEGRAM COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎯 QuizMaster Bot\n\n"
        "Send:\n"
        "📝 Topic (Cardiology)\n"
        "📄 PDF\n"
        "🖼 Image\n\n"
        "and I will generate quizzes."
    )


# =========================
# QUIZ SENDER
# =========================

async def send_quiz(update, questions):

    if not questions:

        await update.message.reply_text(
            "❌ Could not generate questions."
        )
        return

    for q in questions:

        try:

            explanation = q.get(
                "explanation",
                "No explanation available."
            )

            poll_message = await update.message.reply_poll(
                question=q["question"][:300],
                options=q["options"],
                type="quiz",
                correct_option_id=int(q["answer_index"]),
                is_anonymous=False,
                explanation=explanation[:200]
            )

            poll_store[poll_message.poll.id] = {
                "chat_id": update.effective_chat.id
            }

        except Exception as e:
            logger.error(f"Poll send error: {e}")


# =========================
# TEXT HANDLER
# =========================

async def handle_topic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    topic = update.message.text.strip()

    await update.message.reply_text(
        f"⏳ Generating quiz on:\n{topic}"
    )

    questions = await generate_questions(topic)

    await send_quiz(update, questions)


# =========================
# PDF HANDLER
# =========================

async def handle_pdf(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        await update.message.reply_text(
            "📄 PDF received.\nExtracting text..."
        )

        file = await update.message.document.get_file()

        file_bytes = await file.download_as_bytearray()

        pdf_reader = PyPDF2.PdfReader(
            io.BytesIO(bytes(file_bytes))
        )

        text = ""

        max_pages = min(
            len(pdf_reader.pages),
            10
        )

        for page in pdf_reader.pages[:max_pages]:

            extracted = page.extract_text()

            if extracted:
                text += extracted + "\n"

        if not text.strip():

            await update.message.reply_text(
                "❌ Could not extract text."
            )

            return

        text = text[:4000]

        await update.message.reply_text(
            "⏳ Generating quiz..."
        )

        questions = await generate_questions(text)

        await send_quiz(update, questions)

    except Exception as e:

        logger.error(f"PDF error: {e}")

        await update.message.reply_text(
            "❌ PDF processing failed."
        )


# =========================
# IMAGE HANDLER
# =========================

async def handle_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        await update.message.reply_text(
            "🖼 Analyzing image..."
        )

        photo = None

        if update.message.photo:
            photo = update.message.photo[-1]
            file = await photo.get_file()

        elif update.message.document:
            file = await update.message.document.get_file()

        else:
            return

        file_bytes = await file.download_as_bytearray()

        encoded = base64.b64encode(
            bytes(file_bytes)
        ).decode()

        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url":
                                f"data:image/jpeg;base64,{encoded}"
                            }
                        },
                        {
                            "type": "text",
                            "text":
                            "Extract all educational information from this image."
                        }
                    ]
                }
            ]
        )

        extracted = response.choices[0].message.content

        if not extracted:

            await update.message.reply_text(
                "❌ No information found."
            )

            return

        await update.message.reply_text(
            "⏳ Generating quiz..."
        )

        questions = await generate_questions(extracted)

        await send_quiz(update, questions)

    except Exception as e:

        logger.error(f"Image error: {e}")

        await update.message.reply_text(
            "❌ Image processing failed."
        )


# =========================
# POLL ANSWERS
# =========================

async def handle_poll_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        poll_id = update.poll_answer.poll_id

        if poll_id not in poll_store:
            return

        logger.info(
            f"Poll answered: {poll_id}"
        )

    except Exception as e:

        logger.error(
            f"Poll answer error: {e}"
        )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update,
    context
):

    logger.error(
        f"Update error: {context.error}"
    )


# =========================
# MAIN
# =========================

def main():

    logger.info(
        "Starting QuizMaster Bot..."
    )

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        PollAnswerHandler(
            handle_poll_answer
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.PDF,
            handle_pdf
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_image
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT &
            ~filters.COMMAND,
            handle_topic
        )
    )

    app.add_error_handler(
        error_handler
    )

    app.run_polling(
        allowed_updates=[
            "message",
            "poll",
            "poll_answer"
        ]
    )


if __name__ == "__main__":
    main()

