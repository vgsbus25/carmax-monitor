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
    ("x5", "X5"),
    ("x3", "X3"),
    ("x4", "X4"),
]

MAX_PRICE  = 40000
MAX_MILES  = 35   # K
MIN_YEAR   = 2022
ZIP_CODE   = "92101"
# ──────────────────────────────────────────────────────────


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


async def scrape_model(page, model_slug: str, model_name: str) -> list:
    url = (
        f"https://www.carmax.com/cars/bmw/{model_slug}"
        f"?zip={ZIP_CODE}&price={MAX_PRICE}&year={MIN_YEAR}-2023&sortby=price-asc"
    )
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        await page.goto(url, timeout=30000)

    # Scroll to trigger lazy loading
    for _ in range(6):
        await page.evaluate("window.scrollBy(0, 1500)")
        await asyncio.sleep(0.4)
    await asyncio.sleep(2)

    results = await page.evaluate(f"""() => {{
        const model = '{model_name}';
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href*="/car/"]').forEach(a => {{
            if (seen.has(a.href)) return;
            let el = a;
            for (let i = 0; i < 8; i++) {{
                el = el?.parentElement;
                if (!el) break;
                const t = el.innerText || '';
                if (t.includes('BMW') && t.includes(model) && t.includes('$') && t.length < 700) {{
                    const yearM  = t.match(/(202\\d) BMW\\s*(X[345][^\\n]*)/);
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
                                url:       a.href,
                                car:       yearM[1] + ' BMW ' + yearM[2].trim().split('\\n')[0],
                                price:     '$' + priceM[1],
                                priceNum,
                                miles:     milesM ? milesM[1] + 'K' : '?',
                                milesNum,
                                location:  storeM ? storeM[1].trim() : 'unknown',
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


def format_message(cars: list) -> str:
    today = datetime.now().strftime("%-d %B %Y")
    sep   = "━━━━━━━━━━━━━━━"

    if not cars:
        return (
            f"🚗 <b>BMW CarMax San Diego</b>\n"
            f"📅 {today}\n{sep}\n\n"
            f"😔 Сегодня машин под критерии не найдено.\n\n"
            f"{sep}\n"
            f"Фильтры: {MIN_YEAR}-2023 · до ${MAX_PRICE:,} · до {MAX_MILES}K миль"
        )

    lines = [f"🚗 <b>BMW CarMax San Diego</b>", f"📅 {today}", sep, ""]
    for i, car in enumerate(cars, 1):
        delivery = "🚚 Бесплатная доставка" if car["freeShip"] else "✅ Местный магазин"
        lines += [
            f"<b>{i}. {car['car']}</b>",
            f"💰 {car['price']} · 🦋 {car['miles']} миль",
            f"📍 {car['location']}",
            delivery,
            f'🔗 <a href="{car["url"]}">Смотреть на CarMax</a>',
            "",
        ]
    lines += [
        sep,
        f"Всего: {len(cars)} машин · {MIN_YEAR}-2023 · до ${MAX_PRICE:,} · до {MAX_MILES}K миль",
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
        for slug, name in MODELS:
            print(f"Scraping BMW {name}...")
            try:
                results = await scrape_model(page, slug, name)
                print(f"  Found {len(results)} matching cars")
                all_cars.extend(results)
            except Exception as e:
                print(f"  Error: {e}")

        await browser.close()

    # Deduplicate by URL, sort by price
    seen, unique = set(), []
    for car in all_cars:
        if car["url"] not in seen:
            seen.add(car["url"])
            unique.append(car)
    unique.sort(key=lambda x: x["priceNum"])

    print(f"\nTotal unique cars: {len(unique)}")

    msg = format_message(unique)
    send_telegram(msg)
    print("Report sent to Telegram ✅")


if __name__ == "__main__":
    asyncio.run(main())
