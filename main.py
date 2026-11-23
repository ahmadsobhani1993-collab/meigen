import requests
import json
import time
import os
import re
import hashlib

# ============================== تنظیمات ==============================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MEIGEN_API_TOKEN = os.environ.get("MEIGEN_API_TOKEN")  # اختیاری
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json"
MAX_HISTORY_LIMIT = 50000

MAX_POSTS_PER_RUN = 10          # تعداد پست در هر اجرا
MAX_CHECKS_PER_RUN = 80         # حداکثر تعداد پرامپتی که در یک اجرا بررسی می‌شود
SEND_DELAY_SECONDS = 4          # فاصله بین ارسال‌ها
PAGES_PER_QUERY = 6             # هر صفحه ۳ نتیجه دارد

MAX_PROMPT_CHARS = 3900         # بلندتر از این در یک حباب جا نمی‌شود → رد می‌شود
CAPTION_MAX_CHARS = 900         # تا این طول، متن داخل کپشن عکس می‌آید؛ بیشتر → پیام ریپلای
SKIP_REFERENCE_PROMPTS = True   # پرامپت‌هایی که به عکس آپلودی نیاز دارند رد شوند
ONLY_COMMUNITY = False          # اگر True شود فقط آیتم‌های community_

MCP_URL = "https://www.meigen.ai/api/mcp"

SEARCH_QUERIES = [
    "portrait", "product photography", "poster design", "illustration",
    "3D render", "anime", "logo", "infographic", "cinematic", "fashion",
    "sketchbook", "street style", "food photography", "architecture",
    "landscape", "character design", "cyberpunk", "minimalist", "vintage",
    "packaging", "typography", "interior design", "fantasy", "cartoon",
]

REFERENCE_RE = re.compile(
    r"\[image\d*\]|uploaded (reference )?(photo|image|product)|reference photo|"
    r"attached (photo|image)|upload a clear|upload (your|a|the) (product )?(image|photo)|"
    r"uploaded product|uploaded client",
    re.I,
)

FULL_PROMPT_KEYS = (
    "prompt", "fullPrompt", "full_prompt", "promptText", "prompt_text",
    "originalPrompt", "original_prompt", "content", "text", "promptExcerpt",
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


def mark_done(it, sent_set, sent_list):
    """آیتم را (ارسال‌شده یا ردشده) ثبت و فوراً ذخیره می‌کند."""
    for k in (it["key"], it["legacy_key"], it["id_key"], it["img_key"]):
        if k not in sent_set:
            sent_set.add(k)
            sent_list.append(k)
    save_sent_prompts(sent_list)


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
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    for part in result.get("content", []):
        text = part.get("text") if isinstance(part, dict) else None
        if not text:
            continue
        try:
            return json.loads(text)
        except Exception:
            # پاسخ get_inspiration یک جمله اول دارد و بعد JSON؛ از اولین { شروع می‌کنیم
            start = text.find("{")
            if start != -1:
                try:
                    return json.loads(text[start:])
                except Exception:
                    continue
    return {}


# ============================== کاندیداها (تنبل) ==============================
def iter_new_candidates(client, sent_set):
    """آیتم‌های جدید را یکی‌یکی و فقط به اندازه نیاز از جستجو بیرون می‌دهد."""
    seen = set()
    for q in SEARCH_QUERIES:
        offset, snapshot_id = 0, None

        for page in range(PAGES_PER_QUERY):
            args = {"query": q, "offset": offset}
            if snapshot_id:
                args["snapshotId"] = snapshot_id

            try:
                result = client.call("search_gallery", args)
            except Exception as e:
                print(f"   ❌ خطا برای جستجوی «{q}» صفحه {page+1}: {e}")
                break

            data = parse_payload(result)
            entries = (data.get("entries") or []) if isinstance(data, dict) else []
            print(f"🔎 «{q}» صفحه {page+1} (offset {offset}) → {len(entries)} آیتم")

            for e in entries:
                excerpt = e.get("promptExcerpt") or e.get("prompt")
                img = e.get("previewUrl")
                item_id = e.get("id")
                if not excerpt or not img or not item_id:
                    continue
                if ONLY_COMMUNITY and not str(item_id).startswith("community_"):
                    continue

                id_key = "id:" + str(item_id).strip()
                if id_key in seen:
                    continue
                seen.add(id_key)

                ukey = make_prompt_key(excerpt)
                legacy = make_unique_key(item_id, excerpt)
                img_key = "img:" + hashlib.sha256(img.encode()).hexdigest()

                if any(k in sent_set for k in (ukey, legacy, id_key, img_key)):
                    continue

                yield {
                    "key": ukey, "legacy_key": legacy, "id_key": id_key, "img_key": img_key,
                    "excerpt": excerpt, "image_url": img, "source_id": item_id,
                }

            if not isinstance(data, dict) or not data.get("hasMore") or not entries:
                break
            offset = data.get("nextOffset", offset + len(entries))
            snapshot_id = data.get("snapshotId") or snapshot_id
            time.sleep(0.5)


# ============================== متن کامل ==============================
def collect_texts(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in FULL_PROMPT_KEYS:
                out.append((k, v))
            else:
                collect_texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_texts(v, out)


def fetch_full_prompt(client, item):
    """متن کامل پرامپت را با get_inspiration می‌گیرد؛ در صورت شکست None."""
    try:
        result = client.call("get_inspiration", {"imageId": item["source_id"]})
    except Exception as e:
        print(f"   ❌ get_inspiration ناموفق: {e}")
        return None

    payload = parse_payload(result)

    # اولویت با فیلد «prompt»؛ فقط اگر نبود، بلندترین فیلد متنی
    if isinstance(payload, dict) and isinstance(payload.get("prompt"), str) and payload["prompt"].strip():
        return payload["prompt"].strip()

    texts = []
    collect_texts(payload, texts)
    if texts:
        return max(texts, key=lambda kv: len(kv[1]))[1].strip()

    print(f"   ⚠️ فیلد متن در پاسخ پیدا نشد. پاسخ خام: {json.dumps(result, ensure_ascii=False)[:800]}")
    return None


# ============================== تلگرام ==============================
def send_to_telegram(photo_url, prompt_text):
    """اگر متن کوتاه باشد داخل کپشن، وگرنه عکس + یک پیام ریپلای. همیشه حداکثر دو پیام."""
    prompt_text = prompt_text.strip()
    safe_prompt = escape_html(prompt_text)
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    in_caption = len(prompt_text) <= CAPTION_MAX_CHARS
    if in_caption:
        caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    else:
        caption = "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام زیر 👇)"

    try:
        res = requests.post(f"{api}/sendPhoto", data={
            "chat_id": TELEGRAM_CHANNEL_ID, "photo": photo_url,
            "caption": caption, "parse_mode": "HTML",
        }, timeout=30).json()

        if not res.get("ok"):
            print(f"   ↪️ ارسال با URL ناموفق ({res.get('description')}), تلاش با دانلود مستقیم...")
            img = requests.get(photo_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            if img.status_code != 200:
                print(f"   ❌ دانلود عکس ناموفق: {img.status_code}")
                return False
            res = requests.post(f"{api}/sendPhoto", data={
                "chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML",
            }, files={"photo": ("prompt.jpg", img.content, "image/jpeg")}, timeout=40).json()

        if not res.get("ok"):
            print(f"   ❌ تلگرام: {res}")
            return False

        if not in_caption:
            res_text = requests.post(f"{api}/sendMessage", data={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": f"<code>{safe_prompt}</code>\n\n🔗 @prompts_fa",
                "parse_mode": "HTML",
                "reply_to_message_id": res["result"]["message_id"],
            }, timeout=25).json()
            if not res_text.get("ok"):
                print(f"   ❌ ارسال متن ناموفق: {res_text}")
                return False
        return True

    except Exception as e:
        print(f"   ❌ خطای تلگرام: {e}")
        return False


# ============================== اجرای اصلی ==============================
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    print(f"⚙️ تنظیمات: پست در هر اجرا={MAX_POSTS_PER_RUN} | سقف بررسی={MAX_CHECKS_PER_RUN} | "
          f"سقف طول متن={MAX_PROMPT_CHARS} | حد کپشن={CAPTION_MAX_CHARS} | "
          f"رد مرجع={SKIP_REFERENCE_PROMPTS} | فقط community={ONLY_COMMUNITY}")
    print(f"🔑 توکن MeiGen: {'تنظیم شده' if MEIGEN_API_TOKEN else 'تنظیم نشده (حالت بدون توکن)'}")

    sent_set, sent_list = load_sent_prompts()

    client = MCPClient(MCP_URL, MEIGEN_API_TOKEN)
    try:
        client.initialize()
        tool_names = [t["name"] for t in client.list_tools()]
    except Exception as e:
        print(f"❌ اتصال به MCP ناموفق بود: {e}")
        exit(0)

    if "search_gallery" not in tool_names or "get_inspiration" not in tool_names:
        print(f"⚠️ ابزارهای لازم پیدا نشد. ابزارها: {tool_names}")
        exit(0)

    stats = {"sent": 0, "failed": 0, "no_full": 0, "ref": 0, "too_long": 0, "checked": 0}

    for it in iter_new_candidates(client, sent_set):
        if stats["sent"] >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) تکمیل شد.")
            break
        if stats["checked"] >= MAX_CHECKS_PER_RUN:
            print(f"🛑 سقف بررسی این نوبت ({MAX_CHECKS_PER_RUN}) تکمیل شد.")
            break
        stats["checked"] += 1

        print(f"📦 [{stats['checked']}] بررسی id={it['source_id']}")
        full = fetch_full_prompt(client, it)
        time.sleep(0.3)

        if not full:
            stats["no_full"] += 1
            print("   ⏭️ متن کامل گرفته نشد؛ ثبت نمی‌شود تا اجرای بعد دوباره امتحان شود.")
            continue

        if SKIP_REFERENCE_PROMPTS and REFERENCE_RE.search(full):
            stats["ref"] += 1
            print(f"   ⏭️ نیاز به عکس مرجع دارد (طول {len(full)})، رد شد.")
            mark_done(it, sent_set, sent_list)
            continue

        if len(full) > MAX_PROMPT_CHARS:
            stats["too_long"] += 1
            print(f"   ⏭️ بیش از حد بلند است ({len(full)} > {MAX_PROMPT_CHARS})، رد شد.")
            mark_done(it, sent_set, sent_list)
            continue

        mode = "کپشن" if len(full) <= CAPTION_MAX_CHARS else "عکس + یک پیام ریپلای"
        print(f"   📤 ارسال [{stats['sent'] + 1}/{MAX_POSTS_PER_RUN}] | طول={len(full)} | قالب: {mode}")
        if send_to_telegram(it["image_url"], full):
            print("   ✅ ارسال شد.")
            mark_done(it, sent_set, sent_list)
            stats["sent"] += 1
            time.sleep(SEND_DELAY_SECONDS)
        else:
            stats["failed"] += 1
            print("   ❌ ارسال ناموفق (ثبت نمی‌شود، بار بعد دوباره امتحان می‌شود).")

    print(f"🏁 پایان: ارسال‌شده={stats['sent']} | ناموفق={stats['failed']} | بررسی‌شده={stats['checked']} | "
          f"رد(مرجع)={stats['ref']} | رد(بیش از حد بلند)={stats['too_long']} | بدون متن کامل={stats['no_full']} | "
          f"کل کلیدهای ذخیره‌شده={len(sent_list)}")
    if stats["sent"] == 0:
        print("💤 هیچ پرامپتی ارسال نشد.")
