import requests
import json
import time
import os
import re
import hashlib

# ============================== تنظیمات ==============================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MEIGEN_API_TOKEN = os.environ.get("MEIGEN_API_TOKEN")  # اختیاری ولی توصیه می‌شود
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json"
MAX_HISTORY_LIMIT = 50000

MAX_POSTS_PER_RUN = 10        # تعداد پست در هر اجرا
SEND_DELAY_SECONDS = 4        # فاصله بین ارسال‌ها
PAGES_PER_QUERY = 6           # هر صفحه ۳ نتیجه دارد
EXTRA_BUFFER = 5              # چند آیتم اضافه برای جبران ارسال‌های ناموفق
SKIP_REFERENCE_PROMPTS = True # پرامپت‌هایی که به عکس آپلودی نیاز دارند رد شوند
ONLY_COMMUNITY = False        # اگر True شود فقط آیتم‌های community_ ارسال می‌شوند

MCP_URL = "https://www.meigen.ai/api/mcp"

SEARCH_QUERIES = [
    "portrait", "product photography", "poster design", "illustration",
    "3D render", "anime", "logo", "infographic", "cinematic", "fashion",
    "sketchbook", "street style", "food photography", "architecture",
    "landscape", "character design", "cyberpunk", "minimalist", "vintage",
    "packaging", "typography", "interior design", "fantasy", "cartoon",
]

REFERENCE_RE = re.compile(
    r"\[image\d*\]|uploaded (reference )?(photo|image)|reference photo|attached (photo|image)",
    re.I,
)


# ============================== ابزارها ==============================
def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def make_unique_key(item_id, prompt_text):
    clean_prompt = " ".join(str(prompt_text).strip().split()).lower()
    raw = f"{str(item_id).strip()}::{clean_prompt}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def make_prompt_key(prompt_text):
    clean_prompt = " ".join(str(prompt_text).strip().split()).lower()
    return "p:" + hashlib.sha256(clean_prompt.encode('utf-8')).hexdigest()


def load_sent_prompts():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print(f"📖 سابقه خوانده شد: {len(set(data))} کلید ثبت شده است.")
                return set(data), list(data)
        except Exception as e:
            print(f"⚠️ خطا در خواندن سابقه: {e}")
            return set(), []
    print("📖 فایل سابقه‌ای وجود ندارد، ایجاد فایل جدید...")
    return set(), []


def save_sent_prompts(sent_list):
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sent_list[-MAX_HISTORY_LIMIT:], f, ensure_ascii=False, indent=2)


# ============================== کلاینت MCP ==============================
class MCPClient:
    def __init__(self, url, token=None):
        self.url = url
        self.session_id = None
        self.req_id = 0
        self.http = requests.Session()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "prompts-fa-bot/1.0",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _parse(self, res, req_id):
        ctype = res.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            last = None
            for line in res.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    last = msg
                    if msg.get("id") == req_id:
                        return msg
            return last
        return res.json()

    def rpc(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        req_id = None
        if not notify:
            self.req_id += 1
            req_id = self.req_id
            payload["id"] = req_id

        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        res = self.http.post(self.url, headers=headers, data=json.dumps(payload), timeout=60)
        if "Mcp-Session-Id" in res.headers:
            self.session_id = res.headers["Mcp-Session-Id"]
        if notify:
            return None
        if res.status_code != 200:
            raise RuntimeError(f"HTTP {res.status_code}: {res.text[:300]}")
        msg = self._parse(res, req_id)
        if msg and "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error']}")
        return (msg or {}).get("result")

    def initialize(self):
        self.rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "prompts-fa-bot", "version": "1.0"},
        })
        self.rpc("notifications/initialized", notify=True)

    def list_tools(self):
        return (self.rpc("tools/list", {}) or {}).get("tools", [])

    def call(self, name, arguments):
        return self.rpc("tools/call", {"name": name, "arguments": arguments})


def parse_payload(result):
    if not result:
        return {}
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for part in result.get("content", []):
        text = part.get("text") if isinstance(part, dict) else None
        if text:
            try:
                return json.loads(text)
            except Exception:
                continue
    return {}


# ============================== واکشی پرامپت‌ها ==============================
def fetch_prompts(sent_set):
    client = MCPClient(MCP_URL, MEIGEN_API_TOKEN)
    print(f"🔑 توکن MeiGen: {'تنظیم شده' if MEIGEN_API_TOKEN else 'تنظیم نشده (حالت بدون توکن)'}")

    try:
        client.initialize()
        tools = client.list_tools()
    except Exception as e:
        print(f"❌ اتصال به MCP ناموفق بود: {e}")
        return []

    print("🧰 ابزارها:", [t["name"] for t in tools])
    by_name = {t["name"]: t for t in tools}

    # برای عیب‌یابی: schema ابزارهایی که ممکن است متن کامل پرامپت بدهند
    for name in ("search_gallery", "get_inspiration", "browse_gallery"):
        if name in by_name:
            print(f"📐 schema «{name}»: {json.dumps(by_name[name].get('inputSchema'), ensure_ascii=False)[:1800]}")

    if "search_gallery" not in by_name:
        print("⚠️ ابزار search_gallery پیدا نشد.")
        return []

    needed = MAX_POSTS_PER_RUN + EXTRA_BUFFER
    all_items, seen = [], set()
    new_count = 0
    printed_entry_sample = False
    printed_raw_debug = False
    skipped_ref = skipped_dup = skipped_other = 0

    for q in SEARCH_QUERIES:
        if new_count >= needed:
            break
        offset, snapshot_id = 0, None

        for page in range(PAGES_PER_QUERY):
            if new_count >= needed:
                break

            args = {"query": q, "offset": offset}
            if snapshot_id:
                args["snapshotId"] = snapshot_id

            try:
                result = client.call("search_gallery", args)
            except Exception as e:
                print(f"   ❌ خطا برای «{q}» صفحه {page+1}: {e}")
                break

            data = parse_payload(result)
            entries = data.get("entries") or []

            if not entries and not printed_raw_debug:
                printed_raw_debug = True
                print("⚠️ پاسخ خام (بدون entry):", json.dumps(result, ensure_ascii=False)[:1500])

            if entries and not printed_entry_sample:
                printed_entry_sample = True
                print(f"🧬 فیلدهای یک entry: {list(entries[0].keys())}")
                print(f"🧬 نمونه entry: {json.dumps(entries[0], ensure_ascii=False)[:900]}")
                print(f"🧬 کلیدهای سطح بالای پاسخ: {[k for k in data.keys() if k != 'entries']}")

            page_new = 0
            for e in entries:
                prompt = e.get("promptExcerpt") or e.get("prompt")
                img = e.get("previewUrl")
                if not prompt or not img:
                    skipped_other += 1
                    continue
                if ONLY_COMMUNITY and not str(e.get("id", "")).startswith("community_"):
                    skipped_other += 1
                    continue
                if SKIP_REFERENCE_PROMPTS and REFERENCE_RE.search(prompt):
                    skipped_ref += 1
                    continue

                ukey = make_prompt_key(prompt)
                legacy = make_unique_key(e.get("id") or prompt, prompt)
                if ukey in seen:
                    skipped_dup += 1
                    continue
                seen.add(ukey)

                is_new = (ukey not in sent_set) and (legacy not in sent_set)
                if is_new:
                    new_count += 1
                    page_new += 1
                all_items.append({
                    "key": ukey,
                    "legacy_key": legacy,
                    "prompt": prompt,
                    "image_url": img,
                    "source_id": e.get("id", ""),
                    "is_new": is_new,
                })

            print(f"🔎 «{q}» صفحه {page+1} (offset {offset}) → {len(entries)} آیتم | "
                  f"جدید در این صفحه: {page_new} | جدید تا اینجا: {new_count}/{needed}")

            if not data.get("hasMore") or not entries:
                break
            offset = data.get("nextOffset", offset + len(entries))
            snapshot_id = data.get("snapshotId") or snapshot_id
            time.sleep(0.5)

    print(f"📊 واکشی تمام شد: کل={len(all_items)} | جدید={new_count} | "
          f"ردشده(مرجع آپلودی)={skipped_ref} | ردشده(تکراری در همین اجرا)={skipped_dup} | ردشده(سایر)={skipped_other}")
    if all_items:
        avg = sum(len(i["prompt"]) for i in all_items) // len(all_items)
        longest = max(len(i["prompt"]) for i in all_items)
        print(f"📏 طول متن پرامپت: میانگین={avg} | بیشینه={longest} کاراکتر")
    return all_items


# ============================== تلگرام ==============================
def send_photo_to_telegram(photo_url, prompt_text):
    safe_prompt = escape_html(prompt_text.strip())
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    long_text = len(caption) > 1000
    first_caption = "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام زیر 👇)" if long_text else caption

    try:
        # اول اجازه می‌دهیم خود تلگرام عکس را از URL بگیرد
        res = requests.post(f"{api}/sendPhoto", data={
            "chat_id": TELEGRAM_CHANNEL_ID, "photo": photo_url,
            "caption": first_caption, "parse_mode": "HTML",
        }, timeout=30).json()

        # اگر نشد، خودمان دانلود و آپلود می‌کنیم
        if not res.get("ok"):
            print(f"   ↪️ ارسال با URL ناموفق ({res.get('description')}), تلاش با دانلود مستقیم...")
            img = requests.get(photo_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            if img.status_code != 200:
                print(f"   ❌ دانلود عکس ناموفق: {img.status_code}")
                return False
            res = requests.post(f"{api}/sendPhoto", data={
                "chat_id": TELEGRAM_CHANNEL_ID, "caption": first_caption, "parse_mode": "HTML",
            }, files={"photo": ("prompt.jpg", img.content, "image/jpeg")}, timeout=40).json()

        if not res.get("ok"):
            print(f"   ❌ تلگرام: {res}")
            return False

        if long_text:
            msg_id = res["result"]["message_id"]
            res_text = requests.post(f"{api}/sendMessage", data={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": f"<code>{safe_prompt}</code>\n\n🔗 @prompts_fa",
                "parse_mode": "HTML",
                "reply_to_message_id": msg_id,
            }, timeout=25).json()
            if not res_text.get("ok"):
                print(f"   ❌ ارسال متن ناموفق: {res_text}")
            return res_text.get("ok", False)
        return True

    except Exception as e:
        print(f"   ❌ خطای تلگرام: {e}")
        return False


# ============================== اجرای اصلی ==============================
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    print(f"⚙️ تنظیمات: ارسال در هر اجرا={MAX_POSTS_PER_RUN} | رد پرامپت‌های مرجع={SKIP_REFERENCE_PROMPTS} | فقط community={ONLY_COMMUNITY}")

    sent_set, sent_list = load_sent_prompts()
    items = fetch_prompts(sent_set)

    if not items:
        print("⚠️ دیتایی دریافت نشد.")
        exit(0)

    new_items = [i for i in items if i["is_new"]]
    print(f"🔍 {len(items)} آیتم واکشی شد که {len(new_items)} تای آن جدید است.")

    sent_count = failed_count = 0

    for it in new_items:
        if sent_count >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) تکمیل شد.")
            break

        print(f"📦 ارسال [{sent_count + 1}/{MAX_POSTS_PER_RUN}] id={it['source_id']} | طول متن={len(it['prompt'])}")
        if send_photo_to_telegram(it["image_url"], it["prompt"]):
            print("   ✅ ارسال شد.")
            for k in (it["key"], it["legacy_key"]):
                sent_set.add(k)
                sent_list.append(k)
            sent_count += 1
            time.sleep(SEND_DELAY_SECONDS)
        else:
            failed_count += 1
            print("   ❌ ارسال ناموفق.")

    if sent_count > 0:
        save_sent_prompts(sent_list)

    print(f"🏁 پایان: ارسال‌شده={sent_count} | ناموفق={failed_count} | کل کلیدهای ذخیره‌شده={len(sent_list)}")
    if sent_count == 0:
        print("💤 هیچ پرامپت جدیدی ارسال نشد.")
