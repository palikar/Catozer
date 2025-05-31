import os
import json
import sys
from datetime import datetime, timedelta, time
import threading

from PIL import Image

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

import moondream

import facebook

from google import genai
from google.genai import types

from dotenv import load_dotenv

from imgurpython import ImgurClient

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, render_template, send_from_directory
import waitress

from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

AVAILABLE_DAY_TIMES = [
    10,
    14,
    18
]


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MOONDREAM_TOKEN=""

FACEBOOK_TOKEN = os.getenv("FACEBOOK_TOKEN")
IG_TOKEN = os.getenv("IG_TOKEN")
INSTAGRAM_TOKEN = os.getenv("INSTAGRAM_TOKEN")
GEMINI_TOKEN = os.getenv("GEMINI_TOKEN")
IMGUR_CLIENT_ID = os.getenv("IMGUR_CLIENT_ID")
IMGUR_CLIENT_SECRET = os.getenv("IMGUR_CLIENT_SECRET")
IMGUR_ACCESS_TOKEN= os.getenv("IMGUR_ACCESS_TOKEN")
IMGUR_REFRESH_TOKEN= os.getenv("IMGUR_REFRESH_TOKEN")
PAGE_ID = os.getenv("PAGE_ID")
IG_ID = os.getenv("IG_ID")

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = os.getenv("DB_PORT")

DOWNLOAD_DIR = "downloads"

MoondreamModel = moondream.vl(api_key=MOONDREAM_TOKEN)
GeminiClient = genai.Client(api_key=GEMINI_TOKEN)
FacebookGraph = facebook.GraphAPI(access_token=FACEBOOK_TOKEN, version="3.1")
IGGraph = facebook.GraphAPI(access_token=IG_TOKEN, version="3.1")
Imgur = ImgurClient(IMGUR_CLIENT_ID, IMGUR_CLIENT_SECRET)
Imgur.set_user_auth(IMGUR_ACCESS_TOKEN, IMGUR_REFRESH_TOKEN)

ServerApp = Flask(__name__, static_url_path='', static_folder='../web/static', template_folder='../web/templates')

DBConn = psycopg2.connect(
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASS,
    host=DB_HOST,
    port=DB_PORT
)

def put_post_in_db(caption, text, schedule_time, image_name):
    cur = DBConn.cursor()
    cur.execute("""
    INSERT INTO public.posts (caption, text, schedule_time, image_name)
    VALUES (%s, %s, %s, %s)
""", (caption, text, schedule_time, image_name))
    DBConn.commit()
    cur.close()

def get_schedules():
    cur = DBConn.cursor()
    cur.execute("SELECT schedule_time FROM posts WHERE schedule_time > NOW()")
    posts = cur.fetchall()
    posts = [p[0] for p in posts]
    DBConn.commit()
    cur.close()

    return posts

def get_all_posts():
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM posts ORDER BY schedule_time DESC")
        posts = cur.fetchall()
        DBConn.commit()

    return posts

def get_not_posted_but_scheduled():
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, text, image_name, posted_on_fb, posted_on_ig FROM posts WHERE (posted_on_fb = false OR posted_on_ig = false) AND schedule_time <= NOW();")
        posts = cur.fetchall()
        DBConn.commit()

    return posts


def mark_as_fb_posted(post_id):
    cur = DBConn.cursor()
    cur.execute("UPDATE posts SET posted_on_fb = true WHERE id = %s", (post_id, ))
    DBConn.commit()
    cur.close()
    return posts

def mark_as_ig_posted(post_id):
    cur = DBConn.cursor()
    cur.execute("UPDATE posts SET posted_on_ig = true WHERE id = %s", (post_id, ))
    DBConn.commit()
    cur.close()
    return posts


def has_free_slot_in_day(now, now_str, schedules_map):
    """
    Checks whether there is a free posting slot on a given day.

    Args:
        now (datetime): Current datetime.
        now_str (str): Date string in 'dd-mm-YYYY' format representing the day to check.
        schedules_map (dict): A map from date strings to a list of scheduled datetimes.

    Returns:
        bool: True if there is at least one free and valid (i.e., future) slot on the day.
    """
    # If the day has no scheduled posts yet, there is a free slot.
    if now_str not in schedules_map:
        return True

    # If scheduled posts exceed available slots, there is no room.
    if len(schedules_map[now_str]) >= len(AVAILABLE_DAY_TIMES):
        return False

    # Get the list of taken slot hours for the day.
    taken_slots = schedules_map[now_str]
    taken_slots_hours = [int(slot.strftime("%H")) for slot in taken_slots]

    # Check each available hour for a free slot that's also in the future.
    for available_slot_time in AVAILABLE_DAY_TIMES:
        candidate_time = datetime.combine(now.date(), time(hour=available_slot_time))
        if candidate_time < datetime.now():
            continue  # Skip past times
        if available_slot_time not in taken_slots_hours:
            return True  # Found a free slot

    return False  # No free future slots found


def get_free_slot(now, now_str, schedules_map):
    """
    Finds the next free time slot on a given day.

    Args:
        now (datetime): Base datetime (used for date).
        now_str (str): Date string in 'dd-mm-YYYY' format representing the day to check.
        schedules_map (dict): A map from date strings to a list of scheduled datetimes.

    Returns:
        datetime: The next available datetime slot for posting.
    """
    # If no posts are scheduled on that day, return the first available slot.
    if now_str not in schedules_map:
        return datetime.combine(now.date(), time(hour=AVAILABLE_DAY_TIMES[0]))

    # Extract already taken hours on that day.
    taken_slots = [int(slot.strftime("%H")) for slot in schedules_map[now_str]]

    # Find the first available hour not already taken.
    for available_slot_time in AVAILABLE_DAY_TIMES:
        if available_slot_time not in taken_slots:
            return datetime.combine(now.date(), time(hour=available_slot_time))

    # In practice, this should not happen if used after has_free_slot_in_day
    return None


def find_scheduling_time():
    """
    Finds the next available datetime to schedule a post.

    Returns:
        datetime: A datetime object representing when the next post can be scheduled.
    """
    now = datetime.now()

    # Get all existing scheduled post times.
    schedules = get_schedules()

    # Organize schedules by date string (e.g., '31-05-2025' -> [times])
    schedules_map = {}
    for sched in schedules:
        day = sched.strftime("%d-%m-%Y")
        schedules_map.setdefault(day, []).append(sched)

    now_str = now.strftime("%d-%m-%Y")

    # Advance day by day until we find a day with a free slot.
    while not has_free_slot_in_day(now, now_str, schedules_map):
        now += timedelta(days=1)
        now_str = now.strftime("%d-%m-%Y")

    # Return the actual datetime slot available.
    return get_free_slot(now, now_str, schedules_map)

def generate_photo_caption(image_path):
    image = Image.open(image_path)
    caption_response = MoondreamModel.caption(image, length="normal")
    return caption_response["caption"]

def generate_post_content(caption):
    response = GeminiClient.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(
            system_instruction="Това ще е в пост в инстаграм. Страницата за която става въпрос е Cattos. В нея публикувам снимки и историики за премеждията на котарака Марципан. Ще трябва да ми помогнеш с правенеот на съдаржание за тази страница. Аз ще ти давам описание на картинката, ти ще ми даваш забавен пост за Марципан,  който ще е за facebook и instagram. Марципан е раг-дол котка, а не сиамка, имай го предвид. Давай ми само текста на поста, коригирай породата на Марципан. Прави постовете малко по-дълги - 4-5 изречения и вкарвай кратка измислена историйка от живота на Марципан, която да е подходяща за описанието на снимкат. Отговряй на Български език."),
        contents=[caption]
    )

    text = response.text
    return text

def post_on_fb(image_url, content):
    with open(image_url, 'rb') as image:
        photo = FacebookGraph.put_photo(
            image = image,
            album_path = 'me/photos',
            published = False,
        )
    media_fbid = photo['id']
    print(f'Uploaded photo to Facebook - id: {media_fbid}')

    post_data = {
        'message': content,
        'attached_media': str([{'media_fbid': media_fbid}]),
    }
    FacebookGraph.put_object(parent_object=PAGE_ID, connection_name='feed', **post_data)

def post_on_ig(image_url, content):
    result = Imgur.upload_from_path(image_url, config=None, anon=False)
    link = result['link']

    media = IGGraph.put_object(
        parent_object=IG_ID,
        connection_name='media',
        image_url=link,
        caption=content,
    )

    creation_id = media['id']
    result = IGGraph.put_object(
        parent_object=IG_ID,
        connection_name='media_publish',
        creation_id=creation_id
    )

async def handle_telegram_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("🖼️ Image received. Processing started...")

    photo_file = await update.message.photo[-1].get_file()
    image_name = f"{photo_file.file_id}.jpg"
    file_path = os.path.join(DOWNLOAD_DIR, f"{image_name}")
    await photo_file.download_to_drive(file_path)
    print(f"Photo from telegram downloaded to {file_path}")

    post_text = None
    caption = None

    try:
        caption = generate_photo_caption(file_path)
        print(f"Caption for photo: {caption}")
        await update.message.reply_text(f"🧠 Caption: {caption}")
    except:
        pass

    try:
        if caption is not None:
            post_text = generate_post_content(caption)
            print(f"Content for post: {post_text}")
            await update.message.reply_text(f"👍 Post: {post_text}")
    except:
        pass

    post_time = find_scheduling_time()
    schedule_time = post_time.timestamp()
    print(f"Scheduling Post for '{str(post_time)}'")

    put_post_in_db(caption, post_text, post_time, image_name)

    if post_text is not None and caption is not None:
        await update.message.reply_text(f"✅ Done! FB/IG Post Scheduled for: {post_time}")
    else:
        await update.message.reply_text(f"❌ Something went wrong but will retry in some while.")

def run_telegram():
    try:
        os.makedirs(DOWNLOAD_DIR)
    except OSError:
        pass

    telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_telegram_photo))

    print('Starting Telegram Bot...')
    telegram_app.run_polling()

def upload_from_folder(folder_path):
    jpg_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.jpg')]
    for img in jpg_files:
        base_name = os.path.splitext(img)[0]
        image_path = os.path.join(folder_path, img)

        text_path = os.path.join(folder_path, base_name + "_post.txt")
        caption_path = os.path.join(folder_path, base_name + ".txt")

        with open(text_path, "r", encoding="utf-8") as f: text = f.read()
        with open(caption_path, "r", encoding="utf-8") as f: caption = f.read()

        post_time = find_scheduling_time()
        schedule_time = post_time

        print(f"Scheduling Post for '{str(post_time)}'")

        # schedule_fb_post(image_path, text, schedule_time)
        # save_post_in_db(caption, text, post_time)
        put_post_in_db(caption, text, schedule_time, img)

def post_pending():
    print('Polling..')

    posts = get_not_posted_but_scheduled()

    if not posts:
        return

    print("Pending posts: ", len(posts))

    for post in posts:
        text = post['text']
        image_url = os.path.join(DOWNLOAD_DIR, post['image_name'])
        post_id = post['id']

        if not post['posted_on_fb']:
            try:
                # post_on_fb(image_url, text)
                print('Marking as posted on fb:', post_id)
                mark_as_fb_posted(post_id)
            except:
                pass

        if not post['posted_on_ig']:
            try:
                # post_on_ig(image_url, text)
                print('Marking as posted on ig:', post_id)
                mark_as_ig_posted(post_id)
            except:
                pass



def run_server():
    ServerApp.config['TEMPLATES_AUTO_RELOAD'] = True
    ServerApp.run(host='0.0.0.0', port=1313)
    # waitress.serve(ServerApp, host='0.0.0.0', port=1313)


@ServerApp.route("/")
def index():
    posts = get_all_posts()
    days = {}
    for post in posts:
        day = post['schedule_time'].strftime("%d-%m-%Y")
        if day not in days.keys():
            days[day] = {}
            days[day]['posts'] = []
        days[day]['posts'].append(post)

    return render_template('index.html', days=days)

@ServerApp.route("/images/<path:filename>")
def images(filename):
    return send_from_directory(ServerApp.root_path + '/../downloads/', filename)

def main():

    print(find_scheduling_time())
    return
    threading.Thread(target=run_server, daemon=True).start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(post_pending, 'interval', seconds=1)
    scheduler.start()

    run_telegram()

if __name__ == '__main__':
    main()

# @TODO: proper logging
# @TODO: Docker file
# @TODO: Deploy to Atlas
# @TODO: Error handling
