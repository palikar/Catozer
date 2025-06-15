import os
import traceback
import json
import sys
import logging
from datetime import datetime, timedelta, time
import threading
import urllib.parse
from pathlib import Path
import asyncio
import requests

from PIL import Image

from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler

import moondream

import facebook

from google import genai
from google.genai import types

from dotenv import load_dotenv

from imgurpython import ImgurClient

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, render_template, send_from_directory, request, jsonify
import flask
import waitress

from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

AVAILABLE_DAY_TIMES = [
    10,
    18
]

MOONDREAM_TOKEN=os.getenv("MOONDREAM_TOKEN")

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
IG_CLIENT_SECRET = os.getenv("IG_CLIENT_SECRET")
IG_CLIENT_ID = os.getenv("IG_CLIENT_ID")

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = os.getenv("DB_PORT")

# #(Database)
DBConn = psycopg2.connect(
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASS,
    host=DB_HOST,
    port=DB_PORT
)

def default_config():
    config = {}

    config['TELEGRAM_BOT_TOKEN'] = os.getenv('TELEGRAM_BOT_TOKEN')
    config['MOONDREAM_TOKEN'] = os.getenv('MOONDREAM_TOKEN')
    config['FACEBOOK_TOKEN'] = os.getenv('FACEBOOK_TOKEN')
    config['IG_TOKEN'] = os.getenv('IG_TOKEN')
    config['INSTAGRAM_TOKEN '] = os.getenv('INSTAGRAM_TOKEN')
    config['GEMINI_TOKEN'] = os.getenv('GEMINI_TOKEN')
    config['IMGUR_CLIENT_ID'] = os.getenv('IMGUR_CLIENT_ID')
    config['IMGUR_CLIENT_SECRET'] = os.getenv('IMGUR_CLIENT_SECRET')
    config['IMGUR_ACCESS_TOKEN'] = os.getenv('IMGUR_ACCESS_TOKEN')
    config['IMGUR_REFRESH_TOKEN'] = os.getenv('IMGUR_REFRESH_TOKEN')
    config['PAGE_ID'] = os.getenv('PAGE_ID')
    config['IG_ID'] = os.getenv('IG_ID')
    config['IG_CLIENT_ID'] = os.getenv('IG_CLIENT_ID')
    config['IG_CLIENT_SECRET'] = os.getenv('IG_CLIENT_SECRET')

    return config

def db_config_fields():
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM config")
        confs = cur.fetchall()
        DBConn.commit()

    conf_dict = {}
    for conf in confs:
        conf_dict[conf['name']] = conf['value']

    return conf_dict

def add_db_config_fields(name, value):
    cur = DBConn.cursor()
    cur.execute("""INSERT INTO config (name, value) VALUES (%s, %s)""", (name, value))
    DBConn.commit()
    cur.close()

def update_config_field(name, value):
    global CONFIG
    CONFIG[name] = value

    cur = DBConn.cursor()
    cur.execute("""UPDATE config SET value = %s WHERE name = %s""", (value, name))
    DBConn.commit()
    cur.close()

def load_config():
    config = {}
    defaults = default_config()
    db_conf = db_config_fields()

    for name, value in defaults.items():
        if name not in db_conf.keys():
            config[name] = value
            add_db_config_fields(name, value)
        else:
            config[name] = db_conf[name]

    return config

def does_chatter_exists_in_db(chat_id):
    cur = DBConn.cursor()
    cur.execute("SELECT COUNT(*) FROM chat_users WHERE chat_name = %s", (chat_id, ))
    count = cur.fetchall()
    DBConn.commit()
    cur.close()
    return count[0][0] != 0

def is_chatter_verified_in_db(chat_id):
    cur = DBConn.cursor()
    cur.execute("SELECT COUNT(*) FROM chat_users WHERE chat_name = %s AND verified = 'true'", (chat_id, ))
    count = cur.fetchall()
    DBConn.commit()
    cur.close()
    return count[0][0] != 0

def new_chatter_in_db(chat_id, name, subscribed):
    cur = DBConn.cursor()
    print((chat_id, name, subscribed, ))
    cur.execute("INSERT INTO chat_users (chat_name, name, subscribed) VALUES(%s, %s, %s)",
                (chat_id, name, subscribed, ))
    DBConn.commit()
    cur.close()

def subscribe_chatter_in_db(chat_id, subbed):
    cur = DBConn.cursor()
    cur.execute("UPDATE chat_users SET subscribed = %s WHERE chat_name = %s", (subbed, chat_id))
    DBConn.commit()
    cur.close()

def get_chat_subscribes():
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM chat_users WHERE subscribed = 'true' AND verified = 'true'")
        users = cur.fetchall()
        DBConn.commit()
    return users

CONFIG = load_config()

CATOZER_DEBUG = os.getenv("CATOZER_DEBUG") == "1"

DOWNLOAD_DIR = "downloads"



# #(Server)
ServerApp = Flask(__name__, static_url_path='', static_folder='../web/static', template_folder='../web/templates')

# #(logger)
logging.basicConfig(
    level=logging.INFO,

    format='[%(asctime)s] %(levelname)s in %(name)s:%(lineno)d -- %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',

    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('telegram.bot').setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

Logger = logging.getLogger(__name__)


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
    cur.execute("SELECT schedule_time FROM posts WHERE schedule_time > NOW() - INTERVAL '1 day'")
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

def get_post(post_id):
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM posts WHERE id = %s", (post_id, ))
        posts = cur.fetchone()
        DBConn.commit()

    return posts

def update_post(post_id, caption, text):
    cur = DBConn.cursor()
    cur.execute("UPDATE posts SET caption = %s, text = %sWHERE id = %s", (caption, text, post_id, ))
    DBConn.commit()
    cur.close()


def get_not_posted_but_scheduled():
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET TIMEZONE='Europe/Berlin'; SELECT id, text, image_name, posted_on_fb, posted_on_ig FROM posts WHERE (posted_on_fb = false OR posted_on_ig = false) AND schedule_time <= NOW();")
        posts = cur.fetchall()
        DBConn.commit()

    return posts

def count_posts_in_queue():
    with DBConn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET TIMEZONE='Europe/Berlin'; SELECT COUNT(*) FROM posts WHERE (posted_on_fb = false OR posted_on_ig = false) AND schedule_time > NOW();")
        count = cur.fetchall()
        DBConn.commit()

    return count[0]['count']

def mark_as_fb_posted(post_id):
    with DBConn.cursor() as cur:
        cur.execute("UPDATE posts SET posted_on_fb = true WHERE id = %s", (post_id, ))
        DBConn.commit()

def mark_as_ig_posted(post_id):
    with DBConn.cursor() as cur:
        cur.execute("UPDATE posts SET posted_on_ig = true WHERE id = %s", (post_id, ))
        DBConn.commit()

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

    MoondreamModel = moondream.vl(api_key=CONFIG['MOONDREAM_TOKEN'])
    image = Image.open(image_path)
    caption_response = MoondreamModel.caption(image, length="normal")
    return caption_response["caption"]

def generate_post_content(caption):

    GeminiClient = genai.Client(api_key=CONFIG['GEMINI_TOKEN'])
    response = GeminiClient.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(
            system_instruction="Това ще е в пост в инстаграм. Страницата за която става въпрос е Cattos. В нея публикувам снимки и историики за премеждията на котарака Марципан. Ще трябва да ми помогнеш с правенеот на съдаржание за тази страница. Аз ще ти давам описание на картинката, ти ще ми даваш забавен пост за Марципан,  който ще е за facebook и instagram. Марципан е раг-дол котка, а не сиамка, имай го предвид. Давай ми само текста на поста. Прави постовете малко по-къси - 2-3 изречения и вкарвай кратка измислена историйка от живота на Марципан, която да е подходяща за описанието на снимкат. Отговряй на Български език и не прави правописни грешки. Съдържанието трябва да е от името на Марципан."),
        contents=[caption]
    )

    text = response.text
    return text

def post_on_fb(image_url, content):

    FacebookGraph = facebook.GraphAPI(access_token=CONFIG['FACEBOOK_TOKEN'], version="3.1")

    try:
        with open(image_url, 'rb') as image:
            photo = FacebookGraph.put_photo(
                image = image,
                album_path = 'me/photos',
                published = False,
            )

        if 'error' in photo.keys():
            raise Exception(f"There is an error from FB: {photo['error']}")

        if 'id' not in photo.keys():
            raise Exception(f'The response from IG was not the expected one: {photo}')

        media_fbid = photo['id']
        Logger.info(f'Uploaded photo to Facebook - id: {media_fbid}')
    except Exception as e:
        raise ValueError('Could not upload photo to Facebook') from e

    try:
        post_data = {
            'message': content,
            'attached_media': str([{'media_fbid': media_fbid}]),
        }
        response = FacebookGraph.put_object(parent_object=PAGE_ID, connection_name='feed', **post_data)
        if 'error' in response.keys():
            raise Exception(f"There is an error from FB: {response['error']}")
    except Exception as e:
        raise ValueError('Could not publish post to Facebook') from e

    send_chat_subs_message('✉️ Posted on Facebook ✅')


def post_on_ig(image_url, content):
    Imgur = ImgurClient(CONFIG['IMGUR_CLIENT_ID'], CONFIG['IMGUR_CLIENT_SECRET'])
    Imgur.set_user_auth(CONFIG['IMGUR_ACCESS_TOKEN'], CONFIG['IMGUR_REFRESH_TOKEN'])

    try:
        result = Imgur.upload_from_path(image_url, config=None, anon=False)
        link = result['link']
        img_id = result['id']
        Logger.info(f'Uploaded image with link {link}')

    except Exception as e:
        raise ValueError(f'Could not upload image to Imgur') from e

    try:
        try:
            payload = {
                'image_url': link,
                'caption': content,
                'access_token': CONFIG['IG_TOKEN'],
            }
            response = requests.post(f'https://graph.instagram.com/me/media', data=payload)
            response = response.json()

            if 'error' in response.keys():
                error = response['error']
                Logger.error(error)
                raise ValueError(f"There is an error from IG: {error}")

            if 'id' not in response.keys():
                raise ValueError(f'The response from IG was not the expected one: {response}')

            Logger.info(f'Created IG creation...: {response}')
        except Exception as e:
            raise ValueError(f'Could not upload media to Instagram') from e

        try:
            creation_id = response['id']
            Logger.info(f'Uploading creation id {creation_id}')
            payload = {
                'image_url': link,
                'creation_id': creation_id,
                'access_token': CONFIG['IG_TOKEN']
            }
            response = requests.post(f'https://graph.instagram.com/me/media_publish', data=payload)
            response = response.json()
            if 'error' in response.keys():
                raise Exception(f"There is an error from IG: {response['error']}")

        except Exception as e:
            raise ValueError(f'Could not publish media to Instagram.') from e

    except Exception as e:
        try:
            if img_id is not None:
                Logger.info(f'Deleting imgur image: {img_id}')
                Imgur.delete_image(img_id)
        except _:
            pass

        raise e

    send_chat_subs_message('✉️ Posted on Instagram ✅')

def send_chat_subs_message(msg):

    async def send_chat_message(msg):
        Logger.info(f'Sending bot message: {msg}')
        TelegramApp = ApplicationBuilder().token(CONFIG['TELEGRAM_BOT_TOKEN']).build()
        subs = get_chat_subscribes()
        for sub in subs:
            await TelegramApp.bot.send_message(chat_id=sub['chat_name'], text=msg)

    asyncio.run(send_chat_message(msg))

def get_photo_caption_and_text():
    pass


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # If the chatter does not exist or is not verified, simply ignore; Still log their chat_id in the DB
    # but leave them unverified
    chat_id = update.effective_chat.id
    if not is_chatter_verified_in_db(chat_id):
        if not does_chatter_exists_in_db(chat_id):
            name = update.message.chat.first_name + '_' + update.message.chat.last_name
            new_chatter_in_db(chat_id, name, False)
        return

    await update.message.reply_text("🖼️ Image received. Processing started...")

    photo_file = await update.message.photo[-1].get_file()
    image_name = f"{photo_file.file_id}.jpg"
    file_path = os.path.join(DOWNLOAD_DIR, f"{image_name}")
    await photo_file.download_to_drive(file_path)
    Logger.info(f"Photo from telegram downloaded to {file_path}")

    post_text = None
    caption = None

    try:
        caption = generate_photo_caption(file_path)
        Logger.info(f"Caption for photo: {caption}")
        await update.message.reply_text(f"🧠 Caption: {caption}")
    except:
        Logger.error('Could generate post caption with Moondream')

    try:
        if caption is not None:
            post_text = generate_post_content(caption)
            Logger.info(f"Generated content for post!")
            await update.message.reply_text(f"👍 Post: {post_text}")
    except:
        Logger.error('Could generate post content with Gemini')

    post_time = find_scheduling_time()
    schedule_time = post_time.timestamp()
    Logger.info(f"Scheduling Post for '{str(post_time)}'")

    try:
        put_post_in_db(caption, post_text, post_time, image_name)
    except:
        Logger.error('Could save post to DB')

    if post_text is not None and caption is not None:
        Logger.info(f'FB/IG Post Scheduled for: {post_time}')
        await update.message.reply_text(f"✅ Done! FB/IG Post Scheduled for: {post_time}")
    else:
        Logger.info(f'Something went wrong but the post should be in the DB')
        await update.message.reply_text(f"❌ Something went wrong but will retry in some while.")

async def handle_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.message.chat.first_name + '_' + update.message.chat.last_name

    if not does_chatter_exists_in_db(chat_id):
        Logger.info(f'New chatter wants to subscribe: {name}')
        new_chatter_in_db(chat_id, name, True)
        await update.message.reply_text("🙋‍♂️ New user in db! Hello 👋!")
        await update.message.reply_text("You are now subscribed 👍")
    else:
        Logger.info(f'Chatter wants to subscribe: {name}')
        subscribe_chatter_in_db(chat_id, True)
        await update.message.reply_text("You are now subscribed 👍")


async def handle_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.message.chat.first_name + '_' + update.message.chat.last_name

    if not does_chatter_exists_in_db(chat_id):
        Logger.info(f'New chatter wants to unsubscribe: {name}')
        new_chatter_in_db(chat_id, name, False)
        await update.message.reply_text("🙋‍♂️ New user in db! Hello 👋!")
        await update.message.reply_text("You are now unsubscribed 👍")
    else:
        Logger.info(f'Chatter wants to unsubscribe: {name}')
        subscribe_chatter_in_db(chat_id, False)
        await update.message.reply_text("You are now unsubscribed 👍")

async def handle_health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("😼 It's all gud boss!✔️")

def run_telegram():
    try:
        os.makedirs(DOWNLOAD_DIR)
    except OSError:
        pass

    TelegramApp = ApplicationBuilder().token(CONFIG['TELEGRAM_BOT_TOKEN']).build()
    TelegramApp.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    TelegramApp.add_handler(CommandHandler("subscribe", handle_subscribe))
    TelegramApp.add_handler(CommandHandler("unsubscribe", handle_unsubscribe))
    TelegramApp.add_handler(CommandHandler("health", handle_health_check))

    Logger.info('Starting Telegram Bot...')
    TelegramApp.run_polling()


last_posting_event_time = 0
try_posting_interval_time = 60 * 60  # in seconds

def post_pending(send_tg_message = True):
    Logger.info('Polling..')

    posts = get_not_posted_but_scheduled()

    if not posts:
        return

    Logger.info(f"Pending posts: {len(posts)}")

    # Run the post logic only so often
    now = datetime.now().timestamp()
    global last_posting_event_time
    if now - last_posting_event_time < try_posting_interval_time:
        Logger.info(f"Rate limit: Skipping the special work this time")
        return
    last_posting_event_time = now

    for post in posts:
        text = post['text']
        image_url = os.path.join(DOWNLOAD_DIR, post['image_name'])
        post_id = post['id']

        if not post['posted_on_fb']:
            try:
                post_on_fb(image_url, text)
                Logger.info(f'Marking as posted on fb:{post_id}')
                mark_as_fb_posted(post_id)
            except Exception as e:
                Logger.error(f'Could not update fb post in DB: {e}', exc_info=True)
                if send_tg_message:
                    send_chat_subs_message(f'⛔ Problem! Could not update Facebook post in DB; Error: {e}')

        if not post['posted_on_ig']:
            try:
                post_on_ig(image_url, text)
                Logger.info(f'Marking as posted on ig: {post_id}')
                mark_as_ig_posted(post_id)
            except Exception as e:
                Logger.error(f'Could not update ig post in DB: {e}', exc_info=True)
                traceback.print_exc()
                if send_tg_message:
                    send_chat_subs_message(f'⛔ Problem! Could not update Instagram post in DB; Error: {e}')

def check_post_queue():
    Logger.info('Checking queue...')
    now = datetime.now().time()
    if not (time(9, 0) <= now <= time(23, 0)):
        Logger.info(f'It\'s too eraly/late to notify chatters!')
        return

    posts_in_queue = count_posts_in_queue()
    Logger.info(f'Posts in queue: {posts_in_queue}')

    min_days_with_posts = 2
    posts_per_day = 2
    if not posts_in_queue < min_days_with_posts * posts_per_day:
        return

    send_chat_subs_message(f'🚨 Post queue is getting low! Posts in queue left: {posts_in_queue}')

def health_update():
    send_chat_subs_message(f"😼 It's all gud boss!✔️")

def run_server():
    ServerApp.config['TEMPLATES_AUTO_RELOAD'] = True

    ServerApp.run(ssl_context=('cert.pem', 'key.pem'), host='0.0.0.0', port=1313)

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

@ServerApp.route("/config")
def config():
    db_config = db_config_fields()

    Imgur = ImgurClient(CONFIG['IMGUR_CLIENT_ID'], CONFIG['IMGUR_CLIENT_SECRET'])
    imgur_link = Imgur.get_auth_url('pin')

    return render_template('config.html', tokens=db_config,
                           messages=[],
                           imgur_link=imgur_link)

@ServerApp.route("/api/ig_token")
def api_ig_token():
    app_id = "1038808407801073"
    redirect = "https://localhost:1313/api/ig_token/callback"
    perms = "instagram_business_basic,instagram_business_manage_messages,instagram_business_manage_comments,instagram_business_content_publish"
    params = {
        'client_id': app_id,
        'redirect_uri': redirect,
        'scope': perms,
        'response_type': 'code',
    }

    auth_link = "https://www.instagram.com/oauth/authorize"
    url = f"{auth_link}?client_id={app_id}&redirect_uri={redirect}&response_type=code&scope={perms}&code=1234"
    url = f"{auth_link}?{urllib.parse.urlencode(params)}"

    return flask.redirect(url, code=302)

@ServerApp.route("/api/ig_token/callback")
def api_ig_token_callback():
    code = request.args.get('code')

    redirect = "https://localhost:1313/api/ig_token/callback"
    payload = {
        'client_id': IG_CLIENT_ID,
        'client_secret': IG_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect,
        'code': code,
    }
    response = requests.post('https://api.instagram.com/oauth/access_token', data=payload)
    response = response.json()
    short_access_token = response['access_token']

    redirect = "https://localhost:1313/api/ig_token/callback"
    payload = {
        'client_secret': IG_CLIENT_SECRET,
        'grant_type': 'ig_exchange_token',
        'access_token' : short_access_token
    }

    auth_link = 'https://graph.instagram.com/access_token'
    url = f"{auth_link}?{urllib.parse.urlencode(payload)}"
    response = requests.get(url)
    response = response.json()
    short_access_token = response['access_token']

    update_config_field('IG_TOKEN', short_access_token)

    return flask.redirect("/config", code=302)

@ServerApp.route("/api/imgur_pin", methods=['POST'])
def api_imgur_pin():
    pin = request.form.get('pin')
    print(pin)

    Imgur = ImgurClient(CONFIG['IMGUR_CLIENT_ID'], CONFIG['IMGUR_CLIENT_SECRET'])

    credentials = Imgur.authorize(pin, 'pin')
    update_config_field('IMGUR_ACCESS_TOKEN', credentials['access_token'])
    update_config_field('IMGUR_REFRESH_TOKEN', credentials['refresh_token'])
    print(credentials)

    return flask.redirect("/config", code=302)

@ServerApp.route("/api/set_config/<key>", methods=['POST'])
def api_set_config(key):
    value = request.form.get('value')
    update_config_field(key, value)

    return flask.redirect("/config", code=302)

@ServerApp.route("/api/regen/<post_id>", methods=['GET'])
def api_regen_post(post_id):
    post = get_post(post_id)

    file_path = ServerApp.root_path + '/../downloads/' + post['image_name']
    post_text = None
    caption = None

    try:
        caption = generate_photo_caption(file_path)
        Logger.info(f"Caption for photo: {caption}")
    except:
        Logger.error('Could generate post caption with Moondream')

    try:
        if caption is not None:
            post_text = generate_post_content(caption)
            Logger.info(f"Generated content for post!")
    except Exception as e:
        Logger.error('Could generate post content with Gemini')
        Logger.error(e)

    if caption is not None and post_text is not None:
        update_post(post_id, caption, post_text)

    return flask.redirect("/", code=302)

@ServerApp.route("/images/<path:filename>")
def images(filename):
    return send_from_directory(ServerApp.root_path + '/../downloads/', filename)

def main():
    threading.Thread(target=run_server, daemon=True).start()

    poll_interval_seconds = 30
    if CATOZER_DEBUG:
        poll_interval_seconds = 1
    logging.getLogger('apscheduler').setLevel(logging.ERROR)

    scheduler = BackgroundScheduler()
    scheduler.add_job(post_pending, 'interval', seconds=poll_interval_seconds)
    scheduler.add_job(check_post_queue, 'interval', hours=4)
    scheduler.add_job(health_update, 'interval', hours=12)
    scheduler.start()

    run_telegram()
