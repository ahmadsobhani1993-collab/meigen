import cloudscraper
import requests
import json
import io
import time
import os
import hashlib

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json"
MAX_HISTORY_LIMIT = 50000
MAX_POSTS_PER_RUN = 5
TOTAL_PAGES_TO_SCAN = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/app",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9"
}

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def make_unique_key(item_id, prompt_text):
    """
    تولید شناسه کاملاً یکتا بر اساس شناسه ثابت تصویر در سایت و متن پرامپت
    """
    clean_prompt = " ".join(str(prompt_text).strip().split()).lower()
    raw = f"{str(item_id).strip()}::{clean_prompt}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def load_sent_prompts():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                s_set = set(data)
                print(f"📖 سابقه خوانده شد: {len(s_set)} پست قبلاً ثبت شده است.")
                return s_set, list(data)
        except Exception as e:
            print(f"⚠️ خطا در خواندن سابقه: {e}")
            return set(), []
    print("📖 فایل سابقه‌ای وجود ندارد، ایجاد فایل جدید...")
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

def fetch_prompts():
    all_items = []
    seen_in_batch = set()

    for page in range(TOTAL_PAGES_TO_SCAN):
        offset = page * 20
        # فراخوانی با sort=popular یا بدون سورت برای جلوگیری از shuffle شدن نتایج
        api_url = f"https://www.meigen.ai/api/images?offset={offset}&limit=20"
        
        try:
            res = scraper.get(api_url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                continue
                
            data = res.json()
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for key in ['images', 'data', 'items']:
                    if isinstance(data.get(key), list):
                        items = data[key]
                        break

            if not items:
                break

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
                    or (it.get("image") if isinstance(it.get("image"), str) else None)
                )
                item_id = it.get("id") or it.get("_id") or it.get("imageId") or prompt

                if prompt and img:
                    ukey = make_unique_key(item_id, prompt)
                    if ukey not in seen_in_batch:
                        seen_in_batch.add(ukey)
                        all_items.append({
                            "key": ukey,
                            "prompt": prompt,
                            "image_url": img
                        })

        except Exception as e:
            print(f"   ❌ خطا در آفست {offset}: {e}")

    return all_items

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    latest_items = fetch_prompts()

    if not latest_items:
        print("⚠️ دیتایی دریافت نشد.")
        exit(0)

    print(f"🔍 مجموعاً {len(latest_items)} پرامپت منحصر‌به‌فرد از سایت واکشی شد.")

    sent_count = 0

    for idx, it in enumerate(latest_items):
        if sent_count >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) تکمیل شد.")
            break

        ukey = it["key"]

        # چک کردن سابقه
        if ukey in sent_set:
            continue

        print(f"📦 [آیتم #{idx+1}] پرامپت جدید و غیرتکراری پیدا شد! ارسال...")
        if send_photo_file_to_telegram(it["image_url"], it["prompt"]):
            print("✅ با موفقیت ارسال شد.")
            sent_set.add(ukey)
            sent_list.append(ukey)
            sent_count += 1
            time.sleep(4)
        else:
            print("❌ ارسال ناموفق.")

    if sent_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {sent_count} پرامپت ارسال و کلید آن‌ها ذخیره شد.")
    else:
        print(f"💤 تمام {len(latest_items)} پرامپت واکشی‌شده قبلاً در کانال ارسال شده بودند.")
