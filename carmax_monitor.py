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
TOP_N      = 5

# ─── FINANCE PARAMS ───────────────────────────────
FINANCE_APR        = 7.5    # % APR
FINANCE_TERM       = 72     # months
LEASE_MONEY_FACTOR = 0.0022 # ≈ 5.3% APR
LEASE_RESIDUAL_PCT   = 0.48   # 48% residual after 36 months
LEASE_TERM         = 36     # months
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


def calc_finance(price: int, down: int,
                 apr: float = FIMANXEE_APR, term: int = FINANCE_TERM) -> int:
    principal = price - down
    if principal <= 0:
        return 0
    r = apr / 100 / 12
    return round(principal * r * (1 + r)**term / ((1 + r)**term - 1))


def calc_lease(price: int, down: int = 0,
               residual_pct: float = LEASE_RESIDUAL_PCT,
               mf: float = LEASE_MONEY_FACTOR,
               term: int = LEASE_TERM) -> int:
    cap_cost = price - down
    residual  = price * residual_pct
    if cap_cost <= residual:
        return 0
    return round((cap_cost - residual) / term + (cap_cost + residual) * mf)


def score_car(car: dict) -> float:
    score = 0.0
    year = int(car["car"][:4])
    score += (year - 2021) * 40          # 2022→40, 2023→ 80
    score += max(0, 35 - car["milesNum"]) * 1.5
    score += max(0, (MAX_PRICE - car["priceNum"]) / 1000)
    if car["localStore"]:
        score += 15
    return round(score, 1)


async def scrape_model(page, model_slug: str, model_name: str) -> list:
    url = (
        f"https://www.carmax.com/cars/bmw/{model_slug}"
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


MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]


def format_message(cars: list) -> str:
    today = datetime.now().strftime("%-d %B %Y")
    sep   = "━" * 15

    if not cars:
        return (
            f"🚗 <b>BMW CarMax San Diego</b>\n"
            f"📄 {today}\n{sep}\n\n"
            f"😔 Сегоднс֧машин под критерии не найдено.\n\n"
            f"{sep}\n"
            f"Фильтры: {MIN_YEAR}-2023 · до ${MAX_PRICE:,} · до {MAX_MILES}K миль"
        )

    lines = [
        f"🚗 <b>BMW CarMax San Diego</b>  <i>(найдено: {len(cars)})</i>",
        f"📄 {today}",
        sep,
        "",
    ]

    for i, car in enumerate(cars):
        # Первые TOP_N — с медалями, остальныe — простой номер
        if i < TOP_N:
            prefix = MEDALS[i]
        else:
            prefix = f"{i+1}."

        delivery = "🚚Бесплатная доставка" if car["freeShip"] else "✅ Местный магазин"
        score    = car["score"]
        p        = car["priceNum"]

        fin5   = calc_finance(p, 5000)
        fin10  = calc_finance(p, 10000)
        lease0 = calc_lease(p, down=0)

        lines += [
            f"{prefix} <b>{car['car']}</b>  <i>(рейтинг: {score:.0f})</i>",
            f"💰 {car['price']} · 🛣 {car['miles']} миль",
            f"📍 {car['location']}",
            delivery,
            f"📊 Finans $5K↓ <b>~${fin5}/мес</b> · $10K↓ <b>~${fin10}/мес</b> · Лизинг <b>~${lease0}/мес</b>",
            f'✗ <a href="{car["url"]}">Смотреть на CarMax</a>',
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
        for slug, name in MODELS:
            print(f"Scraping BMW {name}...")
            try:
                results = await scrape_model(page, slug, name)
                print(f"  Found {len(results)} matching cars")
                all_cars.extend(results)
            except Exception as e:
                print(f"  Error: {e}")

        await browser.close()

    # Deduplicate by URL
    seen, unique = set(), []
    for car in all_cars:
        if car["url"] not in seen:
            seen.add(car["url"])
            unique.append(car)

    # Score and sort — best first
    for car in unique:
        car["score"] = score_car(car)
    unique.sort(key=lambda x: x["score"], reverse=True)

    print(f"\nTotal unique cars: {len(unique)}")
    for c in unique:
        print(f"  {c['score']:5.1f} | {c['car']} | {c['price']} | {c['miles']}")

    msg = format_message(unique)
    send_telegram(msg)
    print("Report sent to Telegram ✅")


if __name__ == "__main__":
    asyncio.run(main())
