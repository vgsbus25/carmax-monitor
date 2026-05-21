import asyncio
import json
import urllib.request
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = "8018807531:AAHhr1LXUUcGbvgoQK0EPEULDEeQdmp6PTA"
CHAT_ID        = "-5081882651"

MODELS = [
    ("bmw",  "x5", "BMW",  "X5"),
    ("bmw",  "x3", "BMW",  "X3"),
    ("bmw",  "x4", "BMW",  "X4"),
    ("audi", "q5", "Audi", "Q5"),
    ("audi", "q7", "Audi", "Q7"),
]

MAX_PRICE  = 40000
MAX_MILES  = 35
MIN_YEAR   = 2022
ZIP_CODE   = "92101"
TOP_N      = 5

FINANCE_APR        = 7.5
FINANCE_TERM       = 72
LEASE_MONEY_FACTOR = 0.0022
LEASE_RESIDUAL_PCT = 0.48
LEASE_TERM         = 36
# ──────────────────────────────────────────────────────────

TG_LIMIT = 4000   # Telegram max is 4096, оставляем запас


def send_telegram(text: str):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def send_long_message(text: str):
    """Split message by car blocks if it exceeds Telegram limit."""
    if len(text) <= TG_LIMIT:
        send_telegram(text)
        return

    # Split into blocks by empty line separator between cars
    blocks = text.split("\n\n")
    chunk  = ""
    part   = 1
    for block in blocks:
        candidate = chunk + ("\n\n" if chunk else "") + block
        if len(candidate) > TG_LIMIT:
            if chunk:
                send_telegram(chunk.strip())
                part += 1
            chunk = block
        else:
            chunk = candidate
    if chunk:
        send_telegram(chunk.strip())


def calc_finance(price: int, down: int) -> int:
    principal = price - down
    if principal <= 0:
        return 0
    r = FINANCE_APR / 100 / 12
    return round(principal * r * (1 + r)**FINANCE_TERM / ((1 + r)**FINANCE_TERM - 1))


def calc_lease(price: int) -> int:
    residual = price * LEASE_RESIDUAL_PCT
    return round((price - residual) / LEASE_TERM + (price + residual) * LEASE_MONEY_FACTOR)


def score_car(car: dict) -> float:
    score = 0.0
    year = int(car["car"][:4])
    score += (year - 2021) * 40
    score += max(0, 35 - car["milesNum"]) * 1.5
    score += max(0, (MAX_PRICE - car["priceNum"]) / 1000)
    if car["localStore"]:
        score += 15
    return round(score, 1)


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
        document.querySelectorAll('a[href*="/car/"]').forEach(a => {
            if (seen.has(a.href)) return;
            let el = a;
            for (let i = 0; i < 8; i++) {
                el = el?.parentElement;
                if (!el) break;
                const t = el.innerText || '';
                if (t.includes(makeName) && t.includes(modelName) && t.includes('$') && t.length < 700) {
                    const yearM  = t.match(/(202\\d) {make_name}\\s*({model_name}[^\\n]*)/);
                    const priceM = t.match(/\\$([\\d,]+)\\*/);
                    const milesM = t.match(/([\\d]+)K mi/);
                    const storeM = t.match(/CarMax ([^\\n,]+(?:,\\s*[A-Z]{{2}})?)/);
                    const freeShip   = t.includes('Free Shipping');
                    const localStore = /Kearny|El Cajon|Escondido|Oceanside/.test(t);
                    if (yearM && priceM) {
                        const milesNum = milesM ? parseInt(milesM[1]) : 999;
                        const priceNum = parseInt(priceM[1].replace(',', ''));
                        if (
                            parseInt(yearM[1]) >= {MIN_YEAR} &&
                            milesNum <= {MAX_MILES} &&
                            priceNum <= {MAX_PRICE} &&
                            (freeShip || localStore)
                        ) {
                            results.push({
                                url:        a.href,
                                car:        yearM[1] + ' {make_name} ' + yearM[2].trim().split('\\n')[0],
                                price:      '$' + priceM[1],
                                priceNum,
                                miles:      milesM ? milesM[1] + 'K' : '?',
                                milesNum,
                                location:   storeM ? storeM[1].trim() : 'unknown',
                                freeShip,
                                localStore,
                            });
                            seen.add(a.href);
                        }
                    }
                    break;
                }
            }
        });
        return results;
    }}""")
    return results or []


MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]


def format_message(cars: list) -> str:
    today = datetime.now().strftime("%-d %B %Y")
    sep   = "━" * 15

    if not cars:
        return (
            f"🚗 <b>BMW + Audi CarMax San Diego</b>\n"
            f"📅 {today}\n{sep}\n\n"
            f"😔 Сегодня машин под критерии не найдено.\n\n"
            f"{sep}\n"
            f"Фильтры: {MIN_YEAR}-2023 · до ${MAX_PRICE:,} · до {MAX_MILES}K миль"
        )

    lines = [
        f"🚗 <b>BMW + Audi CarMax San Diego</b>  <i>(найдено: {len(cars)})</i>",
        f"📅 {today}",
        sep,
        "",
    ]

    for i, car in enumerate(cars):
        prefix   = MEDALS[i] if i < TOP_N else f"{i+1}."
        delivery = "🚚 Бесплатная доставка" if car["freeShip"] else "✅ Местный магазин"
        p        = car["priceNum"]

        lines += [
            f"{prefix} <b>{car['car']}</b>  <i>(рейтинг: {car['score']:.0f})</i>",
            f"💰 {car['price']} · 🛣 {car['miles']} миль",
            f"📍 {car['location']}",
            delivery,
            f"📊 Финанс $5K↓ <b>~${calc_finance(p,5000)}/мес</b> · "
            f"$10K↓ <b>~${calc_finance(p,10000)}/мес</b> · "
            f"Лизинг <b>~${calc_lease(p)}/мес</b>",
            f'🔗 <a href="{car["url"]}">Смотреть на CarMax</a>',
            "",
        ]

    lines += [
        sep,
        f"Фильтры: {MIN_YEAR}-2023 · до ${MAX_PRICE:,} · до {MAX_MILES}K миль",
    ]
    return "\n".join(lines)


async def main():
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
                results = await scrape_model(page, make_slug, model_slug, make_name, model_name)
                print(f"  Found {len(results)} matching cars")
                all_cars.extend(results)
            except Exception as e:
                print(f"  Error: {e}")

        await browser.close()

    seen, unique = set(), []
    for car in all_cars:
        if car["url"] not in seen:
            seen.add(car["url"])
            unique.append(car)

    for car in unique:
        car["score"] = score_car(car)
    unique.sort(key=lambda x: x["score"], reverse=True)

    print(f"\nTotal unique cars: {len(unique)}")
    for c in unique:
        print(f"  {c['score']:5.1f} | {c['car']} | {c['price']} | {c['miles']}")

    msg = format_message(unique)
    print(f"\nMessage length: {len(msg)} chars")
    send_long_message(msg)
    print("Report sent to Telegram ✅")


if __name__ == "__main__":
    asyncio.run(main())
