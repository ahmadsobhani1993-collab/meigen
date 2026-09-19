import cloudscraper
import requests
import json
import io
import time
import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATE_FILE = "crawler_state.json"
POSTS_PER_RUN = 3

# آفست شروع از گذشته (می‌توانید روی ۲۰۰، ۵۰۰ یا ۱۰۰۰ بگذارید)
INITIAL_OFFSET = 300  

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/app",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9"
}

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def load_state():
    """خواندن آخرین آفست پردازش‌شده و هش‌های ارسالی"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"current_offset": INITIAL_OFFSET, "sent_ids": []}

def save_state(state):
    # نگهداری حداکثر ۱۰۰۰ شناسه اخیر
    state["sent_ids"] = state["sent_ids"][-1000:]
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_photo_file_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    
    try:
        img_res = requests.get(photo_url, headers=HEADERS, timeout=25)
        if img_res.status_code != 200:
            print(f"❌ دانلود ناموفق عکس: {img_res.status_code}")
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
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام بعد 👇)", "parse_mode": "HTML"}
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

def fetch_batch_from_offset(offset, limit=POSTS_PER_RUN):
    api_url = f"https://www.meigen.ai/api/images?offset={offset}&limit={limit}&sort=newest"
    print(f"📡 واکشی آفست {offset} به تعداد {limit} مورد...")
    try:
        res = scraper.get(api_url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            data = res.json()
            items = data if isinstance(data, list) else (
                data.get("images") or data.get("data") or data.get("items") or []
            )
            return items
    except Exception as e:
        print(f"❌ خطا در واکشی: {e}")
    return []

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تعریف نشده.")
        exit(1)

    state = load_state()
    current_offset = state.get("current_offset", INITIAL_OFFSET)
    sent_ids = set(state.get("sent_ids", []))

    print(f"📍 آفست فعلی: {current_offset}")

    items = fetch_batch_from_offset(current_offset, limit=POSTS_PER_RUN)

    # اگر در آفست تعیین‌شده دیتایی نبود (بیش از حد به گذشته رفتیم)، آفست را کمتر کن
    if not items and current_offset > POSTS_PER_RUN:
        print("⚠️ آفست فعلی خالی بود، کاهش ۵۰ واحدی برای همگام‌سازی...")
        state["current_offset"] = max(0, current_offset - 50)
        save_state(state)
        exit(0)

    sent_count = 0
    for it in items:
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
        item_id = str(it.get("id") or it.get("_id") or prompt[:30])

        if prompt and img and item_id not in sent_ids:
            print(f"📦 در حال ارسال پرامپت (آفست {current_offset})...")
            if send_photo_file_to_telegram(img, prompt):
                print(f"✅ با موفقیت ارسال شد.")
                sent_ids.add(item_id)
                state["sent_ids"].append(item_id)
                sent_count += 1
                time.sleep(4)

    # حرکت آفست به سمت جلو (به سمت جدیدترین‌ها)
    # اگر به ۰ رسید، ریست شود یا روی ۰ بماند
    new_offset = current_offset - POSTS_PER_RUN
    if new_offset < 0:
        print("🏁 به تازه‌ترین پست‌های سایت رسیدیم! آفست دوباره ریست می‌شود.")
        new_offset = INITIAL_OFFSET

    state["current_offset"] = new_offset
    save_state(state)
    print(f"💾 آفست جدید ذخیره شد: {new_offset} (تعداد ارسال: {sent_count})")
