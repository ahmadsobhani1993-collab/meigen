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
MAX_HISTORY_LIMIT = 2000
POSTS_PER_RUN = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/app",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9"
}

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def make_fingerprint(prompt_text, image_url):
    """تولید یک کلید هش منحصر‌به‌فرد از متن پرامپت و نام فایل عکس"""
    # استخراج نام فایل از انتهای آدرس عکس بدون پارامترها
    img_name = image_url.split('?')[0].split('/')[-1]
    # تمیزسازی متن پرامپت برای مقایسه دقیق
    clean_prompt = " ".join(prompt_text.strip().split())
    raw_key = f"{clean_prompt}::{img_name}"
    return hashlib.md5(raw_key.encode('utf-8')).hexdigest()

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
    seen = set()

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

                if prompt and img:
                    fp = make_fingerprint(prompt, img)
                    if fp not in seen:
                        seen.add(fp)
                        all_items.append({
                            "fingerprint": fp,
                            "prompt": prompt,
                            "image_url": img
                        })

        except Exception as e:
            print(f"❌ خطا در خواندن API: {e}")
            break

    return all_items

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تعریف نشده.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    prompts_pool = fetch_prompts_from_api(total_pages=3)

    # فیلتر فقط مواردی که هش متن و عکسشان در سابقه نیست
    unsend_items = [item for item in prompts_pool if item["fingerprint"] not in sent_set]
    print(f"📋 کل پرامپت‌های واکشی شده: {len(prompts_pool)} | جدید و ارسال‌نشده: {len(unsend_items)}")

    new_count = 0
    for item in unsend_items:
        if new_count >= POSTS_PER_RUN:
            break

        fp = item["fingerprint"]
        print(f"📦 ارسال [{new_count + 1}/{POSTS_PER_RUN}]...")
        
        if send_photo_file_to_telegram(item["image_url"], item["prompt"]):
            print(f"✅ ارسال موفق (Hash: {fp[:8]}...)")
            sent_set.add(fp)
            sent_list.append(fp)
            new_count += 1
            time.sleep(4)
        else:
            print("❌ ارسال شکست خورد.")

    if new_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {new_count} پرامپت ارسال و ذخیره شد.")
    else:
        print("💤 هیچ پرامپت جدیدی برای ارسال وجود نداشت.")
