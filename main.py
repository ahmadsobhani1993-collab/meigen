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
POSTS_PER_RUN = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.meigen.ai/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
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
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sent_list[-1000:], f, ensure_ascii=False, indent=2)

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
        print(f"❌ خطای ارسال تلگرام: {e}")
        return False

def extract_prompt_from_detail(page_url):
    """استخراج مستقیم پرامپت بر اساس المان تایید شده در تصویر دونالد/اینسپکت"""
    try:
        res = scraper.get(page_url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"⚠️ صفحه جزئیات لود نشد {page_url} (کد {res.status_code})")
            return None, None

        soup = BeautifulSoup(res.text, 'html.parser')

        # ۱. بر اساس استایل دقیق اینسپکت شما:
        # <p dir="auto" class="... whitespace-pre-wrap ...">
        target_p = soup.find('p', class_=lambda c: c and 'whitespace-pre-wrap' in c)
        prompt_text = target_p.get_text(strip=True) if target_p else None

        # ۲. تصویر شاخص در صفحه مودال/جزئیات
        img_url = None
        for img in soup.find_all('img'):
            src = img.get('src') or ''
            if src and not any(x in src for x in ['avatar', 'icon', 'logo', 'data:image']):
                img_url = src if src.startswith('http') else f"https://www.meigen.ai{src}"
                break

        return prompt_text, img_url
    except Exception as e:
        print(f"⚠️ خطای دریافت صفحه جزئیات: {e}")
        return None, None

def inspect_and_crawl():
    print("🌐 در حال بررسی صفحه اصلی https://www.meigen.ai/ ...")
    res = scraper.get("https://www.meigen.ai/", headers=HEADERS, timeout=20)
    print(f"📡 کد وضعیت پاسخ: {res.status_code}")
    print(f"📄 طول محتوای HTML دریافتی: {len(res.text)} بایت")

    soup = BeautifulSoup(res.text, 'html.parser')

    # گزارش کلی از تگ‌های موجود در HTML خام دریافتی
    all_links = soup.find_all('a', href=True)
    all_imgs = soup.find_all('img')
    scripts = soup.find_all('script')
    print(f"📊 آمار اولیه صفحه خام: {len(all_links)} لینک، {len(all_imgs)} تگ تصویر، {len(scripts)} اسکریپت")

    # چاپ نمونه‌ای از لینک‌های پیدا شده برای ردیابی ساختار آدرس‌ها
    found_urls = set()
    sample_links = [a['href'] for a in all_links[:10]]
    print(f"🔎 نمونه لینک‌ها در صفحه: {sample_links}")

    # الگوهای احتمالی شناسه‌های پرامپت (UUID یا آدرس‌های اسلاگ)
    uuid_pattern = re.compile(r'(/[a-zA-Z0-9_-]+/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})|(/p/[0-9a-fA-F-]+)')
    for a in all_links:
        href = a['href']
        if uuid_pattern.search(href) or any(k in href for k in ['/prompt/', '/p/', '/post/']):
            full = href if href.startswith('http') else f"https://www.meigen.ai{href}"
            found_urls.add(full)

    # اگر از لینک‌های a پیدا نشد، بررسی کل متن HTML برای الگوهای UUID
    if not found_urls:
        print("🔍 جستجوی عبارات باقاعده در متن اسکریپت‌ها برای یافتن ID پست‌ها...")
        raw_matches = re.findall(r'/[a-zA-Z0-9_-]*/?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', res.text)
        print(f"🎯 تعداد {len(raw_matches)} الگو در دیتای خام پیدا شد.")
        for match in set(raw_matches):
            found_urls.add(f"https://www.meigen.ai{match}")

    return list(found_urls)

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    candidate_urls = inspect_and_crawl()
    print(f"📋 تعداد کل صفحات اختصاصی شناسایی‌شده: {len(candidate_urls)}")

    new_count = 0
    for url in candidate_urls:
        if new_count >= POSTS_PER_RUN:
            break
            
        if url in sent_set:
            continue

        print(f"⏳ بررسی پرامپت از: {url}")
        prompt, img = extract_prompt_from_detail(url)
        
        if prompt and img:
            print(f"📝 متن استخراج شد ({len(prompt)} کاراکتر). در حال ارسال به تلگرام...")
            if send_photo_file_to_telegram(img, prompt):
                print(f"✅ با موفقیت ارسال شد: {url}")
                sent_set.add(url)
                sent_list.append(url)
                new_count += 1
                time.sleep(4)
            else:
                print("❌ خطای ارسال تلگرام.")
        else:
            print(f"⚠️ المان پرامپت در این صفحه پیدا نشد.")

    if new_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {new_count} پرامپت با موفقیت ارسال شد.")
    else:
        print("💤 پستی ارسال نشد. لاگ‌های بالا را بررسی کنید.")
