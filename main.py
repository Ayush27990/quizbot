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
from pptx import Presentation
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
PENDING_FILE = "pending_questions.json"

if not TELEGRAM_TOKEN:
   raise ValueError("TELEGRAM_TOKEN missing")
if not GROQ_API_KEY:
   raise ValueError("GROQ_API_KEY missing")
if not MEDICINE_GROUP_ID:
   raise ValueError("MEDICINE_GROUP_ID missing")

client = Groq(api_key=GROQ_API_KEY)
used_topics = []

def safe_groq_call(model, messages, temperature=0.3, retries=2):
   """Call Groq with retry/backoff for transient rate-limit or server errors."""
   delay = 2
   last_err = None
   for attempt in range(retries + 1):
       try:
           return client.chat.completions.create(
               model=model,
               messages=messages,
               temperature=temperature
           )
       except Exception as e:
           last_err = e
           msg = str(e).lower()
           if "rate" in msg or "429" in msg or "503" in msg or "timeout" in msg:
               logger.error("Groq transient error (attempt " + str(attempt + 1) + "): " + str(e))
               time.sleep(delay)
               delay *= 2
               continue
           else:
               logger.error("Groq error: " + str(e))
               raise
   raise last_err

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


PATHOLOGY_TOPICS = [
   "Cell injury reversible irreversible",
   "Apoptosis pathways intrinsic extrinsic",
   "Necrosis coagulative liquefactive caseous fat",
   "Acute inflammation mediators",
   "Chronic inflammation granulomatous diseases",
   "Amyloidosis classification diagnosis",
   "Shock pathology",
   "Neoplasia hallmarks of cancer",
   "Tumor suppressor genes p53 Rb",
   "Acute promyelocytic leukemia",
   "Acute myeloid leukemia",
   "Acute lymphoblastic leukemia",
   "Chronic myeloid leukemia",
   "Hodgkin lymphoma",
   "Non Hodgkin lymphoma",
   "Multiple myeloma",
   "Minimal change disease",
   "Membranous nephropathy",
   "Membranoproliferative glomerulonephritis",
   "Diabetic nephropathy",
   "Barrett esophagus",
   "Crohn disease pathology",
   "Ulcerative colitis pathology"
]

ALL_TOPICS = HARRISON_TOPICS + PATHOLOGY_TOPICS


def load_pending():
   try:
       with open(PENDING_FILE, "r") as f:
           data = json.load(f)
           for qid, item in data.items():
               if item.get("image_b64"):
                   item["image_bytes"] = base64.b64decode(item["image_b64"])
               else:
                   item["image_bytes"] = None
           return data
   except:
       return {}

def save_pending(data):
   try:
       saveable = {}
       for qid, item in data.items():
           saveable[qid] = {
               "question": item["question"],
               "source": item["source"],
               "image_b64": base64.b64encode(item["image_bytes"]).decode() if item.get("image_bytes") else None
           }
       with open(PENDING_FILE, "w") as f:
           json.dump(saveable, f)
   except Exception as e:
       logger.error("Save pending error: " + str(e))

pending_questions = load_pending()

def clean_forwarded_text(text):
   # Remove Telegram spoiler tags
   text = text.replace("||", "")
   # Remove common emoji that break JSON parsing
   text = text.replace("â", "").replace("â", "").replace("ð¡", "").replace("ð", "")
   text = text.replace("Answer:", "Answer:").strip()
   # Collapse multiple spaces/newlines
   text = re.sub(r"\n{3,}", "\n\n", text)
   text = re.sub(r"[ \t]{2,}", " ", text)
   return text.strip()

def escape_md(text):
   for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">",
              "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
       text = text.replace(ch, "\\" + ch)
   return text

def extract_json_list(text):
   raw = text
   try:
       # Strip markdown code fences if present
       text = re.sub(r"```json|```", "", text).strip()
       match = re.search(r"\[.*\]", text, re.DOTALL)
       if not match:
           logger.error("JSON parse error: no array found. Raw (first 500 chars): " + raw[:500])
           return []
       candidate = match.group()
       try:
           result = json.loads(candidate)
           if isinstance(result, list):
               return result
           return []
       except Exception:
           # Try common auto-fixes for malformed JSON from the model
           fixed = candidate
           # remove trailing commas before ] or }
           fixed = re.sub(r",\s*([\]}])", r"\1", fixed)
           # remove control characters
           fixed = re.sub(r"[\x00-\x1f]+", " ", fixed)
           try:
               result = json.loads(fixed)
               if isinstance(result, list):
                   return result
           except Exception as e2:
               logger.error("JSON parse error after fix attempt: " + str(e2) + " | Raw (first 500 chars): " + raw[:500])
           return []
   except Exception as e:
       logger.error("JSON parse error: " + str(e) + " | Raw (first 500 chars): " + raw[:500])
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
       return random.choice(ALL_TOPICS)
   except Exception as e:
       logger.error("Topic generation error: " + str(e))
       return random.choice(ALL_TOPICS)

async def generate_questions_from_content(content, count=2):
   def build_prompt(c):
       return (
           "You are a Harrison Internal Medicine expert examiner.\n\n"
           "Generate EXACTLY " + str(count) + " high-yield clinical MCQs based on:\n\n"
           + c + "\n\n"
           "Rules:\n"
           "- Clinical vignette with patient scenario\n"
           "- 4 options, one definitively correct\n"
           "- Detailed explanation\n"
           "- Explain why each wrong option is incorrect\n"
           "- NEET PG / INICET / USMLE level\n- Include laboratory values whenever possible\n- Avoid direct recall questions\n- Focus on pathology correlations\n\n"
           "Return ONLY a raw JSON array with no markdown, no backticks, no extra commentary:\n"
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
       response = safe_groq_call(
           "llama-3.3-70b-versatile",
           [{"role": "user", "content": build_prompt(content)}],
           temperature=0.3
       )
       raw = response.choices[0].message.content
       result = extract_json_list(raw)
       if result:
           return result

       # --- Retry once with shortened/cleaned content if first attempt failed ---
       logger.info("First attempt failed, retrying with trimmed content...")
       trimmed = re.sub(r"[\x00-\x1f]+", " ", content)
       trimmed = re.sub(r"\s{2,}", " ", trimmed).strip()[:2000]
       response2 = safe_groq_call(
           "llama-3.3-70b-versatile",
           [{"role": "user", "content": build_prompt(trimmed)}],
           temperature=0.3
       )
       raw2 = response2.choices[0].message.content
       return extract_json_list(raw2)
   except Exception as e:
       logger.error("Question generation error: " + str(e))
       return []

async def rephrase_forwarded_mcq(text):
   prompt = (
       "You are a medical MCQ expert.\n\n"
       "Here is a forwarded MCQ:\n\n" + text + "\n\n"
       "Task:\n"
       "1. Slightly rephrase the question stem (keep same meaning)\n"
       "2. Keep the same options A B C D\n"
       "3. Identify the correct answer index (0 for A, 1 for B, 2 for C, 3 for D)\n"
       "4. Add a detailed explanation\n\n"
       "Return ONLY a raw JSON array with no markdown, no backticks, no extra text:\n"
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
       response = safe_groq_call(
           "llama-3.3-70b-versatile",
           [{"role": "user", "content": prompt}],
           temperature=0.3
       )
       raw = response.choices[0].message.content
       logger.info("Rephrase raw response: " + raw[:300])
       return extract_json_list(raw)
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
                           "Return ONLY a raw JSON array with no markdown, no backticks:\n"
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
       qid = str(int(time.time())) + "_" + str(random.randint(1000, 9999))
       pending_questions[qid] = {
           "question": question,
           "source": source,
           "image_bytes": image_bytes
       }
       save_pending(pending_questions)

       correct_option = question["options"][question["answer_index"]]
       text = (
           "ð MCQ FOR APPROVAL\n\n"
           "ð Source: " + source + "\n\n"
           + question["question"] + "\n\n"
           + "\n".join(question["options"])
           + "\n\nâ Correct: " + correct_option
           + "\n\nð¡ Explanation:\n" + question["explanation"]
       )

       keyboard = InlineKeyboardMarkup([
           [
               InlineKeyboardButton("â Approve & Post", callback_data="approve_" + qid),
               InlineKeyboardButton("â Reject", callback_data="reject_" + qid)
           ],
           [
               InlineKeyboardButton("ð Regenerate", callback_data="regen_" + qid)
           ]
       ])

       if len(text) > 4000:
           text = text[:4000] + "...[truncated]"

       if image_bytes:
           await bot.send_photo(
               chat_id=ADMIN_ID,
               photo=io.BytesIO(image_bytes),
               caption="ð¼ Image for this MCQ"
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
               caption="ð Study this image carefully before answering!"
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
       spoiler = "ð¡ Explanation:\n\n||" + explanation_escaped + "||"
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
       logger.info("Approve clicked. qid=" + qid + ", found=" + str(item is not None))
       if item:
           await post_single_to_group(
               context.bot,
               item["question"],
               item.get("image_bytes")
           )
           pending_questions.pop(qid, None)
           save_pending(pending_questions)
           await query.edit_message_text("â Posted to medicine group!")
       else:
           await query.edit_message_text("â Question expired. Use /postnow to generate new ones.")

   elif data.startswith("reject_"):
       qid = data.replace("reject_", "")
       pending_questions.pop(qid, None)
       save_pending(pending_questions)
       await query.edit_message_text("â Rejected.")

   elif data.startswith("regen_"):
       qid = data.replace("regen_", "")
       pending_questions.pop(qid, None)
       save_pending(pending_questions)
       await query.edit_message_text("ð Regenerating...")
       topic = await generate_topic()
       questions = await generate_questions_from_content(topic, count=1)
       if questions:
           await send_single_for_approval(context.bot, questions[0], "Regenerated: " + topic)
       else:
           await context.bot.send_message(
               chat_id=ADMIN_ID,
               text="â Failed to regenerate. Try /postnow"
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

       # --- FIX: strip hashtags and clean text before building MCQ ---
       question = re.sub(r"#\w+", "", question).strip()
       options = [re.sub(r"#\w+", "", opt).strip() for opt in options]

       text = (
           question + "\n\n"
           + "\n".join([chr(65 + i) + ") " + opt for i, opt in enumerate(options)])
       )
       text = clean_forwarded_text(text)
       logger.info("Cleaned poll text: " + text[:300])

       await update.message.reply_text("ð¬ Forwarded poll detected! Processing...")
       questions = await rephrase_forwarded_mcq(text)

       # --- FIX: retry once if first attempt returns nothing ---
       if not questions:
           logger.info("Poll rephrase failed, retrying once...")
           await asyncio.sleep(1)
           questions = await rephrase_forwarded_mcq(text)

       if not questions:
           await update.message.reply_text(
               "â Could not process poll.\n\n"
               "The AI failed to return valid MCQ data. Try /postnow for a fresh question instead."
           )
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "Forwarded Poll")
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("Forwarded poll error: " + str(e))
       await update.message.reply_text("â Failed to process poll.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return

   text = update.message.text.strip()

   if text.startswith("/debug"):
       await update.message.reply_text("ð Raw text received:\n\n" + repr(text[:500]))
       return

   if text.startswith("http://") or text.startswith("https://"):
       youtube_id = extract_youtube_id(text)

       if youtube_id:
           await update.message.reply_text("ð¥ YouTube link! Fetching transcript...")
           transcript = await get_youtube_transcript(youtube_id)
           if not transcript:
               await update.message.reply_text("â No transcript. Trying page content...")
               transcript = await fetch_url_content(text)
           if not transcript:
               await update.message.reply_text("â Could not extract content.")
               return
           await update.message.reply_text("â³ Generating MCQs from video...")
           questions = await generate_questions_from_content(transcript, count=2)
           source = "YouTube: " + text[:50]
       else:
           await update.message.reply_text("ð Article URL! Fetching content...")
           content = await fetch_url_content(text)
           if not content:
               await update.message.reply_text("â Could not fetch content.")
               return
           await update.message.reply_text("â³ Generating MCQs from article...")
           questions = await generate_questions_from_content(content, count=2)
           source = "Article: " + text[:50]

       if not questions:
           await update.message.reply_text("â Failed to generate MCQs.")
           return

       for q in questions:
           await send_single_for_approval(context.bot, q, source)
           await asyncio.sleep(1)

   else:
       await update.message.reply_text("ð¬ Forwarded MCQ text detected! Processing...")

       # --- FIX: Clean spoiler tags and emojis before sending to Groq ---
       cleaned = clean_forwarded_text(text)
       logger.info("Cleaned MCQ text: " + cleaned[:300])

       questions = await rephrase_forwarded_mcq(cleaned)
       if not questions:
           logger.info("MCQ rephrase failed, retrying once...")
           await asyncio.sleep(1)
           questions = await rephrase_forwarded_mcq(cleaned)
       if not questions:
           await update.message.reply_text(
               "â Could not process MCQ.\n\n"
               "Tip: Make sure text has a question, 4 options (A/B/C/D), and an answer."
           )
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "Forwarded MCQ")
           await asyncio.sleep(1)

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   try:
       await update.message.reply_text("ð PDF received. Extracting text...")
       file = await update.message.document.get_file()
       file_bytes = await file.download_as_bytearray()
       pdf_reader = PyPDF2.PdfReader(io.BytesIO(bytes(file_bytes)))
       text = ""
       for page in pdf_reader.pages[:15]:
           extracted = page.extract_text()
           if extracted:
               text += extracted + "\n"
       if not text.strip():
           await update.message.reply_text("â Could not extract text.")
           return
       text = text[:6000]
       await update.message.reply_text("â³ Generating MCQs from PDF...")
       questions = await generate_questions_from_content(text, count=5)
       if not questions:
           questions = await generate_questions_from_content(text, count=2)
       if not questions:
           await update.message.reply_text("â Failed to generate MCQs.")
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "PDF Upload")
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("PDF error: " + str(e))
       await update.message.reply_text("â PDF processing failed.")

def get_slide_text(slide):
   parts = []
   for shape in slide.shapes:
       if hasattr(shape, "has_text_frame") and shape.has_text_frame:
           t = shape.text_frame.text
           if t and t.strip():
               parts.append(t)
       elif hasattr(shape, "text") and shape.text:
           parts.append(shape.text)
   combined = "\n".join(parts)
   # vertical-tab (\x0b) is used by PowerPoint for soft line breaks between options
   combined = combined.replace("\x0b", "\n")
   combined = re.sub(r"[\x00-\x09\x0c-\x1f]+", " ", combined)
   combined = re.sub(r"[ \t]{2,}", " ", combined)
   return combined.strip()

def parse_pptx_quiz_blocks(prs, max_blocks=5):
   """Detect Question -> Answer -> (Explanation) slide patterns common in
   pre-made MCQ decks, and return cleaned text blocks ready for rephrasing."""
   slides_text = [get_slide_text(s) for s in prs.slides]
   blocks = []
   i = 0
   while i < len(slides_text) and len(blocks) < max_blocks:
       t = slides_text[i]
       option_lines = re.findall(r"(?m)^[A-D][\.\)]\s*.+", t)
       if len(option_lines) >= 3:
           block = t
           consumed = 1
           if i + 1 < len(slides_text) and re.search(r"answer", slides_text[i + 1], re.I):
               block += "\n\n" + slides_text[i + 1]
               consumed = 2
               if i + 2 < len(slides_text) and re.search(r"explanation", slides_text[i + 2], re.I):
                   block += "\n\n" + slides_text[i + 2]
                   consumed = 3
           blocks.append(block)
           i += consumed
       else:
           i += 1
   return blocks, len(slides_text)

async def handle_pptx(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   try:
       await update.message.reply_text("ð PPTX received. Extracting text...")
       file = await update.message.document.get_file()
       file_bytes = await file.download_as_bytearray()

       prs = Presentation(io.BytesIO(bytes(file_bytes)))

       # --- Try to detect pre-made MCQs (Question -> Answer -> Explanation slides) ---
       blocks, total_slides = parse_pptx_quiz_blocks(prs, max_blocks=5)

       if blocks:
           await update.message.reply_text(
               "â Found " + str(len(blocks)) + " ready-made MCQ(s) in this PPTX (out of "
               + str(total_slides) + " slides). Processing them now..."
           )
           sent_any = False
           for block in blocks:
               cleaned = clean_forwarded_text(block)
               questions = await rephrase_forwarded_mcq(cleaned)
               if not questions:
                   await asyncio.sleep(1)
                   questions = await rephrase_forwarded_mcq(cleaned)
               if questions:
                   for q in questions:
                       await send_single_for_approval(context.bot, q, "PPTX Upload")
                       await asyncio.sleep(1)
                   sent_any = True
               else:
                   logger.error("Could not process one MCQ block from PPTX")
               await asyncio.sleep(1)
           if not sent_any:
               await update.message.reply_text("â Found MCQs in the PPTX but the AI failed to process any of them.")
           return

       # --- Fallback: treat as lecture slides, generate new MCQs from content ---
       text = "\n\n".join(get_slide_text(s) for s in prs.slides)
       text = re.sub(r"[â¢âªâºâ¤âââ¦]", "-", text)
       text = re.sub(r"\n{3,}", "\n\n", text).strip()

       if not text:
           await update.message.reply_text("â Could not extract any text from this PPTX. It may contain only images.")
           return

       text = text[:6000]
       await update.message.reply_text("â³ No ready-made MCQs detected â generating new MCQs from slide content...")
       questions = await generate_questions_from_content(text, count=5)
       if not questions:
           questions = await generate_questions_from_content(text, count=2)
       if not questions:
           await update.message.reply_text("â Failed to generate MCQs from this PPTX content.")
           return
       for q in questions:
           await send_single_for_approval(context.bot, q, "PPTX Upload")
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("PPTX error: " + str(e))
       await update.message.reply_text("â PPTX processing failed: " + str(e))

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   try:
       await update.message.reply_text("ð¼ Image received! Analyzing...")
       if update.message.photo:
           file = await update.message.photo[-1].get_file()
       elif update.message.document:
           file = await update.message.document.get_file()
       else:
           return
       file_bytes = bytes(await file.download_as_bytearray())
       questions = await analyze_image(file_bytes)
       if not questions:
           await update.message.reply_text("â Could not generate MCQs from image.")
           return
       await update.message.reply_text("â³ Sending for approval...")
       for q in questions:
           await send_single_for_approval(context.bot, q, "Image Upload", image_bytes=file_bytes)
           await asyncio.sleep(1)
   except Exception as e:
       logger.error("Image error: " + str(e))
       await update.message.reply_text("â Image processing failed.")

async def test_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   try:
       await context.bot.send_message(
           chat_id=MEDICINE_GROUP_ID,
           text="ð§ª Test message from bot!"
       )
       await update.message.reply_text("â Success! Bot can post to group.")
   except Exception as e:
       await update.message.reply_text("â Error: " + str(e))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text(
       "â QuizMasterBot Running!\n\n"
       "Send me:\n"
       "ð Forwarded MCQ text\n"
       "ð Forwarded MCQ poll\n"
       "ð PDF\n"
       "ð PPTX (PowerPoint slides)\n"
       "ð¼ Image\n"
       "ð Article URL\n"
       "ð¥ YouTube URL\n\n"
       "Auto: 2 Harrison MCQs every 15 min\n"
       "All go through your approval!\n\n"
       "Commands:\n"
       "/postnow - Generate immediately\n"
       "/testgroup - Test group posting\n"
       "/status - Check bot status\n"
       "/debug - Echo raw text for troubleshooting"
   )

async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text("â³ Generating Harrison MCQs... please wait")
   await scheduled_job(context)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   await update.message.reply_text(
       "â Bot is running\n"
       "ð Pending approvals: " + str(len(pending_questions)) + "\n"
       "ð Topics used: " + str(len(used_topics))
   )


async def pathology(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   topic = random.choice(PATHOLOGY_TOPICS)
   await update.message.reply_text("ð§¬ Generating pathology MCQs...")
   questions = await generate_questions_from_content(topic, count=5)
   for q in questions:
       await send_single_for_approval(context.bot, q, "Pathology: " + topic)

async def hardmcq(update: Update, context: ContextTypes.DEFAULT_TYPE):
   if update.effective_user.id != ADMIN_ID:
       return
   topic = await generate_topic()
   await update.message.reply_text("ð¥ Generating hard INICET MCQs...")
   prompt = "Generate difficult INICET/USMLE style MCQs with labs and clinical reasoning. Topic: " + topic
   questions = await generate_questions_from_content(prompt, count=5)
   for q in questions:
       await send_single_for_approval(context.bot, q, "Hard MCQ: " + topic)


async def error_handler(update, context):
   logger.error("Update error: " + str(context.error))

def main():
   logger.info("Starting QuizMasterBot...")
   app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

   app.add_handler(CommandHandler("start", start))
   app.add_handler(CommandHandler("postnow", post_now))
   app.add_handler(CommandHandler("status", status))
   app.add_handler(CommandHandler("pathology", pathology))
   app.add_handler(CommandHandler("hardmcq", hardmcq))
   app.add_handler(CommandHandler("testgroup", test_group))
   app.add_handler(CallbackQueryHandler(handle_callback))
   app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
   app.add_handler(MessageHandler(filters.Document.FileExtension("pptx"), handle_pptx))
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
