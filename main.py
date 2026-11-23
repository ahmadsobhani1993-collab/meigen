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

MAX_POSTS_PER_RUN = 10         # تعداد پست در هر اجرا
SEND_DELAY_SECONDS = 4         # فاصله بین ارسال‌ها
PAGES_PER_QUERY = 6            # هر صفحه ۳ نتیجه دارد
CANDIDATE_BUFFER = 12          # آیتم اضافه، چون بعد از گرفتن متن کامل بعضی‌ها رد می‌شوند
SKIP_REFERENCE_PROMPTS = True  # پرامپت‌هایی که به عکس آپلودی نیاز دارند رد شوند
REQUIRE_FULL_PROMPT = True     # اگر متن کامل پیدا نشد، آیتم ارسال نشود
ONLY_COMMUNITY = False         # اگر True شود فقط آیتم‌های community_ ارسال می‌شوند
TELEGRAM_CHUNK = 3000          # برای پرامپت‌های خیلی بلند (سقف تلگرام ۴۰۹۶ است)

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
    r"uploaded product",
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
        if text:
            try:
                return json.loads(text)
            except Exception:
                continue
    return {}


# ============================== واکشی کاندیداها ==============================
def fetch_candidates(client, sent_set):
    needed = MAX_POSTS_PER_RUN + CANDIDATE_BUFFER
    all_items, seen = [], set()
    new_count = 0
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
            entries = data.get("entries") or [] if isinstance(data, dict) else []

            if not entries and not printed_raw_debug and page == 0:
                printed_raw_debug = True
                print("⚠️ پاسخ خام (بدون entry):", json.dumps(result, ensure_ascii=False)[:800])

            page_new = 0
            for e in entries:
                excerpt = e.get("promptExcerpt") or e.get("prompt")
                img = e.get("previewUrl")
                item_id = e.get("id")
                if not excerpt or not img or not item_id:
                    skipped_other += 1
                    continue
                if ONLY_COMMUNITY and not str(item_id).startswith("community_"):
                    skipped_other += 1
                    continue
                if SKIP_REFERENCE_PROMPTS and REFERENCE_RE.search(excerpt):
                    skipped_ref += 1
                    continue

                # کلید بر اساس گزیده ساخته می‌شود (همان روش اجرای قبل، برای ثبات سابقه)
                ukey = make_prompt_key(excerpt)
                legacy = make_unique_key(item_id, excerpt)
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
                    "excerpt": excerpt,
                    "image_url": img,
                    "source_id": item_id,
                    "is_new": is_new,
                })

            print(f"🔎 «{q}» صفحه {page+1} (offset {offset}) → {len(entries)} آیتم | "
                  f"جدید در این صفحه: {page_new} | جدید تا اینجا: {new_count}/{needed}")

            if not isinstance(data, dict) or not data.get("hasMore") or not entries:
                break
            offset = data.get("nextOffset", offset + len(entries))
            snapshot_id = data.get("snapshotId") or snapshot_id
            time.sleep(0.5)

    print(f"📊 واکشی کاندیداها: کل={len(all_items)} | جدید={new_count} | "
          f"ردشده(مرجع آپلودی)={skipped_ref} | ردشده(تکراری)={skipped_dup} | ردشده(سایر)={skipped_other}")
    return all_items


# ============================== گرفتن متن کامل ==============================
_debug_shown = 0


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
    """متن کامل پرامپت را با get_inspiration می‌گیرد. اگر نشد None برمی‌گرداند."""
    global _debug_shown
    excerpt = item["excerpt"]
    try:
        result = client.call("get_inspiration", {"imageId": item["source_id"]})
    except Exception as e:
        print(f"   ❌ get_inspiration ناموفق: {e}")
        return None

    if _debug_shown < 2:
        _debug_shown += 1
        print(f"   🧪 پاسخ خام get_inspiration (نمونه {_debug_shown}): "
              f"{json.dumps(result, ensure_ascii=False)[:1500]}")

    payload = parse_payload(result)
    texts = []
    collect_texts(payload, texts)

    if texts:
        key, best = max(texts, key=lambda kv: len(kv[1]))
        best = best.strip()
        if len(best) >= len(excerpt) - 2:
            print(f"   📝 متن کامل از فیلد «{key}»: {len(best)} کاراکتر (گزیده: {len(excerpt)})")
            return best

    # گزیده‌های کوتاه‌تر از حد برش، احتمالاً خودشان کامل‌اند
    if len(excerpt) < 110:
        return excerpt
    return None


# ============================== تلگرام ==============================
def split_text(text, size):
    chunks, current = [], ""
    for para in text.split("\n"):
        while len(para) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:size])
            para = para[size:]
        if len(current) + len(para) + 1 > size:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def send_photo_to_telegram(photo_url, prompt_text):
    prompt_text = prompt_text.strip()
    safe_prompt = escape_html(prompt_text)
    caption = f"✨ <b>پرامپت جدید</b> ✨\n\n<code>{safe_prompt}</code>\n\n🔗 @prompts_fa"
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    long_text = len(caption) > 1000
    first_caption = "✨ <b>پرامپت جدید</b> ✨\n(متن کامل در پیام زیر 👇)" if long_text else caption

    try:
        res = requests.post(f"{api}/sendPhoto", data={
            "chat_id": TELEGRAM_CHANNEL_ID, "photo": photo_url,
            "caption": first_caption, "parse_mode": "HTML",
        }, timeout=30).json()

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
            reply_to = res["result"]["message_id"]
            chunks = split_text(prompt_text, TELEGRAM_CHUNK)
            for i, chunk in enumerate(chunks):
                suffix = "\n\n🔗 @prompts_fa" if i == len(chunks) - 1 else ""
                res_text = requests.post(f"{api}/sendMessage", data={
                    "chat_id": TELEGRAM_CHANNEL_ID,
                    "text": f"<code>{escape_html(chunk)}</code>{suffix}",
                    "parse_mode": "HTML",
                    "reply_to_message_id": reply_to,
                }, timeout=25).json()
                if not res_text.get("ok"):
                    print(f"   ❌ ارسال متن ناموفق: {res_text}")
                    return False
                time.sleep(1)
        return True

    except Exception as e:
        print(f"   ❌ خطای تلگرام: {e}")
        return False


# ============================== اجرای اصلی ==============================
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("❌ متغیر TELEGRAM_BOT_TOKEN تنظیم نشده است.")
        exit(1)

    print(f"⚙️ تنظیمات: ارسال در هر اجرا={MAX_POSTS_PER_RUN} | رد پرامپت‌های مرجع={SKIP_REFERENCE_PROMPTS} "
          f"| الزام متن کامل={REQUIRE_FULL_PROMPT} | فقط community={ONLY_COMMUNITY}")
    print(f"🔑 توکن MeiGen: {'تنظیم شده' if MEIGEN_API_TOKEN else 'تنظیم نشده (حالت بدون توکن)'}")

    sent_set, sent_list = load_sent_prompts()

    client = MCPClient(MCP_URL, MEIGEN_API_TOKEN)
    try:
        client.initialize()
        tools = client.list_tools()
    except Exception as e:
        print(f"❌ اتصال به MCP ناموفق بود: {e}")
        exit(0)

    tool_names = [t["name"] for t in tools]
    if "search_gallery" not in tool_names or "get_inspiration" not in tool_names:
        print(f"⚠️ ابزارهای لازم پیدا نشد. ابزارها: {tool_names}")
        exit(0)

    candidates = [i for i in fetch_candidates(client, sent_set) if i["is_new"]]
    if not candidates:
        print("⚠️ پرامپت جدیدی پیدا نشد.")
        exit(0)

    print(f"🔍 {len(candidates)} کاندیدای جدید. شروع گرفتن متن کامل و ارسال...")

    sent_count = failed_count = no_full_count = ref_after_count = 0

    for it in candidates:
        if sent_count >= MAX_POSTS_PER_RUN:
            print(f"🛑 سقف ارسال این نوبت ({MAX_POSTS_PER_RUN}) تکمیل شد.")
            break

        print(f"📦 بررسی id={it['source_id']}")
        full = fetch_full_prompt(client, it)
        time.sleep(0.3)

        if not full:
            no_full_count += 1
            if REQUIRE_FULL_PROMPT:
                print("   ⏭️ متن کامل پیدا نشد، رد شد (برای اجرای بعدی دوباره امتحان می‌شود).")
                continue
            full = it["excerpt"]

        if SKIP_REFERENCE_PROMPTS and REFERENCE_RE.search(full):
            ref_after_count += 1
            print("   ⏭️ پرامپت به عکس مرجع نیاز دارد، رد شد.")
            for k in (it["key"], it["legacy_key"]):  # دوباره بررسی نشود
                sent_set.add(k)
                sent_list.append(k)
            continue

        print(f"   📤 ارسال [{sent_count + 1}/{MAX_POSTS_PER_RUN}] | طول متن={len(full)}")
        if send_photo_to_telegram(it["image_url"], full):
            print("   ✅ ارسال شد.")
            for k in (it["key"], it["legacy_key"]):
                sent_set.add(k)
                sent_list.append(k)
            sent_count += 1
            time.sleep(SEND_DELAY_SECONDS)
        else:
            failed_count += 1
            print("   ❌ ارسال ناموفق.")

    save_sent_prompts(sent_list)

    print(f"🏁 پایان: ارسال‌شده={sent_count} | ناموفق={failed_count} | بدون متن کامل={no_full_count} "
          f"| رد (مرجع)={ref_after_count} | کل کلیدهای ذخیره‌شده={len(sent_list)}")
    if sent_count == 0:
        print("💤 هیچ پرامپتی ارسال نشد.")
