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
MAX_HISTORY_LIMIT = 2000  # سقف ذخیره تاریخچه
POSTS_PER_RUN = 5        # تعداد پست ارسالی در هر نوبت اجرا (قابل تغییر)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/",
    "Accept": "application/json, text/plain, */*"
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
    trimmed_list = sent_list[-MAX_HISTORY_LIMIT:]
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(trimmed_list, f, ensure_ascii=False, indent=2)

def send_photo_file_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    
    try:
        img_response = requests.get(photo_url, headers=HEADERS, timeout=20)
        if img_response.status_code != 200:
            print(f"❌ خطای دانلود عکس: کد {img_response.status_code}")
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
            payload = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": "✨ <b>پرامپت جدید</b> ✨\n(متن در پیام بعد 👇)", "parse_mode": "HTML"}
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            res_photo = requests.post(url, data=payload, files=files, timeout=25).json()
            if not res_photo.get("ok"):
                print(f"❌ خطای تلگرام (عکس): {res_photo.get('description')}")
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
        print(f"❌ استثنا در ارسال تلگرام: {e}")
        return False

def fetch_all_meigen_prompts(max_pages=10):
    """
    دریافت تمام پرامپت‌ها از طریق API صفحه‌بندی‌شده و فال‌بک به صفحه اول
    """
    all_items = []
    seen = set()

    # تلاش ۱: بررسی اندپوینت‌های استاندارد API میگن برای صفحات قدیمی‌تر
    for page in range(1, max_pages + 1):
        api_url = f"https://www.meigen.ai/api/prompts?page={page}&limit=30"
        try:
            res = scraper.get(api_url, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                data = res.json()
                posts = data.get('data') or data.get('prompts') or data.get('items') or []
                if not posts and isinstance(data, list):
                    posts = data
                
                if not posts:
                    break
                    
                for p in posts:
                    prompt = p.get('prompt') or p.get('promptText') or p.get('description')
                    img = p.get('imageUrl') or p.get('image') or p.get('url')
                    if prompt and img and prompt not in seen:
                        seen.add(prompt)
                        all_items.append({'prompt': prompt, 'image_url': img})
            else:
                break
        except Exception:
            break

    # تلاش ۲: اگر API مستقیم پاسخ نداد، استخراج داده‌های صفحه اصلی با DOM و NextData
    if not all_items:
        try:
            res = scraper.get("https://www.meigen.ai/", headers=HEADERS, timeout=20)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # بررسی Next Data
                next_data = soup.find('script', id='__NEXT_DATA__')
                if next_data and next_data.string:
                    try:
                        data = json.loads(next_data.string)
                        page_props = data.get('props', {}).get('pageProps', {})
                        posts = page_props.get('prompts') or page_props.get('posts') or page_props.get('feed') or []
                        for p in posts:
                            prompt = p.get('prompt') or p.get('promptText') or p.get('description')
                            img = p.get('imageUrl') or p.get('image') or p.get('url')
                            if prompt and img and prompt not in seen:
                                seen.add(prompt)
                                all_items.append({'prompt': prompt, 'image_url': img})
                    except Exception:
                        pass

                # بررسی تگ‌های تصاویر در صورت خالی بودن دیتای قبلی
                if not all_items:
                    for img in soup.find_all('img'):
                        src = img.get('src') or img.get('data-src', '')
                        alt = img.get('alt', '').strip()
                        if src and len(alt) > 25 and not any(ext in src for ext in ['logo', 'icon', 'avatar']):
                            if src.startswith('/'):
                                src = f"https://www.meigen.ai{src}"
                            if alt not in seen:
                                seen.add(alt)
                                all_items.append({'prompt': alt, 'image_url': src})
        except Exception as e:
            print(f"❌ خطا در خواندن صفحه اصلی: {e}")

    print(f"🔎 مجموعاً {len(all_items)} پرامپت از سایت استخراج شد.")
    return all_items

def test_telegram_connection():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        res = requests.get(url, timeout=10).json()
        return res.get("ok", False)
    except Exception:
        return False

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not test_telegram_connection():
        print("❌ توکن تلگرام نامعتبر است.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    
    # واکشی تمام پرامپت‌ها
    prompts_pool = fetch_all_meigen_prompts(max_pages=10)

    # فیلتر کردن مواردی که هنوز ارسال نشده‌اند
    unsend_items = [item for item in prompts_pool if item['prompt'] not in sent_set]
    print(f"📋 تعداد کل پرامپت‌های ارسال‌نشده باقی‌مانده: {len(unsend_items)}")

    # اگر می‌خواهید از قدیمی‌ترین‌های ارسال‌نشده شروع کند:
    # unsend_items.reverse()

    new_count = 0
    for item in unsend_items:
        if new_count >= POSTS_PER_RUN:
            break

        prompt_text = item['prompt']
        img_url = item['image_url']

        new_count += 1
        print(f"📦 در حال ارسال [{new_count}/{POSTS_PER_RUN}]...")
        
        if send_photo_file_to_telegram(img_url, prompt_text):
            print("✅ با موفقیت ارسال شد.")
            sent_set.add(prompt_text)
            sent_list.append(prompt_text)
        else:
            print("❌ ارسال ناموفق.")
        
        time.sleep(5)

    if new_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {new_count} پرامپت با موفقیت ارسال شد.")
    else:
        print("💤 مورد ارسال‌نشده‌ای یافت نشد.")
