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

DOWNLOAD_DIR = "downloads"

MoondreamModel = moondream.vl(api_key=MOONDREAM_TOKEN)
GeminiClient = genai.Client(api_key=GEMINI_TOKEN)
FacebookGraph = facebook.GraphAPI(access_token=FACEBOOK_TOKEN, version="3.1")

PostsDB = {}
with open('posts.json') as json_data:
    PostsDB = json.load(json_data)

def find_scheduling_time(PostsDB):
    now = datetime.now()

    now_str = now.strftime("%d-%m-%Y")
    while now_str in PostsDB.keys() and len(PostsDB[now_str]) > 1:
        now += timedelta(days=1)
        now_str = now.strftime("%d-%m-%Y")

    if now_str in PostsDB.keys():
        scedule = datetime.combine(now.date(), time(hour=18))
    else:
        scedule = datetime.combine(now.date(), time(hour=10))
    return scedule

def save_posts_json():
    with open('posts.json', 'w', encoding="utf-8") as f:
        json.dump(PostsDB, f)

def generate_photo_caption(image_path):
    image = Image.open(image_path)
    caption_response = MoondreamModel.caption(image, length="normal")
    return caption_response["caption"]

def save_post_in_db(caption, post_text, post_time):
    global PostsDB

    post = {}
    post['time'] = str(post_time)
    post['caption'] = str(caption)
    # post['text'] = str(post_text)

    post_key = post_time.strftime("%d-%m-%Y")
    PostsDB[post_key] = PostsDB.get(post_key, [])
    PostsDB[post_key].append(post)

    save_posts_json()

def generate_post_content(caption):
    response = GeminiClient.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(
            system_instruction="Това ще е в пост в инстаграм. Страницата за която става въпрос е Cattos. В нея публикувам снимки и историики за премеждията на котарака Марципан. Ще трябва да ми помогнеш с правенеот на съдаржание за тази страница. Аз ще ти давам описание на картинката, ти ще ми даваш забавен пост за Марципан,  който ще е за facebook и instagram. Марципан е раг-дол котка, а не сиамка, имай го предвид. Давай ми само текста на поста, коригирай породата на Марципан. Прави постовете малко по-дълги - 4-5 изречения и вкарвай кратка измислена историйка от живота на Марципан, която да е подходяща за описанието на снимкат. Отговряй на Български език."),
        contents=[caption]
    )

    text = response.text
    return text

def schedule_post(image_url, content, scheduled_time):
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
        'published': False,  # Required to make it a scheduled post
        'scheduled_publish_time': scheduled_time,
        'attached_media': str([{'media_fbid': media_fbid}]),
    }
    FacebookGraph.put_object(parent_object=PAGE_ID, connection_name='feed', **post_data)

async def handle_telegram_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("🖼️ Image received. Processing started...")

    photo_file = await update.message.photo[-1].get_file()
    file_path = os.path.join(DOWNLOAD_DIR, f"{photo_file.file_id}.jpg")
    await photo_file.download_to_drive(file_path)
    print(f"Photo from telegram downloaded to {file_path}")

    caption = generate_photo_caption(file_path)
    print(f"Caption for photo: {caption}")
    await update.message.reply_text(f"🧠 Caption: {caption}")

    post_text = generate_post_content(caption)
    print(f"Content for post: {caption}")
    await update.message.reply_text(f"👍 Post: {post_text}")

    post_time = find_scheduling_time(PostsDB)
    schedule_time = post_time.timestamp()

    print(f"Scheduling Post for '{str(post_time)}'")
    schedule_post(file_path, post_text, schedule_time)

    save_post_in_db(caption, post_text, post_time)

    await update.message.reply_text(f"✅ Done! Post Scheduled for: {post_time}")

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

        post_time = find_scheduling_time(PostsDB)
        schedule_time = post_time.timestamp()

        print(f"Scheduling Post for '{str(post_time)}'")

        schedule_post(image_path, text, schedule_time)
        save_post_in_db(caption, text, post_time)

def main():
    run_telegram()

    # folder_path = "cattos"
    # upload_from_folder(folder_path)

if __name__ == '__main__':
    main()

# @TODO: don't leak tokens
# @TODO: .env setup
# @TODO: proper logging
# @TODO: Docker file
# @TODO: Deploy to Atlas
# @TODO: Error handling
