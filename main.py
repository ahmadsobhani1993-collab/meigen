import requests
import json
import time
import os
import re
import hashlib

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MEIGEN_API_TOKEN = os.environ.get("MEIGEN_API_TOKEN")  # اختیاری ولی توصیه می‌شود
TELEGRAM_CHANNEL_ID = "-1003790089817"
STATUS_FILE = "sent_prompts.json"
MAX_HISTORY_LIMIT = 50000
MAX_POSTS_PER_RUN = 5
MCP_URL = "https://www.meigen.ai/api/mcp"

# هر نوبت چند جستجو انجام می‌شود تا نتایج متنوع شود
SEARCH_QUERIES = [
    "portrait", "product photography", "poster design", "illustration",
    "3D render", "anime", "logo", "infographic", "cinematic", "fashion",
    "sketchbook", "street style", "food photography", "architecture",
]
RESULTS_PER_QUERY = 20

PROMPT_KEYS = ("prompt", "prompt_text", "promptText", "originalPrompt", "description", "caption")
IMAGE_KEYS = ("image_url", "imageUrl", "url", "image", "thumbnail", "thumbnailUrl", "previewUrl", "src")


# ---------------------------------------------------------------- utils
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
                print(f"📖 سابقه خوانده شد: {len(set(data))} پست قبلاً ثبت شده است.")
                return set(data), list(data)
        except Exception as e:
            print(f"⚠️ خطا در خواندن سابقه: {e}")
            return set(), []
    print("📖 فایل سابقه‌ای وجود ندارد، ایجاد فایل جدید...")
    return set(), []


def save_sent_prompts(sent_list):
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sent_list[-MAX_HISTORY_LIMIT:], f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- MCP client
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


def build_args(tool, query):
    """آرگومان‌ها را بر اساس schema واقعی ابزار می‌سازد."""
    props = (tool.get("inputSchema") or {}).get("properties", {})
    args = {}
    for k in props:
        kl = k.lower()
        if kl in ("query", "q", "keyword", "keywords", "search", "text"):
            args[k] = query
        elif kl in ("limit", "count", "max_results", "maxresults", "pagesize", "page_size", "n"):
            args[k] = RESULTS_PER_QUERY
    return args


# ---------------------------------------------------------------- parsing
def walk(obj):
    """همه dictهایی را که شبیه آیتم پرامپت هستند پیدا می‌کند."""
    if isinstance(obj, dict):
        prompt = next((obj[k] for k in PROMPT_KEYS if isinstance(obj.get(k), str) and len(obj[k]) > 20), None)
        img = None
        for k in IMAGE_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.startswith("http"):
                img = v
                break
            if isinstance(v, list) and v and isinstance(v[0], str) and v[0].startswith("http"):
                img = v[0]
                break
        if prompt and img:
            item_id = obj.get("id") or obj.get("_id") or obj.get("slug") or ""
            yield {"id": item_id, "prompt": prompt, "image_url": img}
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def extract_items(result):
    items = []
    if not result:
        return items
    if result.get("structuredContent"):
        items.extend(walk(result["structuredContent"]))
    for part in result.get("content", []):
        text = part.get("text") if isinstance(part, dict) else None
        if not text:
            continue
        try:
            items.extend(walk(json.loads(text)))
        except Exception:
            pass
    return items


def fetch_prompts():
    client = MCPClient(MCP_URL, MEIGEN_API_TOKEN)
    try:
        client.initialize()
        tools = client.list_tools()
    except Exception as e:
        print(f"❌ اتصال به MCP ناموفق بود: {e}")
        return []

    print("🧰 ابزارهای موجود:", [t["name"] for t in tools])
    tool = next((t for t in tools if t["name"] == "search_gallery"), None)
    if not tool:
        print("⚠️ ابزار search_gallery پیدا نشد. schema ابزارها:")
        print(json.dumps(tools, ensure_ascii=False, indent=2)[:3000])
        return []

    print("📐 schema ابزار:", json.dumps(tool.get("inputSchema"), ensure_ascii=False)[:600])

    all_items, seen = [], set()
    debug_printed = False

    for q in SEARCH_QUERIES:
        try:
            result = client.call("search_gallery", build_args(tool, q))
        except Exception as e:
            print(f"   ❌ خطا برای «{q}»: {e}")
            continue

        items = extract_items(result)
        print(f"🔎 «{q}» → {len(items)} آیتم")

        if not items and not debug_printed:
            debug_printed = True
            print("⚠️ پاسخ خام (برای عیب‌یابی):")
            print(json.dumps(result, ensure_ascii=False)[:1500])

        for it in items:
            ukey = make_prompt_key(it["prompt"])
            if ukey in seen:
                continue
            seen.add(ukey)
            all_items.append({
                "key": ukey,
                "legacy_key": make_unique_key(it["id"] or it["prompt"], it["prompt"]),
                "prompt": it["prompt"],
                "image_url": it["image_url"],
            })
        time.sleep(1)

    return all_items


# ---------------------------------------------------------------- telegram
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
            img = requests.get(photo_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            if img.status_code != 200:
                print(f"❌ دانلود عکس ناموفق: {img.status_code}")
                return False
            res = requests.post(f"{api}/sendPhoto", data={
                "chat_id": TELEGRAM_CHANNEL_ID, "caption": first_caption, "parse_mode": "HTML",
            }, files={"photo": ("prompt.jpg", img.content, "image/jpeg")}, timeout=40).json()

        if not res.get("ok"):
            print(f"❌ تلگرام: {res}")
            return False

        if long_text:
            msg_id = res["result"]["message_id"]
            res_text = requests.post(f"{api}/sendMessage", data={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": f"<code>{safe_prompt}</code>\n\n🔗 @prompts_fa",
                "parse_mode": "HTML",
                "reply_to_message_id": msg_id,
            }, timeout=25).json()
            return res_text.get("ok", False)
        return True

    except Exception as e:
        print(f"❌ خطای تلگرام: {e}")
        return False


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    sent_set, sent_list = load_sent_prompts()
    latest_items = fetch_prompts()

    if not latest_items:
        print("⚠️ دیتایی دریافت نشد.")
        exit(0)

    print(f"🔍 مجموعاً {len(latest_items)} پرامپت منحصر‌به‌فرد واکشی شد.")

    sent_count = 0
    for idx, it in enumerate(latest_items):
        if sent_count >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) تکمیل شد.")
            break

        if it["key"] in sent_set or it["legacy_key"] in sent_set:
            continue

        print(f"📦 [آیتم #{idx+1}] پرامپت جدید پیدا شد! ارسال...")
        if send_photo_to_telegram(it["image_url"], it["prompt"]):
            print("✅ با موفقیت ارسال شد.")
            for k in (it["key"], it["legacy_key"]):
                sent_set.add(k)
                sent_list.append(k)
            sent_count += 1
            time.sleep(4)
        else:
            print("❌ ارسال ناموفق.")

    if sent_count > 0:
        save_sent_prompts(sent_list)
        print(f"🏁 {sent_count} پرامپت ارسال و ذخیره شد.")
    else:
        print("💤 همه پرامپت‌های واکشی‌شده قبلاً ارسال شده بودند.")
