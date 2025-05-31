import os
import json
import sys
from datetime import datetime, timedelta, time

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

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MOONDREAM_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJrZXlfaWQiO\
iJkZjkzN2E3Ny1kM2ZlLTQ1YmMtYmE0Ny1iYjJiNTU4NmY1NWMiLCJvcmdfaWQiOiJoN\
k5iTXJDbWxPdkE1UHB0OFdKelhsQlpRcndQSVdxMSIsImlhdCI6MTc0ODQyODQ0NSwidm\
VyIjoxfQ.92LUkmifGQ5g1RPVpNaV9uSVMqseFg3Kehssg1TAolU"

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

def get_not_posted_but_schedules():
    cur = DBConn.cursor()
    cur.execute("SELECT id, text, image_name FROM posts WHERE scheduled = false AND schedule_time <= NOW();")
    posts = cur.fetchall()
    DBConn.commit()
    cur.close()

    return posts

def find_scheduling_time():
    now = datetime.now()

    schedules = get_schedules()
    schedules_map = {}
    for sched in schedules:
        day = sched.strftime("%d-%m-%Y")
        if day not in schedules_map.keys():
            schedules_map[day] = []
        schedules_map[day].append(sched)

    now_str = now.strftime("%d-%m-%Y")
    while now_str in schedules_map.keys() and len(schedules_map[now_str]) > 1:
        now += timedelta(days=1)
        now_str = now.strftime("%d-%m-%Y")

    if now_str in schedules_map.keys():
        scedule = datetime.combine(now.date(), time(hour=18))
    else:
        scedule = datetime.combine(now.date(), time(hour=10))

    return scedule

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

    caption = generate_photo_caption(file_path)
    print(f"Caption for photo: {caption}")
    await update.message.reply_text(f"🧠 Caption: {caption}")

    post_text = generate_post_content(caption)
    print(f"Content for post: {post_text}")
    await update.message.reply_text(f"👍 Post: {post_text}")

    post_time = find_scheduling_time()
    schedule_time = post_time.timestamp()

    print(f"Scheduling Post for '{str(post_time)}'")

    put_post_in_db(caption, post_text, post_time, image_name)

    # schedule_fb_post(file_path, post_text, schedule_time)

    await update.message.reply_text(f"✅ Done! FB/IG Post Scheduled for: {post_time}")

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
    posts = get_not_posted_but_schedules()
    if not posts:
        return

    for post in posts:
        content = post[1]
        image_url = os.path.join(DOWNLOAD_DIR, post[2])

        try:
            post_on_fb(image_url, content)
            post_on_ig(image_url, content)
        except:
            pass


def main():
    run_telegram()


if __name__ == '__main__':
    main()

# @TODO: proper logging
# @TODO: Docker file
# @TODO: Deploy to Atlas
# @TODO: Error handling
# @TODO: DB
