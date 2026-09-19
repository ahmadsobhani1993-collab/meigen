import cloudscraper
import requests
import json
import io
import time
import os
import hashlib
import re

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json"
MAX_HISTORY_LIMIT = 3000
MAX_POSTS_PER_RUN = 3
TOTAL_PAGES_TO_SCAN = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/app",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9"
}

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def clean_text_for_hash(text):
    if not text:
        return ""
    text = text.lower()
    return re.sub(r'[^a-z0-9\u0600-\u06FF]', '', text)

def make_prompt_hash(prompt_text):
    clean = clean_text_for_hash(prompt_text)
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

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
    trimmed = sent_list[-MAX_HISTORY_LIMIT:]
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)

def send_photo_file_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    
    try:
        img_res = requests.get(photo_url, headers=HEADERS, timeout=25)
        if img_res.status_code != 200:
            print(f"❌ دانلود عکس ناموفق: {img_res.status_code}")
            return False
            
        file_data = io.BytesIO(img_res.content)
        
        if len(caption) <= 1000:
            files = {"photo": ("prompt.jpg", file_data, "image/jpeg")}
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res = requests.post(url, data=payload, files=files, timeout=25).json()
            return res.get("ok", False)
        else:
            files = {"photo": ("prompt.jpg", file_data, "image/jpeg")}
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام زیر 👇)", "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res_photo = requests.post(url, data=payload, files=files, timeout=25).json()
            if not res_photo.get("ok"):
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
        print(f"❌ خطای تلگرام: {e}")
        return False

def fetch_prompts():
    all_items = []
    seen_in_batch = set()

    for page in range(TOTAL_PAGES_TO_SCAN):
        offset = page * 20
        # استفاده از sort=featured طبق درخواست شبکه شما
        api_url = f"https://www.meigen.ai/api/images?offset={offset}&limit=20&sort=featured"
        print(f"📡 درخواست به: offset={offset} (sort=featured)...")
        
        try:
            res = scraper.get(api_url, headers=HEADERS, timeout=15)
            print(f"   وضعیت پاسخ: {res.status_code}")
            
            if res.status_code != 200:
                print(f"   ⚠️ پاسخ نامعتبر، رد شدن از صفحه...")
                continue
                
            data = res.json()
            
            # پیدا کردن آرایه داده‌ها در خروجی جیسون
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                # لاگ کلیدهای اصلی جیسون دریافتی برای شفافیت
                print(f"   کلیدهای موجود در پاسخ: {list(data.keys())}")
                for key in ['images', 'data', 'items', 'rows', 'results']:
                    if isinstance(data.get(key), list):
                        items = data[key]
                        break

            print(f"   تعداد آیتم‌های استخراج شده از این صفحه: {len(items)}")

            if not items:
                break

            for it in items:
                # استخراج متن پرامپت از فیلدهای محتمل
                prompt = (
                    it.get("prompt")
                    or it.get("prompt_text")
                    or it.get("description")
                    or it.get("caption")
                )
                
                # استخراج آدرس تصویر
                img = (
                    it.get("url")
                    or it.get("image_url")
                    or it.get("imageUrl")
                    or it.get("src")
                    or (it.get("image") if isinstance(it.get("image"), str) else None)
                )

                if prompt and img:
                    p_hash = make_prompt_hash(prompt)
                    if p_hash not in seen_in_batch:
                        seen_in_batch.add(p_hash)
                        all_items.append({
                            "hash": p_hash,
                            "prompt": prompt,
                            "image_url": img
                        })

        except Exception as e:
            print(f"   ❌ خطا در خواندن این آفست: {e}")

    return all_items

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    latest_items = fetch_prompts()

    if not latest_items:
        print("⚠️ دیتایی دریافت نشد. لاگ‌های کلیدهای بالا را بررسی کنید.")
        exit(0)

    print(f"🔍 مجموعاً {len(latest_items)} پرامپت معتبر واکشی شد. بررسی برای ارسال...")

    sent_count = 0

    for idx, it in enumerate(latest_items):
        if sent_count >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) پر شد.")
            break

        p_hash = it["hash"]

        # اگر قبلاً ارسال شده بود، رد شو
        if p_hash in sent_set:
            continue

        print(f"📦 [آیتم #{idx+1}] در حال ارسال پرامپت جدید به کانال...")
        if send_photo_file_to_telegram(it["image_url"], it["prompt"]):
            print("✅ با موفقیت ارسال شد.")
            sent_set.add(p_hash)
            sent_list.append(p_hash)
            sent_count += 1
            time.sleep(4)
        else:
            print("❌ ارسال ناموفق.")

    if sent_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {sent_count} پرامپت با موفقیت ارسال و در دیتابیس ثبت شد.")
    else:
        print(f"💤 تمام {len(latest_items)} پرامپت قبلاً در کانال ارسال شده بودند.")
