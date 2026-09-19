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
POSTS_PER_RUN = 3  # تعداد ارسالی در هر ساعت

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
    """
    نرمال‌سازی سخت‌گیرانه متن:
    حذف تمام علائم نگارشی، تبدیل به حروف کوچک و یکدست‌سازی فاصله‌ها
    """
    if not text:
        return ""
    # تبدیل به حروف کوچک
    text = text.lower()
    # حذف تمام کاراکترهای غیرحرف و غیراعداد (فقط متن خالص می‌ماند)
    text = re.sub(r'[^a-z0-9\u0600-\u06FF]', '', text)
    return text

def extract_image_unique_id(url):
    """استخراج شناسه یا نام فایل یکتای عکس بدون پارامترهای توکن"""
    if not url:
        return ""
    clean_url = url.split('?')[0].rstrip('/')
    return clean_url.split('/')[-1]

def generate_keys(item_id, prompt, image_url):
    """
    تولید چند کلید مختلف برای مسدود کردن قطعی تکراری‌ها
    """
    keys = []
    # ۱. بر اساس متن نرمال‌شده
    norm_text = clean_text_for_hash(prompt)
    if norm_text:
        text_hash = hashlib.md5(norm_text.encode('utf-8')).hexdigest()
        keys.append(f"text:{text_hash}")
        
    # ۲. بر اساس شناسه عکس اگر در API موجود باشد
    if item_id:
        keys.append(f"id:{str(item_id).strip()}")
        
    # ۳. بر اساس نام فایل عکس
    img_id = extract_image_unique_id(image_url)
    if img_id:
        keys.append(f"img:{img_id}")
        
    return keys

def load_sent_prompts():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data), list(data)
        except Exception as e:
            print(f"⚠️ خطا در خواندن سابقه: {e}")
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
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": "✨ <b>پرامپت جدید</b> ✨\n(متن در پیام زیر 👇)", "parse_mode": "HTML"}
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

def fetch_prompts_from_api(total_pages=3):
    all_items = []
    seen_in_batch = set()

    for p in range(total_pages):
        offset = p * 20
        api_url = f"https://www.meigen.ai/api/images?offset={offset}&limit=20&sort=newest"
        
        try:
            res = scraper.get(api_url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                break
                
            data = res.json()
            items = data if isinstance(data, list) else (
                data.get("images") or data.get("data") or data.get("items") or []
            )

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
                item_id = it.get("id") or it.get("_id") or ""

                if prompt and img:
                    keys = generate_keys(item_id, prompt, img)
                    # اگر در همین نوبت واکشی تکرار شده بود رد شو
                    if any(k in seen_in_batch for k in keys):
                        continue
                    
                    for k in keys:
                        seen_in_batch.add(k)
                        
                    all_items.append({
                        "keys": keys,
                        "prompt": prompt,
                        "image_url": img
                    })

        except Exception as e:
            print(f"❌ خطا در API: {e}")
            break

    return all_items

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تعریف نشده.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    prompts_pool = fetch_prompts_from_api(total_pages=3)

    # هر پستی که حداقل یکی از کلیدهایش (متن نرمال‌شده، آی‌دی یا اسم عکس) در سابقه باشد، بلافاصله رد می‌شود
    unsend_items = []
    for item in prompts_pool:
        if not any(k in sent_set for k in item["keys"]):
            unsend_items.append(item)

    print(f"📋 کل واکشی: {len(prompts_pool)} | غیرتکراری و جدید: {len(unsend_items)}")

    new_count = 0
    for item in unsend_items:
        if new_count >= POSTS_PER_RUN:
            break

        print(f"📦 در حال ارسال [{new_count + 1}/{POSTS_PER_RUN}]...")
        
        if send_photo_file_to_telegram(item["image_url"], item["prompt"]):
            print(f"✅ ارسال موفق")
            # ثبت تمام شناسه‌ها و هش‌های مربوط به این آیتم
            for k in item["keys"]:
                sent_set.add(k)
                sent_list.append(k)
            new_count += 1
            time.sleep(4)
        else:
            print("❌ ارسال ناموفق.")

    if new_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {new_count} پرامپت ارسال شد و در تاریخچه ثبت گردید.")
    else:
        print("💤 هیچ پرامپت جدیدی یافت نشد.")
