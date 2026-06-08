import os
import json
import re
import time
import logging
import asyncio
import io

import PyPDF2
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

# ======================
# LOGGING
# ======================
logging.basicConfig(
   level=logging.INFO,
   format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ======================
# CONFIG
# ======================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = 723919716
INTERVAL = 900

if not TELEGRAM_TOKEN:
   raise ValueError("TELEGRAM_TOKEN missing")
if not GROQ_API_KEY:
   raise ValueError("GROQ_API_KEY missing")
if not CHANNEL_ID:
   raise ValueError("CHANNEL_ID missing")

client = Groq(api_key=GROQ_API_KEY)
pending_questions = {}
used_topics = []

# ======================
# HELPERS
# ======================
def escape_md(text):
   for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">",
              "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
       text = text.replace(ch, f"\\{ch}")
   return text

def extract_json(text):
   try:
       match = re.search(r"\{.*\}", text, re.DOTALL)
       if match:
           return json.loads(match.group())
       match = re.search(r"\[.*\]", text, re.DOTALL)
       if match:
           result = json.loads(match.group())
           if isinstance(result, list) and len(result) > 0:
               return result[0]
       return None
   except Exception as e:
       logger.error(f"JSON parse error: {e}")
       return None

# ======================
# GENERATE TOPIC
# ======================
async def generate_topic():
   used = ", ".join(used_topics[-20:]) if used_topics else "none"
   prompt = (
       "You are a NEET PG / FMGE / USMLE medical expert.\n\n"
       "Suggest ONE specific high-yield topic for a biochemistry or pharmacology MCQ.\n\n"
       "Already used topics (avoid repeating): " + used + "\n\n"
       "Requirements:\n"
       "- Must be specific\n"
       "- Must be clinically relevant\n"
       "- Alternate between biochemistry and pharmacology\n"
       "- Focus on NEET PG high yield topics\n\n"
       'Return ONLY JSON: {"topic": "Warfarin mechanism and vitamin K cycle"}'
   )
   try:
       response = client.chat.completions.create(
           model="llama-3.3-70b-versatile",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.9
       )
       result = extract_json(response.choices[0].message.content)
       topic = result.get("topic") if result else "Pharmacology high yield topic"
       used_topics.append(topic)
       if len(used_topics) > 100:
           used_topics.pop(0)
       return topic
   except Exception as e:
       logger.error("Topic generation error: " + str(e))
       return "Biochemistry high yield topic"

# ======================
# GENERATE MCQ
# ======================
async def generate_mcq(content):
   prompt = (
       "You are a NEET PG / USMLE / FMGE expert examiner.\n\n"
       "Generate ONE high-yield clinical MCQ based on: " + content + "\n\n"
       "Rules:\n"
       "- Clinical vignette style with patient scenario\n"
       "- 4 options, one definitively correct\n"
       "- No ambiguous or trick questions\n"
       "- Explanation must cite mechanism clearly\n"
       "- Explain why each wrong option is incorrect\n\n"
       "Return ONLY this JSON:\n"
       '{"question": "A patient presents with...", '
       '"options": ["A) ...", "B) ...", "C) ...", "D) ..."], '
       '"answer_index": 0, '
       '"explanation": "Correct: A because... B is wrong because..."}'
   )
   try:
       response = client.chat.completions.create(
           model="llama-3.3-70b-versatile",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.3
       )
       return extract_json(response.choices[0].message.content)
   except Exception as e:
       logger.error("MCQ generation error: " + str(e))
       return None

# ======================
# VALIDATE MCQ
# ======================
async def validate_mcq(mcq):
   prompt = (
       "You are a medical education quality reviewer.\n\n"
       "Review this MCQ:\n"
       "Question: " + mcq["question"] + "\n"
       "Options: " + str(mcq["options"]) + "\n"
       "Answer index: " + str(mcq["answer_index"]) + "\n"
       "Explanation: " + mcq["explanation"] + "\n\n"
       "Check accuracy, explanation quality, and NEET PG relevance.\n\n"
       "Return ONLY JSON:\n"
       '{"score": 8, "is_accurate": true, "feedback": "Good question"}'
   )
   try:
       response = client.chat.completions.create(
           model="llama-3.3-70b-versatile",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.1
       )
       return extract_json(response.choices[0].message.content)
   except Exception as e:
       logger.error("Validation error: " + str(e))
       return None

# ======================
# SEND FOR APPROVAL
# ======================
async def send_for_approval(bot, mcq, source):
   try:
       qid = str(int(time.time()))
       pending_questions[qid] = {"mcq": mcq, "source": source}
       correct_option = mcq["options"][mcq["answer_index"]]
       text = (
           "📋 NEW MCQ FOR APPROVAL\n\n"
           "📚 Source: " + source + "\n\n"
           + mcq["question"] + "\n\n"
           + "\n".join(mcq["options"])
           + "\n\n✅ Correct: " + correct_option
           + "\n\n💡 Explanation:\n" + mcq["explanation"]
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
       await bot.send_message(
           chat_id=ADMIN_ID,
           text=text,
           reply_markup=keyboard
       )
       logger.info("MCQ sent for approval: " + source)
   except Exception as e:
       logger.error("Send for approval error: " + str(e))

# ======================
# POST TO CHANNEL
# ======================
async def post_to_channel(bot, mcq):
   try:
       text_msg = (
           "📚 DAILY MCQ\n\n"
           + mcq["question"] + "\n\n"
           + "\n".join(mcq["options"])
       )
       await bot.send_message(
           chat_id=CHANNEL_ID,
           text=text_msg
       )
       await asyncio.sleep(2)

       clean_options = []
       for opt in mcq["options"]:
           if len(opt) > 2 and opt[1] == ")":
               clean_options.append(opt[3:].strip())
           else:
               clean_options.append(opt)

       await bot.send_poll(
           chat_id=CHANNEL_ID,
           question=mcq["question"][:300],
           options=clean_options,
           type="quiz",
           correct_option_id=int(mcq["answer_index"]),
           is_anonymous=True
       )
       await asyncio.sleep(2)

       explanation_escaped = escape_md(mcq["explanation"])
       spoiler = "💡 Explanation:\n\n||" + explanation_escaped + "||"
       await bot.send_message(
           chat_id=CHANNEL_ID,
           text=spoiler,
           parse_mode="MarkdownV2"
       )
       logger.info("Successfully posted to channel")
   except Exception as e:
       logger.error("Post to channel error: " + str(e))

# ======================
# SCHEDULED JOB
# ======================
async def scheduled_job(context: ContextTypes.DEFAULT_TYPE):
   try:
       logger.info("Running scheduled job...")
       topic = await generate_topic()
       logger.info("Generated topic: " + topic)

       mcq = await generate_mcq(topic)
       if not mcq:
           logger.error("Failed to generate MCQ")
           return

       review = await validate_mcq(mcq)
       score = review.get("score", 0) if review else 0
       logger.info("MCQ score: " + str(score))

       if score >= 7:
           await send_for_approval(context.bot, mcq, "Auto: " + topic)
       else:
           logger.info("Low score, regenerating...")
           topic2 = await generate_topic()
           mcq2 = await generate_mcq(topic2)
           if mcq2:
               await send_for_approval(context.bot, mcq2, "Auto retry: " + topic2)
   except Exception as e:
       logger.error("Scheduled job error: " + str(e))

# ======================
# CALLBACK HANDLER
# ======================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
   query = update.callback_query
   await query.answer()
   data = query.data

   if data.startswith("approve_"):
       qid = data.replace("approve_", "")
       item = pending_questions.get(qid)
       if item:
           await post_to_channel(context.bot, item["mcq"])
           pending_questions.pop(qid, None)
           await query.edit_message_text("✅ Posted to channel!")
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
       mcq = await generate_mcq(topic)
       if mcq:
           await send_for_approval(context.bot, mcq, "Regenerated: " + topic)
       else:
           await context.bot.send_message(
               chat_id=ADMIN_ID,
               text="❌ Failed to regenerate. Try /postnow"
           )

# ======================
# PDF HANDLER
# ======================
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
       await update.message.reply_text("⏳ Generating MCQ from PDF...")
       mcq = await generate_mcq(text)
       if not mcq:
           await update.message.reply_text("❌ Failed to generate MCQ.")
           return
       review = await validate_mcq(mcq)
       score = review.get("score", 0) if review else 0
       if score >= 7:
           await send_for_approval(context.bot, mcq, "PDF Upload")
       else:
           await update.message.reply_text("⚠️ Low quality. Retrying...")
           mcq2 = await generate_mcq(text)
           if mcq2:
               await send_for_approval(context.bot, mcq2, "PDF Upload retry")
   except Exception as e:
       logger.error("PDF error: " + str(e))
       await update.message.reply_text("❌ PDF processing failed.")

# ======================
# COMMANDS
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text(
       "✅ Pharma Quiz Bot Running!\n\n"
       "Features:\n"
       "🤖 AI generates topics automatically\n"
       "✅ AI validates quality\n"
       "👨 You approve before posting\n"
       "🔄 Regenerate if not satisfied\n"
       "📄 Send PDF to generate MCQ\n\n"
       "Commands:\n"
       "/postnow - Generate immediately\n"
       "/status - Check bot status"
   )

async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text("⏳ Generating MCQ... please wait 30-60 seconds")
   await scheduled_job(context)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text(
       "✅ Bot is running\n"
       "📊 Pending approvals: " + str(len(pending_questions)) + "\n"
       "📚 Topics used: " + str(len(used_topics))
   )

# ======================
# MAIN
# ======================
def main():
   logger.info("Starting Pharma Quiz Bot...")
   app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

   app.add_handler(CommandHandler("start", start))
   app.add_handler(CommandHandler("postnow", post_now))
   app.add_handler(CommandHandler("status", status))
   app.add_handler(CallbackQueryHandler(handle_callback))
   app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

   app.job_queue.run_repeating(
       scheduled_job,
       interval=INTERVAL,
       first=10
   )

   logger.info("Bot started! Interval: " + str(INTERVAL // 60) + " minutes")
   app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
   main()
