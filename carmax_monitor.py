import asyncio
import base64
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = "8018807531:AAHhr1LXUUcGbvgoQK0EPEULDEeQdmp6PTA"
CHAT_ID        = "-5081882651"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = "vgsbus25"
GITHUB_REPO  = "carmax-monitor"
SEEN_FILE    = "seen_cars.json"

MODELS = [
    ("bmw",  "x5", "BMW",  "X5"),
    ("bmw",  "x3", "BMW",  "X3"),
    ("bmw",  "x4", "BMW",  "X4"),
    ("audi", "q5", "Audi", "Q5"),
    ("audi", "q7", "Audi", "Q7"),
]

MAX_PRICE   = 40000
MAX_MILES   = 35
MIN_YEAR    = 2022
ZIP_CODE    = "92101"
TOP_N       = 5
RESEND_DAYS = 7   # повторно показывать лот через N дней

FINANCE_APR        = 7.5
FINANCE_TERM       = 72
LEASE_MONEY_FACTOR = 0.0022
LEASE_RESIDUAL_PCT = 0.48
LEASE_TERM         = 36
# ──────────────────────────────────────────────────────────

TG_LIMIT = 4000
PDT      = timezone(timedelta(hours=-7))
MEDALS   = ["\U0001f947", "\U0001f948", "\U0001f949", "4️⃣", "5️⃣"]


# ─── SEEN-CARS STATE (GitHub repo file) ───────────────────

def _gh_headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }


def fetch_seen_cars() -> tuple[dict, str | None]:
    """Return (seen_dict, file_sha).  seen_dict = {url: iso_timestamp}."""
    if not GITHUB_TOKEN:
        print("  [seen] No GITHUB_TOKEN — skipping state load")
        return {}, None
    api_url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{SEEN_FILE}"
    )
    req = urllib.request.Request(api_url, headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("  [seen] seen_cars.json not found — starting fresh")
            return {}, None
        raise


def save_seen_cars(seen: dict, sha: str | None) -> None:
    """Commit updated seen_cars.json back to the repo."""
    if not GITHUB_TOKEN:
        print("  [seen] No GITHUB_TOKEN — skipping state save")
        return
    # Remove entries older than 30 days to keep file small
    cutoff_clean = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff_clean}

    content  = json.dumps(seen, indent=2, sort_keys=True)
    encoded  = base64.b64encode(content.encode("utf-8")).decode("ascii")
    api_url  = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{SEEN_FILE}"
    )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload: dict = {
        "message": f"chore: update seen_cars [{ts}]",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(api_url, data=data,
                                  headers=_gh_headers(), method="PUT")
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    print(f"  [seen] Saved {len(seen)} entries → "
          f"commit {resp['commit']['sha'][:8]}")


def filter_eligible(cars: list, seen: dict) -> list:
    """Keep only cars that are new or were last reported >= RESEND_DAYS ago."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RESEND_DAYS)).isoformat()
    result = []
    for car in cars:
        last = seen.get(car["url"])
        if last is None or last <= cutoff:
            car["is_new"] = (last is None)
            result.append(car)
    return result


# ─── TELEGRAM ─────────────────────────────────────────────

def send_telegram(text: str):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text":    text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def send_long_message(text: str):
    """Split by car blocks if message exceeds Telegram limit."""
    if len(text) <= TG_LIMIT:
        send_telegram(text)
        return
    blocks = text.split("\n\n")
    chunk  = ""
    for block in blocks:
        candidate = chunk + ("\n\n" if chunk else "") + block
        if len(candidate) > TG_LIMIT:
            if chunk:
                send_telegram(chunk.strip())
            chunk = block
        else:
            chunk = candidate
    if chunk:
        send_telegram(chunk.strip())


# ─── FINANCE ──────────────────────────────────────────────

def calc_finance(price: int, down: int) -> int:
    principal = price - down
    if principal <=�
        return 0
    r = FINANCE_APR / 100 / 12
    return round(principal * r * (1 + r)**FINANCE_TERM / ((1 + r)**FINANCE_TERM - 1))


def calc_lease(price: int) -> int:
    residual = price * LEASE_RESIDUAL_PCT
    return round((price - residual) / LEASE_TERM + (price + residual) * LEASE_MONEY_FACTOR)


# ─── SCORING ──────────────────────────────────────────────

def score_car(car: dict) -> float:
    score = 0.0
    year  = int(car["car"][:4])
    score += (year - 2021) * 40
    score += max(0, 35 - car["milesNum"]) * 1.5
    score += max(0, (MAX_PRICE - car["priceNum"]) / 1000)
    if car["localStore"]:
        score += 15
    return round(score, 1)


# ─── SCRAPING ─────────────────────────────────────────────

async def scrape_model(page, make_slug: str, model_slug: str,
                       make_name: str, model_name: str) -> list:
    url = (
        f"https://www.carmax.com/cars/{make_slug}/{model_slug}"
        f"?zip={ZIP_CODE}&price={MAX_PRICE}&year={MIN_YEAR}-2023&sortby=price-asc"
    )
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        await page.goto(url, timeout=30000)

    for _ in range(6):
        await page.evaluate("window.scrollBy(0, 1500)")
        await asyncio.sleep(0.4)
    await asyncio.sleep(2)

    results = await page.evaluate(f"""() => {{
        const makeName  = '{make_name}';
        const modelName = '{model_name}';
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href*="/car/"]').forEach(a => {{
            if (seen.has(a.href)) return;
            let el = a;
            for (let i = 0; i < 8; i++) {{
                el = el?.parentElement;
                if (!el) break;
                const t = el.innerText || '';
                if (t.includes(makeName) && t.includes(modelName) && t.includes('$') && t.length < 700) {{
                    const yearM  = t.match(/(202\\d) {make_name}\\s*({model_name}[^\\n]*)/);
                    const priceM = t.match(/\\$([\\d,]+)\\*/);
                    const milesM = t.match(/([\\d]+)K mi/);
                    const storeM = t.match(/CarMax ([^\\n,]+(?:,\\s*[A-Z]{{2}})?)/);
                    const freeShip   = t.includes('Free Shipping');
                    const localStore = /Kearny|El Cajon|Escondido|Oceanside/.test(t);
                    if (yearM && priceM) {{
                        const milesNum = milesM ? parseInt(milesM[1]) : 999;
                        const priceNum = parseInt(priceM[1].replace(',', ''));
                        if (
                            parseInt(yearM[1]) >= {MIN_YEAR} &&
                            milesNum <= {MAX_MILES} &&
                            priceNum <= {MAX_PRICE} &&
                            (freeShip || localStore)
                        ) {{
                            results.push({{
                                url:        a.href,
                                car:        yearM[1] + ' {make_name} ' + yearM[2].trim().split('\\n')[0],
                                price:      '$' + priceM[1],
                                priceNum,
                                miles:      milesM ? milesM[1] + 'K' : '?',
                                milesNum,
                                location:   storeM ? storeM[1].trim() : 'unknown',
                                freeShip,
                                localStore,
                            }});
                            seen.add(a.href);
                        }}
                    }}
                    break;
                }}
            }}
        }});
        return results;
    }}""")
    return results or []


# ─── MESSAGE FORMATTING ───────────────────────────────────

def get_greeting() -> str:
    hour = datetime.now(PDT).hour
    if 5 <= hour < 12:
        return (
            "\U0001f305 Доброе утро, Виталий и Светлана!\n"
            "Пусть это утро принесёт вам отличные находки! \U0001f340"
        )
    elif 12 <= hour < 18:
        return (
            "☀️ Добрый день, Виталий и Светлана!\n"
            "Желаем хорошего настроения и удачных покупок! \U0001f600"
        )
    elif 18 <= hour < 23:
        return (
            "\U0001f306 Добрый вечер, Виталий и Светлана!\n"
            "Надеемся, вечерний обзор вас порадует! \U0001f31f"
        )
    else:
        return (
            "\U0001f319 Доброй ночи, Виталий и Светлана!\n"
            "Пусть завтра найдётся идеальное авто! ✨"
        )


def format_message(eligible: list, total_scraped: int) -> str:
    today    = datetime.now().strftime("%-d %B %Y")
    sep      = "━" * 15
    greeting = get_greeting()

    if not eligible:
        return (
            f"{greeting}\n\n"
            f"\U0001f697 <b>BMW + Audi CarMax San Diego</b>\n"
            f"\U0001f4c5 {today}\n{sep}\n\n"
            f"\U0001f614 Новых машин под критерии не найдено.\n"
            f"(всего найдено: {total_scraped})\n\n"
            f"{sep}\n"
            f"Фильтры: "
            f"{MIN_YEAR}-2023 · до ${MAX_PRICE:,} "
            f"· до {MAX_MILES}K миль"
        )

    top = eligible[:TOP_N]

    lines = [
        greeting,
        "",
        (
            f"\U0001f697 <b>BMW + Audi CarMax San Diego</b>  "
            f"<i>(новых: {len(eligible)}, показываю ТОП-{min(TOP_N, len(eligible))})</i>"
        ),
        f"\U0001f4c5 {today}",
        sep,
        "",
    ]

    for i, car in enumerate(top):
        prefix   = MEDALS[i]
        delivery = (
            "\U0001f69a Бесплатная доставка"
            if car["freeShip"] else
            "✅ Местный магазин"
        )
        new_badge = " \U0001f195" if car.get("is_new") else " \U0001f504"
        p = car["priceNum"]

        lines += [
            f"{prefix} <b>{car['car']}</b>{new_badge}  <i>(рейтинг: {car['score']:.0f})</i>",
            f"\U0001f4b0 {car['price']} · \U0001f6e3 {car['miles']} миль",
            f"\U0001f4cd {car['location']}",
            delivery,
            (
                f"\U0001f4ca Финанс $5K↓ <b>~${calc_finance(p, 5000)}/мес</b> · "
                f"$10K↓ <b>~${calc_finance(p, 10000)}/мес</b> · "
                f"Лизинг <b>~${calc_lease(p)}/мес</b>"
            ),
            f'\U0001f517 <a href="{car["url"]}">Смотреть на CarMax</a>',
            "",
        ]

    lines += [
        sep,
        f"Фильтры: {MIN_YEAR}-2023 · до ${MAX_PRICE:,} · до {MAX_MILES}K миль",
    ]
    return "\n".join(lines)


# ─── MAIN ─────────────────────────────────────────────────

async def main():
    # 1. Scrape
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        all_cars = []
        for make_slug, model_slug, make_name, model_name in MODELS:
            print(f"Scraping {make_name} {model_name}...")
            try:
                results = await scrape_model(
                    page, make_slug, model_slug, make_name, model_name
                )
                print(f"  Found {len(results)} matching cars")
                all_cars.extend(results)
            except Exception as e:
                print(f"  Error: {e}")

        await browser.close()

    # 2. Deduplicate
    seen_urls, unique = set(), []
    for car in all_cars:
        if car["url"] not in seen_urls:
            seen_urls.add(car["url"])
            unique.append(car)

    # 3. Score
    for car in unique:
        car["score"] = score_car(car)
    unique.sort(key=lambda x: x["score"], reverse=True)

    total_scraped = len(unique)
    print(f"\nTotal unique cars scraped: {total_scraped}")
    for c in unique:
        print(f"  {c['score']:5.1f} | {c['car']} | {c['price']} | {c['miles']}")

    # 4. Load seen state
    print("\nLoading seen_cars state...")
    seen_data, seen_sha = fetch_seen_cars()
    print(f"  Loaded {len(seen_data)} previously seen cars")

    # 5. Filter: keep new or unseen for 7+ days
    eligible = filter_eligible(unique, seen_data)
    print(f"\nEligible (new or 7d+): {len(eligible)}")
    for c in eligible:
        label = "NEW" if c.get("is_new") else "RESEND"
        print(f"  [{label}] {c['score']:5.1f} | {c['car']} | {c['price']}")

    # 6. Format & send
    msg = format_message(eligible, total_scraped)
    print(f"\nMessage length: {len(msg)} chars")
    send_long_message(msg)
    print("Report sent to Telegram ✅")

    # 7. Update seen state for cars shown in this report
    now_ts = datetime.now(timezone.utc).isoformat()
    for car in eligible[:TOP_N]:
        seen_data[car["url"]] = now_ts

    # 8. Save updated state
    print("\nSaving seen_cars state...")
    save_seen_cars(seen_data, seen_sha)


if __name__ == "__main__":
    asyncio.run(main())
