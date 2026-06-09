import os
import json
import re
import time
import logging
import asyncio
import io
import base64
import random

import PyPDF2
import httpx
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
   ApplicationBuilder,
   CommandHandler,
   CallbackQueryHandler,
   MessageHandler,
   ContextTypes,
   filters
)
from groq import Groq

logging.basicConfig(
   level=logging.INFO,
   format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MEDICINE_GROUP_ID = os.getenv("MEDICINE_GROUP_ID")
ADMIN_ID = 723919716
INTERVAL = 900

if not TELEGRAM_TOKEN:
   raise ValueError("TELEGRAM_TOKEN missing")
if not GROQ_API_KEY:
   raise ValueError("GROQ_API_KEY missing")
if not MEDICINE_GROUP_ID:
   raise ValueError("MEDICINE_GROUP_ID missing")

client = Groq(api_key=GROQ_API_KEY)
pending_questions = {}
used_topics = []

HARRISON_TOPICS = [
   "Approach to the patient with chest pain",
   "Acute coronary syndrome STEMI NSTEMI",
   "Heart failure systolic diastolic management",
   "Atrial fibrillation management anticoagulation",
   "Hypertensive emergency urgency",
   "Aortic stenosis clinical features management",
   "Infective endocarditis diagnosis Duke criteria",
   "Pericarditis and cardiac tamponade",
   "Pulmonary embolism diagnosis management",
   "Deep vein thrombosis anticoagulation",
   "Community acquired pneumonia management",
   "Tuberculosis diagnosis treatment",
   "COPD exacerbation management",
   "Asthma acute severe management",
   "Pleural effusion causes diagnosis",
   "Acute respiratory distress syndrome",
   "Pneumothorax types management",
   "Peptic ulcer disease H pylori",
   "Inflammatory bowel disease Crohn ulcerative colitis",
   "Acute pancreatitis severity management",
   "Liver cirrhosis complications management",
   "Hepatitis B C diagnosis treatment",
   "Acute liver failure causes management",
   "Acute kidney injury causes management",
   "Chronic kidney disease complications",
   "Nephrotic syndrome causes management",
   "Nephritic syndrome glomerulonephritis",
   "Diabetic ketoacidosis management",
   "Hyperosmolar hyperglycemic state",
   "Hypothyroidism hyperthyroidism management",
   "Adrenal insufficiency Addison disease",
   "Cushing syndrome diagnosis",
   "Diabetes mellitus type 1 type 2 complications",
   "Hyponatremia hypernatremia management",
   "Hypokalemia hyperkalemia ECG changes",
   "Hypercalcemia hypocalcemia causes",
   "Metabolic acidosis alkalosis approach",
   "Respiratory acidosis alkalosis approach",
   "Anemia approach iron deficiency",
   "Megaloblastic anemia B12 folate",
   "Hemolytic anemia causes workup",
   "Sickle cell disease complications",
   "Thrombocytopenia causes ITP TTP",
   "Disseminated intravascular coagulation",
   "Leukemia acute chronic types",
   "Lymphoma Hodgkin non Hodgkin",
   "Multiple myeloma diagnosis treatment",
   "Rheumatoid arthritis diagnosis management",
   "Systemic lupus erythematosus criteria",
   "Sepsis septic shock management",
   "Meningitis bacterial viral management",
   "Stroke ischemic hemorrhagic management",
   "Seizures epilepsy management",
   "Guillain Barre syndrome",
   "Myasthenia gravis diagnosis treatment",
   "Parkinson disease management",
   "HIV AIDS opportunistic infections",
   "Malaria diagnosis treatment",
   "Typhoid fever diagnosis treatment",
   "Dengue fever management",
   "Approach to fever of unknown origin",
]

def escape_md(text):
   for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">",
              "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
       text = text.replace(ch, f"\\{ch}")
   return text

def extract_json_list(text):
   try:
       match = re.search(r"\[.*\]", text, re.DOTALL)
       if match:
           result = json.loads(match.group())
           if isinstance(result, list):
               return result
       return []
   except Exception as e:
       logger.error("JSON parse error: " + str(e))
       return []

def extract_youtube_id(url):
   patterns = [
       r"youtube\.com/watch\?v=([^&]+)",
       r"youtu\.be/([^?]+)",
       r"youtube\.com/shorts/([^?]+)"
   ]
   for pattern in patterns:
       match = re.search(pattern, url)
       if match:
           return match.group(1)
   return None

async def get_youtube_transcript(video_id):
   try:
       transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
       text = " ".join([t["text"] for t in transcript_list])
       return text[:4000]
   except Exception as e:
       logger.error("YouTube transcript error: " + str(e))
       return None

async def fetch_url_content(url):
   try:
       headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
       async with httpx.AsyncClient(timeout=15) as client_http:
           response = await client_http.get(url, headers=headers, follow_redirects=True)
           soup = BeautifulSoup(response.text, "html.parser")
           for tag in soup(["script", "style", "nav", "footer", "header"]):
               tag.decompose()
           text = soup.get_text(separator="\n", strip=True)
           text = re.sub(r"\n{3,}", "\n\n", text)
           return text[:4000]
   except Exception as e:
       logger.error("URL fetch error: " + str(e))
       return None

async def generate_topic():
   used = ", ".join(used_topics[-20:]) if used_topics else "none"
   prompt = (
       "You are a Harrison Internal Medicine expert.\n\n"
       "Suggest ONE specific high-yield Internal Medicine topic.\n\n"
       "Already used (avoid repeating): " + used + "\n\n"
       "Must be from Harrison Principles of Internal Medicine.\n"
       "High yield for NEET PG / USMLE / FMGE.\n\n"
       'Return ONLY JSON: {"topic": "Acute coronary syndrome management"}'
   )
   try:
       response = client.chat.completions.create(
           model="llama-3.3-70b-versatile",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.9
       )
       text = response.choices[0].message.content
       match = re.search(r"\{.*\}", text, re.DOTALL)
       if match:
           result = json.loads(match.group())
           topic = result.get("topic", "Internal medicine high yield topic")
           used_topics.append(topic)
           if len(used_topics) > 100:
               used_topics.pop(0)
           return topic
       return random.choice(HARRISON_TOPICS)
   except Exception as e:
       logger.error("Topic generation error: " + str(e))
       return random.choice(HARRISON_TOPICS)

async def generate_questions_from_content(content, count=2):
   prompt = (
       "You are a Harrison Internal Medicine expert examiner.\n\n"
       "Generate EXACTLY " + str(count) + " high-yield clinical MCQs based on:\n\n"
       + content + "\n\n"
       "Rules:\n"
       "- Clinical vignette with patient scenario\n"
       "- 4 options, one definitively correct\n"
       "- Detailed explanation\n"
       "- Explain why each wrong option is incorrect\n"
       "- NEET PG / USMLE standard\n\n"
       "Return ONLY JSON array:\n"
       "[\n"
       "  {\n"
       '    "question": "A 55-year-old patient presents with...",\n'
       '    "options": ["A) ...", "B) ...", "C) ...", "D) ..."],\n'
       '    "answer_index": 0,\n'
       '    "explanation": "Correct: A because... B is wrong because..."\n'
       "  }\n"
       "]"
   )
   try:
       response = client.chat.completions.create(
           model="llama-3.3-70b-versatile",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.3
       )
       return extract_json_list(response.choices[0].message.content)
   except Exception as e:
       logger.error("Question generation error: " + str(e))
       return []

async def rephrase_forwarded_mcq(text):
   prompt = (
       "You are a medical MCQ expert.\n\n"
       "Here is a forwarded MCQ:\n\n" + text + "\n\n"
       "Task:\n"
       "1. Slightly rephrase the question stem (keep same meaning)\n"
       "2. Keep the same options\n"
       "3. Identify the correct answer\n"
       "4. Add a detailed explanation\n\n"
       "Return ONLY JSON array:\n"
       "[\n"
       "  {\n"
       '    "question": "rephrased question...",\n'
       '    "options": ["A) ...", "B) ...", "C) ...", "D) ..."],\n'
       '    "answer_index": 0,\n'
       '    "explanation": "Correct: A because... B is wrong because..."\n'
       "  }\n"
       "]"
   )
   try:
       response = client.chat.completions.create(
           model="llama-3.3-70b-versatile",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.3
       )
       return extract_json_list(response.choices[0].message.content)
   except Exception as e:
       logger.error("Rephrase error: " + str(e))
       return []

async def analyze_image(image_bytes):
   try:
       encoded = base64.b64encode(image_bytes).decode()
       response = client.chat.completions.create(
           model="meta-llama/llama-4-scout-17b-16e-instruct",
           messages=[{
               "role": "user",
               "content": [
                   {
                       "type": "image_url",
                       "image_url": {"url": "data:image/jpeg;base64," + encoded}
                   },
                   {
                       "type": "text",
                       "text": (
                           "You are a medical educator.\n"
                           "Analyze this medical image carefully.\n"
                           "Generate 2 high-yield MCQs based on what you see.\n\n"
                           "Return ONLY JSON array:\n"
                           "[\n"
                           "  {\n"
                           '    "question": "Based on this image...",\n'
                           '    "options": ["A) ...", "B) ...", "C) ...", "D) ..."],\n'
                           '    "answer_index": 0,\n'
                           '    "explanation": "Correct: A because..."\n'
                           "  }\n"
                           "]"
                       )
                   }
               ]
           }]
       )
       return extract_json_list(response.choices[0].message.content)
   except Exception as e:
       logger.error("Image analysis error: " + str(e))
       return []

async def send_single_for_approval(bot, question, source, image_bytes=None):
   try:
       qid = str(int(time.time())) + "_" + str(len(pending_questions))
       pending_questions[qid] = {
           "question": question,
           "source": source,
           "image_bytes": image_bytes
       }

       correct_option = question["options"][question["answer_index"]]
       text = (
           "📋 MCQ FOR APPROVAL\n\n"
           "📚 Source: " + source + "\n\n"
           + question["question"] + "\n\n"
           + "\n".join(question["options"])
           + "\n\n✅ Correct: " + correct_option
           + "\n\n💡 Explanation:\n" + question["explanation"]
       )

       keyboard = InlineKeyboardMarkup([
           [
               InlineKeyboardButton("✅ Approve & Post", callback_data="approve_" + qid),
               InlineKeyboardButton("❌ Reject", callback_data="reject_" + qid)
           ],
           [
               InlineKeyboardButton("🔄 Regenerate", callback_data="regen_" + qid)
           ]
       ])

       if len(text) > 4000:
           text = text[:4000] + "...[truncated]"

       if image_bytes:
           await bot.send_photo(
               chat_id=ADMIN_ID,
               photo=io.BytesIO(image_bytes),
               caption="🖼 Image for this MCQ"
           )

       await bot.send_message(
           chat_id=ADMIN_ID,
           text=text,
           reply_markup=keyboard
       )
       logger.info("Sent for approval: " + source)
   except Exception as e:
       logger.error("Send for approval error: " + str(e))

async def post_single_to_group(bot, question, image_bytes=None):
   try:
       if image_bytes:
           await bot.send_photo(
               chat_id=MEDICINE_GROUP_ID,
               photo=io.BytesIO(image_bytes),
               caption="🔍 Study this image carefully before answering!"
           )
           await asyncio.sleep(1)

       text_msg = question["question"] + "\n\n" + "\n".join(question["options"])
       await bot.send_message(
           chat_id=MEDICINE_GROUP_ID,
           text=text_msg
       )
       await asyncio.sleep(1)

       clean_options = []
       for opt in question["options"]:
           if len(opt) > 2 and opt[1] == ")":
               clean_options.append(opt[3:].strip())
           else:
               clean_options.append(opt)

       await bot.send_poll(
           chat_id=MEDICINE_GROUP_ID,
           question=question["question"][:300],
           options=clean_options,
           type="quiz",
           correct_option_id=int(question["answer_index"]),
           is_anonymous=True
       )
       await asyncio.sleep(2)

       explanation_escaped = escape_md(question["explanation"])
       spoiler = "💡 Explanation:\n\n||" + explanation_escaped + "||"
       await bot.send_message(
           chat_id=MEDICINE_GROUP_ID,
           text=spoiler,
           parse_mode="MarkdownV2"
       )
       logger.info("Posted to medicine group")
   except Exception as e:
       logger.error("Post to group error: " + str(e))

async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
   try:
       logger.info("Running scheduled job...")
       topic = await generate_topic()
       logger.info("Topic: " + topic)
       questions = await generate_questions_from_content(topic, count=2)
       if not questions:
           logger.error("Failed to generate questions")
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "Auto: " + topic)
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("Scheduled job error: " + str(e))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
   query = update.callback_query
   await query.answer()
   data = query.data

   if data.startswith("approve_"):
       qid = data.replace("approve_", "")
       item = pending_questions.get(qid)
       if item:
           await post_single_to_group(
               context.bot,
               item["question"],
               item.get("image_bytes")
           )
           pending_questions.pop(qid, None)
           await query.edit_message_text("✅ Posted to medicine group!")
       else:
           await query.edit_message_text("❌ Question expired.")

   elif data.startswith("reject_"):
       qid = data.replace("reject_", "")
       pending_questions.pop(qid, None)
       await query.edit_message_text("❌ Rejected.")

   elif data.startswith("regen_"):
       qid = data.replace("regen_", "")
       pending_questions.pop(qid, None)
       await query.edit_message_text("🔄 Regenerating...")
       topic = await generate_topic()
       questions = await generate_questions_from_content(topic, count=1)
       if questions:
           await send_single_for_approval(context.bot, questions[0], "Regenerated: " + topic)
       else:
           await context.bot.send_message(
               chat_id=ADMIN_ID,
               text="❌ Failed to regenerate. Try /postnow"
           )

async def handle_forwarded_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   try:
       poll = update.message.poll
       if not poll:
           return
       question = poll.question
       options = [opt.text for opt in poll.options]
       text = (
           question + "\n\n"
           + "\n".join([chr(65 + i) + ") " + opt for i, opt in enumerate(options)])
       )
       await update.message.reply_text("💬 Forwarded poll detected! Processing...")
       questions = await rephrase_forwarded_mcq(text)
       if not questions:
           await update.message.reply_text("❌ Could not process poll.")
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "Forwarded Poll")
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("Forwarded poll error: " + str(e))
       await update.message.reply_text("❌ Failed to process poll.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return

   text = update.message.text.strip()

   if text.startswith("http://") or text.startswith("https://"):
       youtube_id = extract_youtube_id(text)

       if youtube_id:
           await update.message.reply_text("🎥 YouTube link! Fetching transcript...")
           transcript = await get_youtube_transcript(youtube_id)
           if not transcript:
               await update.message.reply_text("❌ No transcript. Trying page content...")
               transcript = await fetch_url_content(text)
           if not transcript:
               await update.message.reply_text("❌ Could not extract content.")
               return
           await update.message.reply_text("⏳ Generating MCQs from video...")
           questions = await generate_questions_from_content(transcript, count=2)
           source = "YouTube: " + text[:50]
       else:
           await update.message.reply_text("🔗 Article URL! Fetching content...")
           content = await fetch_url_content(text)
           if not content:
               await update.message.reply_text("❌ Could not fetch content.")
               return
           await update.message.reply_text("⏳ Generating MCQs from article...")
           questions = await generate_questions_from_content(content, count=2)
           source = "Article: " + text[:50]

       if not questions:
           await update.message.reply_text("❌ Failed to generate MCQs.")
           return

       for q in questions:
           await send_single_for_approval(context.bot, q, source)
           await asyncio.sleep(1)

   else:
       await update.message.reply_text("💬 Forwarded MCQ text detected! Processing...")
       questions = await rephrase_forwarded_mcq(text)
       if not questions:
           await update.message.reply_text("❌ Could not process MCQ.")
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "Forwarded MCQ")
           await asyncio.sleep(1)

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
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
       await update.message.reply_text("⏳ Generating MCQs from PDF...")
       questions = await generate_questions_from_content(text, count=2)
       if not questions:
           await update.message.reply_text("❌ Failed to generate MCQs.")
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "PDF Upload")
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("PDF error: " + str(e))
       await update.message.reply_text("❌ PDF processing failed.")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   try:
       await update.message.reply_text("🖼 Image received! Analyzing...")
       if update.message.photo:
           file = await update.message.photo[-1].get_file()
       elif update.message.document:
           file = await update.message.document.get_file()
       else:
           return
       file_bytes = bytes(await file.download_as_bytearray())
       questions = await analyze_image(file_bytes)
       if not questions:
           await update.message.reply_text("❌ Could not generate MCQs from image.")
           return
       await update.message.reply_text("⏳ Sending for approval...")
       for q in questions:
           await send_single_for_approval(context.bot, q, "Image Upload", image_bytes=file_bytes)
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("Image error: " + str(e))
       await update.message.reply_text("❌ Image processing failed.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text(
       "✅ MedHacker Bot Running!\n\n"
       "Send me:\n"
       "📝 Forwarded MCQ text\n"
       "📊 Forwarded MCQ poll\n"
       "📄 PDF\n"
       "🖼 Image\n"
       "🔗 Article URL\n"
       "🎥 YouTube URL\n\n"
       "Auto: 2 Harrison MCQs every 15 min\n"
       "All go through your approval!\n\n"
       "Commands:\n"
       "/postnow - Generate immediately\n"
       "/status - Check bot status"
   )

async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text("⏳ Generating Harrison MCQs... please wait")
   await scheduled_job(context)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text(
       "✅ Bot is running\n"
       "📊 Pending approvals: " + str(len(pending_questions)) + "\n"
       "📚 Topics used: " + str(len(used_topics))
   )

async def error_handler(update, context):
   logger.error("Update error: " + str(context.error))

def main():
   logger.info("Starting MedHacker Bot...")
   app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

   app.add_handler(CommandHandler("start", start))
   app.add_handler(CommandHandler("postnow", post_now))
   app.add_handler(CommandHandler("status", status))
   app.add_handler(CallbackQueryHandler(handle_callback))
   app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
   app.add_handler(MessageHandler(filters.PHOTO, handle_image))
   app.add_handler(MessageHandler(filters.POLL, handle_forwarded_poll))

   
  
   app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
   app.add_error_handler(error_handler)

   app.job_queue.run_repeating(
       scheduled_job,
       interval=INTERVAL,
       first=10
   )

   logger.info("Bot started!")
   app.run_polling(
       allowed_updates=["message", "poll", "poll_answer", "callback_query"],
       drop_pending_updates=True
   )

if __name__ == "__main__":
   main()
