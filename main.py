import os
import json
import re
import io
import base64
import logging
import PyPDF2
import httpx
from bs4 import BeautifulSoup

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing")

groq_client = Groq(api_key=GROQ_API_KEY)
poll_store = {}

def escape_md(text):
    for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(ch, f"\\{ch}")
    return text

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
You are an expert medical educator.
Generate EXACTLY 5 high-quality multiple-choice questions.
Rules:
- Four options per question
- One correct answer
- Clinical style if medical content
- Detailed explanation
- Explain why wrong options are wrong
Return ONLY JSON.
Format:
[
 {{
   "question":"...",
   "options":["A","B","C","D"],
   "answer_index":0,
   "explanation":"Correct: A because... B is wrong because... C is wrong because... D is wrong because..."
 }}
]
Content:
{content}
"""

def build_image_prompt():
    return """You are an expert medical educator.
Analyze this medical image carefully.
Generate EXACTLY 3 high-quality multiple-choice questions based on what you see.
Rules:
- Four options per question
- One correct answer
- Clinical style questions
- Reference specific findings visible in the image
- Detailed explanation of findings
Return ONLY JSON.
Format:
[
 {{
   "question":"Based on this image, ...",
   "options":["A","B","C","D"],
   "answer_index":0,
   "explanation":"Correct: A because... B is wrong because... C is wrong because... D is wrong because..."
 }}
]"""

async def generate_questions(content):
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": build_prompt(content)}]
        )
        raw = response.choices[0].message.content
        return extract_json(raw)
    except Exception as e:
        logger.error(f"Question generation failed: {e}")
        return []

async def analyze_image_and_generate(image_bytes):
    try:
        encoded = base64.b64encode(image_bytes).decode()
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                    {"type": "text", "text": build_image_prompt()}
                ]
            }]
        )
        raw = response.choices[0].message.content
        return extract_json(raw)
    except Exception as e:
        logger.error(f"Image analysis error: {e}")
        return []

async def fetch_url_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text[:4000]
    except Exception as e:
        logger.error(f"URL fetch error: {e}")
        return None

async def fetch_images_from_url(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        images = []
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            soup = BeautifulSoup(response.text, "html.parser")
            base_url = "/".join(url.split("/")[:3])
            img_tags = soup.find_all("img")
            for img in img_tags[:5]:
                src = img.get("src") or img.get("data-src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = base_url + src
                elif not src.startswith("http"):
                    continue
                skip_keywords = ["logo", "icon", "avatar", "banner", "ad", "social", "button", "arrow"]
                if any(k in src.lower() for k in skip_keywords):
                    continue
                try:
                    img_response = await client.get(src, headers=headers, follow_redirects=True, timeout=10)
                    if "image" in img_response.headers.get("content-type", ""):
                        if len(img_response.content) > 10000:
                            images.append(img_response.content)
                except:
                    continue
                if len(images) >= 3:
                    break
        return images
    except Exception as e:
        logger.error(f"Image fetch error: {e}")
        return []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 QuizMaster Bot\n\n"
        "Send:\n"
        "📝 Topic (e.g. Cardiology)\n"
        "📄 PDF\n"
        "🖼 Image\n"
        "🔗 URL (Radiopaedia, LITFL etc)\n\n"
        "For URLs → I extract images + text and create visual MCQs!\n"
        "Answer each question to get detailed explanation!"
    )

async def send_quiz(update, questions, image_bytes=None):
    if not questions:
        await update.message.reply_text("❌ Could not generate questions.")
        return
    for i, q in enumerate(questions):
        try:
            if image_bytes and i == 0:
                await update.message.reply_photo(
                    photo=io.BytesIO(image_bytes),
                    caption="🔍 Study this image carefully before answering!"
                )
            poll_message = await update.message.reply_poll(
                question=q["question"][:300],
                options=q["options"],
                type="quiz",
                correct_option_id=int(q["answer_index"]),
                is_anonymous=False
            )
            poll_store[poll_message.poll.id] = {
                "chat_id": update.effective_chat.id,
                "explanation": q.get("explanation", "No explanation available."),
                "correct_index": int(q["answer_index"]),
                "options": q["options"]
            }
        except Exception as e:
            logger.error(f"Poll send error: {e}")

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        poll_id = update.poll_answer.poll_id
        if poll_id not in poll_store:
            return
        data = poll_store[poll_id]
        chosen = update.poll_answer.option_ids[0]
        correct_index = data["correct_index"]
        options = data["options"]
        explanation = data["explanation"]
        if chosen == correct_index:
            result = "✅ Correct!"
        else:
            result = f"❌ Wrong! Correct: {options[correct_index]}"
        result_escaped = escape_md(result)
        explanation_escaped = escape_md(explanation)
        spoiler_text = f"{result_escaped}\n\n||{explanation_escaped}||"
        await context.bot.send_message(
            chat_id=data["chat_id"],
            text=spoiler_text,
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Poll answer error: {e}")

async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("http://") or text.startswith("https://"):
        await update.message.reply_text("🔗 URL received! Fetching content and images...")
        content = await fetch_url_content(text)
        images = await fetch_images_from_url(text)
        if not content and not images:
            await update.message.reply_text("❌ Could not fetch content from URL.")
            return
        if images:
            await update.message.reply_text(f"🖼 Found {len(images)} image(s)! Generating image-based MCQs...")
            for image_bytes in images:
                questions = await analyze_image_and_generate(image_bytes)
                if questions:
                    await send_quiz(update, questions, image_bytes=image_bytes)
            if content:
                await update.message.reply_text("📝 Also generating text-based MCQs from article...")
                questions = await generate_questions(content)
                await send_quiz(update, questions)
        else:
            await update.message.reply_text("⏳ No images found. Generating quiz from text...")
            questions = await generate_questions(content)
            await send_quiz(update, questions)
    else:
        await update.message.reply_text(f"⏳ Generating quiz on: {text}")
        questions = await generate_questions(text)
        await send_quiz(update, questions)

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("📄 PDF received. Extracting text...")
        file = await update.message.document.get_file()
        file_bytes = await file.download_as_bytearray()
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(bytes(file_bytes)))
        text = ""
        for page in pdf_reader.pages[:10]:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
        if not text.strip():
            await update.message.reply_text("❌ Could not extract text.")
            return
        text = text[:4000]
        await update.message.reply_text("⏳ Generating quiz...")
        questions = await generate_questions(text)
        await send_quiz(update, questions)
    except Exception as e:
        logger.error(f"PDF error: {e}")
        await update.message.reply_text("❌ PDF processing failed.")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("🖼 Analyzing image...")
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
        elif update.message.document:
            file = await update.message.document.get_file()
        else:
            return
        file_bytes = bytes(await file.download_as_bytearray())
        questions = await analyze_image_and_generate(file_bytes)
        if not questions:
            await update.message.reply_text("❌ Could not generate questions from image.")
            return
        await update.message.reply_text("⏳ Sending quiz...")
        await send_quiz(update, questions, image_bytes=file_bytes)
    except Exception as e:
        logger.error(f"Image error: {e}")
        await update.message.reply_text("❌ Image processing failed.")

async def error_handler(update, context):
    logger.error(f"Update error: {context.error}")

def main():
    logger.info("Starting QuizMaster Bot...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=["message", "poll", "poll_answer"])

if __name__ == "__main__":
    main()
