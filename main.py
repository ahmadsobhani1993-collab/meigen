import cloudscraper
import requests
from bs4 import BeautifulSoup
import json
import io
import time
import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json" 
MAX_HISTORY_LIMIT = 500  # نگهداری تنها ۵۰۰ پرامپت اخیر در تاریخچه

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/"
}

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def load_sent_prompts():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data), list(data)
        except Exception:
            return set(), []
    return set(), []

def save_sent_prompts(sent_list):
    # محدود نگه‌داشتن فایل به آخرین موارد
    trimmed_list = sent_list[-MAX_HISTORY_LIMIT:]
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(trimmed_list, f, ensure_ascii=False, indent=2)

def send_photo_file_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    
    try:
        img_response = requests.get(photo_url, headers=HEADERS, timeout=20)
        if img_response.status_code != 200:
            print(f"❌ خطای دانلود تصویر: کد {img_response.status_code}")
            return False
            
        file_data = io.BytesIO(img_response.content)
        
        if len(caption) <= 1000:
            files = {"photo": ("prompt.jpg", file_data, "image/jpeg")}
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res = requests.post(url, data=payload, files=files, timeout=25).json()
            if not res.get("ok"):
                print(f"❌ خطای تلگرام: {res.get('description')}")
                return False
            return True
        else:
            files = {"photo": ("prompt.jpg", file_data, "image/jpeg")}
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام زیر 👇)", "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res_photo = requests.post(url, data=payload, files=files, timeout=25).json()
            if not res_photo.get("ok"):
                print(f"❌ خطای تلگرام (تصویر): {res_photo.get('description')}")
                return False
                
            msg_id = res_photo["result"]["message_id"]
            text_payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": f"<code>{safe_prompt}</code>\n\n🔗 @prompts_fa",
                "parse_mode": "HTML",
                "reply_to_message_id": msg_id
            }
            url_text = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            res_text = requests.post(url_text, data=text_payload, timeout=25).json()
            return res_text.get("ok", False)
            
    except Exception as e:
        print(f"❌ خطا در پردازش ارسال تلگرام: {e}")
        return False

def get_meigen_prompts():
    url = "https://www.meigen.ai/"
    items = []
    
    try:
        res = scraper.get(url, headers=HEADERS, timeout=20)
        if res.status_code != 200:
            print(f"❌ وضعیت نامعتبر در اتصال به سایت: {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, 'html.parser')
        
        # ۱. بررسی دیتای Next.js
        next_data_script = soup.find('script', id='__NEXT_DATA__')
        if next_data_script and next_data_script.string:
            try:
                data = json.loads(next_data_script.string)
                page_props = data.get('props', {}).get('pageProps', {})
                posts = page_props.get('prompts') or page_props.get('posts') or page_props.get('feed') or []
                for p in posts:
                    prompt = p.get('prompt') or p.get('promptText') or p.get('description')
                    img = p.get('imageUrl') or p.get('image') or p.get('url')
                    if prompt and img:
                        items.append({'prompt': prompt, 'image_url': img})
            except Exception as err:
                print(f"⚠️ پارس __NEXT_DATA__ با خطا مواجه شد: {err}")

        # ۲. فال‌بک مستقیم DOM
        if not items:
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src', '')
                alt = img.get('alt', '').strip()
                if src and len(alt) > 25 and not any(ext in src for ext in ['logo', 'icon', 'avatar']):
                    if src.startswith('/'):
                        src = f"https://www.meigen.ai{src}"
                    items.append({'prompt': alt, 'image_url': src})

        seen = set()
        unique_items = []
        for it in items:
            if it['prompt'] not in seen:
                seen.add(it['prompt'])
                unique_items.append(it)
                
        return unique_items

    except Exception as e:
        print(f"❌ خطای دریافت اطلاعات: {e}")
        return []

def test_telegram_connection():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        res = requests.get(url, timeout=10).json()
        return res.get("ok", False)
    except Exception:
        return False

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not test_telegram_connection():
        print("❌ توکن تلگرام نامعتبر است یا تعریف نشده.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    
    # واکشی آخرین پرامپت‌ها از صفحه اول
    prompts_pool = get_meigen_prompts()
    
    # فیلتر فقط روی ۲۰ آیتم جدید اول فید
    fresh_pool = prompts_pool[:20] 

    # اگر اولین بار است که اسکریپت اجرا می‌شود (فایل خالی است)
    # برای جلوگیری از اسپم شدن کانال، آیتم‌های فعلی سایت را فقط نشانه‌گذاری می‌کند بدون ارسال
    if not sent_set:
        print("ℹ️ اولین اجرای اسکریپت؛ پرامپت‌های کنونی سایت به عنوان ارسال‌شده ثبت می‌شوند تا کانال اسپم نشود.")
        for item in fresh_pool:
            sent_set.add(item['prompt'])
            sent_list.append(item['prompt'])
        save_sent_prompts(sent_list)
        exit(0)

    MAX_POSTS_PER_RUN = 3
    new_count = 0

    # بررسی جدیدترین‌ها از بالا به پایین
    for item in fresh_pool:
        if new_count >= MAX_POSTS_PER_RUN:
            break

        prompt_text = item['prompt']
        img_url = item['image_url']

        if prompt_text not in sent_set:
            new_count += 1
            print(f"📦 ارسال پرامپت جدید [{new_count}/{MAX_POSTS_PER_RUN}]...")
            
            if send_photo_file_to_telegram(img_url, prompt_text):
                print("✅ ارسال شد.")
                sent_set.add(prompt_text)
                sent_list.append(prompt_text)
            else:
                print("❌ ارسال ناموفق.")
            
            time.sleep(4)

    if new_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {new_count} پرامپت جدید با موفقیت به کانال ارسال شد.")
    else:
        print("💤 هیچ پرامپت جدیدی در این نوبت پیدا نشد.")
