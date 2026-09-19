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
MAX_HISTORY_LIMIT = 2000
MAX_POSTS_PER_RUN = 3  # در هر نوبت نهایتاً ۳ پرامپت جدید بفرستد

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
    """حذف فاصله‌ها و علائم برای ساخت هش یکتا و غیرقابل خطا از متن پرامپت"""
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
            print(f"❌ دانلود عکس ناموفق: وضعیت {img_res.status_code}")
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

def fetch_latest_prompts():
    """فقط جدیدترین پرامپت‌های بالای فید را دریافت می‌کند"""
    api_url = "https://www.meigen.ai/api/images?offset=0&limit=25&sort=newest"
    print("📡 در حال دریافت ۲۰ پرامپت تازه از بالای سایت...")
    try:
        res = scraper.get(api_url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            data = res.json()
            items = data if isinstance(data, list) else (
                data.get("images") or data.get("data") or data.get("items") or []
            )
            return items
    except Exception as e:
        print(f"❌ خطای اتصال به API: {e}")
    return []

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ توکن تلگرام تنظیم نشده است.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    latest_items = fetch_latest_prompts()

    if not latest_items:
        print("⚠️ دیتایی از سایت دریافت نشد.")
        exit(0)

    print(f"🔍 تعداد {len(latest_items)} پرامپت دریافت شد. بررسی به ترتیب از جدیدترین...")

    sent_count = 0

    # بررسی یکی‌یکی از جدیدترین به قدیمی‌تر
    for idx, it in enumerate(latest_items):
        if sent_count >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) پر شد.")
            break

        prompt = (
            it.get("prompt")
            or it.get("prompt_text")
            or it.get("description")
            or it.get("caption")
        )
        img = (
            it.get("url")
            or it.get("image_url")
            or it.get("imageUrl")
            or it.get("src")
        )

        if not prompt or not img:
            continue

        p_hash = make_prompt_hash(prompt)

        # اگر قبلاً ارسال شده، اسکیپ کن
        if p_hash in sent_set:
            continue

        # اگر قبلاً ارسال نشده، بفرست
        print(f"📦 [آیتم #{idx+1}] پرامپت جدید کشف شد! در حال ارسال به کانال...")
        if send_photo_file_to_telegram(img, prompt):
            print("✅ با موفقیت ارسال شد.")
            sent_set.add(p_hash)
            sent_list.append(p_hash)
            sent_count += 1
            time.sleep(4)
        else:
            print("❌ ارسال ناموفق.")

    if sent_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {sent_count} پرامپت جدید با موفقیت به کانال ارسال شد.")
    else:
        print("💤 هیچ پرامپت جدیدی نسبت به سابقه قبلی وجود نداشت (همه اسکیپ شدند).")
