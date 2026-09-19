import cloudscraper
import requests
from bs4 import BeautifulSoup
import json
import io
import time
import os
import re

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json"
MAX_HISTORY_LIMIT = 1000
POSTS_PER_RUN = 3  # تعداد ارسالی در هر ساعت

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
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
    trimmed = sent_list[-MAX_HISTORY_LIMIT:]
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)

def send_photo_file_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    
    try:
        img_res = requests.get(photo_url, headers=HEADERS, timeout=20)
        if img_res.status_code != 200:
            print(f"❌ خطای دانلود تصویر: کد {img_res.status_code}")
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
        print(f"❌ ارور ارسال تلگرام: {e}")
        return False

def get_detail_page_prompt(detail_url):
    """باز کردن صفحه تکی هر پست و استخراج پرامپت کامل"""
    try:
        res = scraper.get(detail_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return None
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # ۱. بررسی متن در صورت وجود تگ اختصاصی یا متن کپی پرامپت
        for tag in soup.find_all(['p', 'div', 'span'], class_=lambda c: c and any(k in str(c).lower() for k in ['prompt', 'content', 'desc'])):
            text = tag.get_text(strip=True)
            if len(text) > 35 and not text.startswith("http"):
                return text
                
        # ۲. بررسی تگ‌های meta description
        meta = soup.find('meta', attrs={'property': 'og:description'}) or soup.find('meta', attrs={'name': 'description'})
        if meta and meta.get('content') and len(meta['content'].strip()) > 30:
            desc = meta['content'].strip()
            # فیلتر توضیحات پیش‌فرض سایت
            if "meigen" not in desc.lower() and "platform" not in desc.lower():
                return desc
                
        # ۳. جستجو در دیتای Next.js درون صفحه جزئیات
        next_tag = soup.find('script', id='__NEXT_DATA__')
        if next_tag and next_tag.string:
            data = json.loads(next_tag.string)
            props = data.get('props', {}).get('pageProps', {})
            for key in ['prompt', 'detail', 'data', 'post']:
                val = props.get(key)
                if isinstance(val, dict):
                    p_text = val.get('prompt') or val.get('promptText') or val.get('description')
                    if p_text: return p_text
                elif isinstance(val, str) and len(val) > 30:
                    return val

    except Exception as e:
        print(f"⚠️ خطا در واکشی جزئیات ({detail_url}): {e}")
    return None

def extract_cards_from_meigen():
    """استخراج لینک صفحات و تصاویر تمام کارت‌های صفحه اصلی"""
    cards = []
    seen_urls = set()
    
    url = "https://www.meigen.ai/"
    try:
        res = scraper.get(url, headers=HEADERS, timeout=20)
        if res.status_code != 200:
            print(f"❌ عدم دسترسی به صفحه اصلی: کد {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, 'html.parser')

        # الف) استخراج لینک‌ها بر اساس UUID های مشاهده شده در سایت
        uuid_pattern = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            if uuid_pattern.search(href) or '/p/' in href or '/prompt/' in href:
                full_url = href if href.startswith('http') else f"https://www.meigen.ai{href}"
                img = a.find('img')
                if img:
                    img_src = img.get('src') or img.get('data-src') or ''
                    if img_src and not any(x in img_src for x in ['avatar', 'icon', 'logo']):
                        if img_src.startswith('/'):
                            img_src = f"https://www.meigen.ai{img_src}"
                        if full_url not in seen_urls:
                            seen_urls.add(full_url)
                            cards.append({'page_url': full_url, 'image_url': img_src})

        # ب) اگر لینکی پیدا نشد، بررسی کل عکس‌های دارای ابعاد اصلی
        if not cards:
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src') or ''
                parent_a = img.find_parent('a', href=True)
                if parent_a and src and not any(x in src for x in ['avatar', 'icon', 'logo', 'data:image']):
                    p_url = parent_a['href']
                    full_p_url = p_url if p_url.startswith('http') else f"https://www.meigen.ai{p_url}"
                    img_full = src if src.startswith('http') else f"https://www.meigen.ai{src}"
                    if full_p_url not in seen_urls:
                        seen_urls.add(full_p_url)
                        cards.append({'page_url': full_p_url, 'image_url': img_full})

    except Exception as e:
        print(f"❌ خطای استخراج کارت‌ها: {e}")

    print(f"🔍 تعداد {len(cards)} کارت از صفحه اصلی کشف شد.")
    return cards

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
    
    # دریافت لیست کل کارت‌ها
    cards = extract_cards_from_meigen()
    
    new_count = 0
    for card in cards:
        if new_count >= POSTS_PER_RUN:
            break

        detail_url = card['page_url']
        
        # اگر قبلاً ارسال شده بود رد شو
        if detail_url in sent_set:
            continue

        # دریافت متن پرامپت از صفحه اختصاصی
        prompt_text = get_detail_page_prompt(detail_url)
        
        if prompt_text and prompt_text not in sent_set:
            new_count += 1
            print(f"📦 در حال ارسال [{new_count}/{POSTS_PER_RUN}]...")
            
            if send_photo_file_to_telegram(card['image_url'], prompt_text):
                print(f"✅ با موفقیت ارسال شد: {detail_url}")
                sent_set.add(detail_url)
                sent_set.add(prompt_text)
                sent_list.append(detail_url)
            else:
                print("❌ ارسال ناموفق به تلگرام.")
                
            time.sleep(5)

    if new_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {new_count} پرامپت جدید با موفقیت ارسال شد.")
    else:
        print("💤 مورد ارسال‌نشده جدیدی یافت نشد.")
