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
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent_prompts(sent_set):
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(sent_set), f, ensure_ascii=False, indent=4)

def send_photo_file_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    
    try:
        img_response = requests.get(photo_url, headers=HEADERS, timeout=20)
        if img_response.status_code != 200:
            print(f"❌ خطای دانلود عکس: وضعیت {img_response.status_code}")
            return False
            
        file_data = io.BytesIO(img_response.content)
        
        if len(caption) <= 1000:
            files = {"photo": ("prompt.jpg", file_data, "image/jpeg")}
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res = requests.post(url, data=payload, files=files, timeout=25).json()
            if not res.get("ok"):
                print(f"❌ ارور تلگرام: {res.get('description')}")
                return False
            return True
        else:
            files = {"photo": ("prompt.jpg", file_data, "image/jpeg")}
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام زیر 👇)", "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res_photo = requests.post(url, data=payload, files=files, timeout=25).json()
            if not res_photo.get("ok"):
                print(f"❌ ارور تلگرام (عکس): {res_photo.get('description')}")
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
        print(f"❌ استثنا در تلگرام: {e}")
        return False

def get_meigen_prompts():
    """
    استخراج آیتم‌ها از ساختار داده‌های فرانت‌اند meigen.ai
    """
    url = "https://www.meigen.ai/"
    items = []
    
    try:
        res = scraper.get(url, headers=HEADERS, timeout=20)
        if res.status_code != 200:
            print(f"❌ خطا در اتصال به meigen.ai: کد {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, 'html.parser')
        
        # ۱. بررسی دیتای Next.js در صورت وجود
        next_data_script = soup.find('script', id='__NEXT_DATA__')
        if next_data_script:
            try:
                data = json.loads(next_data_script.string)
                # جستجو در props برای پیدا کردن لیست تصاویر
                page_props = data.get('props', {}).get('pageProps', {})
                posts = page_props.get('prompts') or page_props.get('posts') or page_props.get('feed') or []
                for p in posts:
                    prompt = p.get('prompt') or p.get('promptText') or p.get('description')
                    img = p.get('imageUrl') or p.get('image') or p.get('url')
                    if prompt and img:
                        items.append({'prompt': prompt, 'image_url': img})
            except Exception as json_err:
                print(f"⚠️ ساختار __NEXT_DATA__ قابل پردازش نبود: {json_err}")

        # ۲. فال‌بک به اسکرپ مستقیم کارت‌ها در HTML در صورت عدم موفقیت مرحله قبل
        if not items:
            # meigen معمولاً کارت‌ها را با تصویر و ویژگی alt یا اتریبیوت‌های داده‌ای رندر می‌کند
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src', '')
                alt = img.get('alt', '').strip()
                
                # پرامپت‌ها معمولاً طولانی‌تر از تایتل‌های معمولی هستند
                if src and len(alt) > 25 and not any(ext in src for ext in ['logo', 'icon', 'avatar']):
                    if src.startswith('/'):
                        src = f"https://www.meigen.ai{src}"
                    items.append({
                        'prompt': alt,
                        'image_url': src
                    })

        # حذف موارد تکراری بر اساس متن پرامپت
        seen = set()
        unique_items = []
        for it in items:
            if it['prompt'] not in seen:
                seen.add(it['prompt'])
                unique_items.append(it)
                
        print(f"🔍 تعداد {len(unique_items)} پرامپت از meigen.ai استخراج شد.")
        return unique_items

    except Exception as e:
        print(f"❌ خطا در پردازش meigen.ai: {e}")
        return []

def test_telegram_connection():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("ok"):
            print(f"✅ بات متصل است: @{res['result']['username']}")
            return True
        print(f"❌ توکن نامعتبر: {res.get('description')}")
        return False
    except Exception as e:
        print(f"❌ اتصال به تلگرام ناموفق: {e}")
        return False

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ خطا: متغیر TELEGRAM_BOT_TOKEN مقداردهی نشده است.")
        exit(1)

    if not test_telegram_connection():
        exit(1)

    sent_prompts_set = load_sent_prompts()
    MAX_POSTS = 4
    new_count = 0

    print("🚀 شروع فرآیند...")
    prompts_pool = get_meigen_prompts()

    for item in prompts_pool:
        if new_count >= MAX_POSTS:
            break

        prompt_text = item['prompt']
        img_url = item['image_url']

        if prompt_text not in sent_prompts_set:
            new_count += 1
            print(f"📦 در حال ارسال [{new_count}/{MAX_POSTS}]...")
            
            if send_photo_file_to_telegram(img_url, prompt_text):
                print("✅ با موفقیت ارسال شد.")
                sent_prompts_set.add(prompt_text)
            else:
                print("❌ ارسال شکست خورد.")
            
            time.sleep(4)

    save_sent_prompts(sent_prompts_set)
    print(f"🏁 پایان کار. تعداد {new_count} پست ارسال گردید.")
