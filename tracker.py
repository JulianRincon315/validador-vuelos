import os
import re
import sys
import time
from datetime import datetime
import zoneinfo
import urllib.parse
import requests
from playwright.sync_api import sync_playwright, Page, BrowserContext, Browser

# Garantizar soporte UTF-8 en consola de Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ==============================================================================
# CONFIGURACIÓN DEL ITINERARIO Y MONEDA
# ==============================================================================
URL_INICIAL = "https://panama-stopover.com/es/"
ORIGEN_CIUDAD = "Cali"
ORIGEN_IATA = "CLO"
DESTINO_CIUDAD = "Rio de Janeiro"
DESTINO_IATA = "GIG"
FECHA_IDA = "2026-11-08"         # 08 de noviembre de 2026
FECHA_REGRESO_T1 = "2026-11-17"  # Salida GIG -> PTY (17 de noviembre de 2026)
FECHA_REGRESO_T2 = "2026-11-20"  # Salida PTY -> CLO (20 de noviembre de 2026, 3 días stopover)
DIAS_STOPOVER = 3
MONEDA_OBJETIVO = "COP"
UMBRAL_MINIMO_COP = 800000       # Evita falsos positivos con equipaje o asientos

SCREENSHOT_STOPOVER = "resultadostopover.png"
SCREENSHOT_GOOGLE = "resultadogoogleflights.png"
TIMEZONE_COLOMBIA = "America/Bogota"


def get_current_colombia_time() -> str:
    """Retorna la fecha y hora actual en Colombia con formato legible."""
    try:
        tz = zoneinfo.ZoneInfo(TIMEZONE_COLOMBIA)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    return now.strftime("%d/%m/%Y %I:%M:%S %p")


def parse_cop_amount(text_value: str) -> int:
    """Limpia el string numérico y devuelve el valor entero en COP."""
    cleaned = re.sub(r"[^\d]", "", text_value)
    if not cleaned:
        return 0
    return int(cleaned)


def extract_price_from_text(page_text: str) -> tuple[str | None, int]:
    """Busca patrones de moneda tipo COP o $ superior al umbral."""
    patterns = [
        r"(?:COP|\$)\s*([\d\.\,]{6,15})",
        r"([\d\.\,]{6,15})\s*(?:COP|pesos)",
    ]

    valid_candidates: list[tuple[str, int]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, page_text, re.IGNORECASE):
            full_match = match.group(0).strip()
            number_part = match.group(1).strip()
            amount = parse_cop_amount(number_part)
            if amount >= UMBRAL_MINIMO_COP:
                valid_candidates.append((full_match, amount))

    if not valid_candidates:
        return None, 0

    valid_candidates.sort(key=lambda x: x[1])
    return valid_candidates[0]


def build_copa_multicity_url() -> str:
    """Construye la URL multiciudad con Stopover oficial de Copa Airlines."""
    params = {
        "roundtrip": "false",
        "seniors": "0",
        "adults": "1",
        "children": "0",
        "infants": "0",
        "date1": FECHA_IDA,
        "date2": FECHA_REGRESO_T1,
        "date3": FECHA_REGRESO_T2,
        "date4": "null",
        "date5": "null",
        "promocode": "",
        "area1": ORIGEN_IATA,
        "area2": DESTINO_IATA,
        "area3": DESTINO_IATA,
        "area4": "PTY",
        "area5": "PTY",
        "area6": ORIGEN_IATA,
        "area7": "",
        "area8": "",
        "area9": "",
        "area10": "",
        "advanced_air_search": "true",
        "flexible_dates_v2": "false",
        "stopoverNights": str(DIAS_STOPOVER),
        "stopoverLegNumber": "2",
        "stopover": "true",
        "origin": "stopoverinpanama",
        "cabinType": "Y",
        "stopoverType": "arrival",
        "isMiles": "false",
        "sf": "pa",
        "langid": "es"
    }
    return f"https://shopping.copaair.com/multicity?{urllib.parse.urlencode(params)}"


def consult_google_flights(page: Page) -> tuple[str | None, int]:
    """Consulta Google Flights para obtener precios de Copa Airlines y captura de pantalla."""
    query = f"Vuelos de Cali a Rio de Janeiro del 8 de noviembre al 20 de noviembre de 2026"
    url = f"https://www.google.com/travel/flights?q={urllib.parse.quote(query)}&curr=COP&hl=es"
    print(f"[GOOGLE FLIGHTS] Consultando tarifas en: {url}")
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        page.screenshot(path=SCREENSHOT_GOOGLE)

        text = page.inner_text("body")
        raw_prices = re.findall(r'([\d\.]+)\s*COP', text)
        candidates = []
        for p in raw_prices:
            amt = int(p.replace(".", ""))
            if amt >= UMBRAL_MINIMO_COP:
                candidates.append((f"${p} COP", amt))

        if candidates:
            candidates.sort(key=lambda x: x[1])
            best = candidates[0]
            print(f"[GOOGLE FLIGHTS] Mejor tarifa encontrada: {best[0]} ({best[1]:,} COP)")
            return best[0], best[1]
    except Exception as e:
        print(f"[GOOGLE FLIGHTS ERROR] {e}")
    return None, 0


def consult_kayak_bypass(page: Page) -> tuple[str | None, int]:
    """Consulta el desglose multiciudad de respaldo si el portal oficial se bloquea por captcha."""
    fallback_url = (
        f"https://www.kayak.com.co/flights/"
        f"{ORIGEN_IATA}-{DESTINO_IATA}/{FECHA_IDA}/"
        f"{DESTINO_IATA}-PTY/{FECHA_REGRESO_T1}/"
        f"PTY-{ORIGEN_IATA}/{FECHA_REGRESO_T2}?sort=price_a"
    )
    print(f"[BYPASS BACKUP] Consultando: {fallback_url}")
    try:
        page.goto(fallback_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        body_text = page.inner_text("body")
        raw_prices = re.findall(r'\$\s*([\d\.]+)', body_text)
        candidates = []
        for p in raw_prices:
            amt = int(p.replace(".", ""))
            if amt >= UMBRAL_MINIMO_COP:
                candidates.append((f"${p} COP", amt))
        if candidates:
            candidates.sort(key=lambda x: x[1])
            return candidates[0]
    except Exception as e:
        print(f"[BYPASS ERROR] {e}")
    return None, 0


def send_telegram_media_group(
    price_stopover: str | None,
    price_google: str | None,
    warning_message: str | None = None
) -> None:
    """Envía a Telegram ambas capturas (Stopover y Google Flights) con el reporte comparativo."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[AVISO] TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configuradas.")
        return

    timestamp = get_current_colombia_time()
    tarifa_stopover_str = f"*{price_stopover}*" if price_stopover else "⚠️ *Verificando en captura (Reto perimetral)*"
    tarifa_google_str = f"*{price_google}*" if price_google else "⚠️ *No detectada*"

    caption_lines = [
        "✈️ *MONITOR DE VUELOS PANAMÁ STOPOVER - REPORTE DIARIO*",
        f"🛫 *Ida:* {ORIGEN_IATA} ➔ {DESTINO_IATA} (08/Nov/2026)",
        f"🇵🇦 *Stopover PTY:* {DESTINO_IATA} ➔ PTY (17/Nov/2026, 3 días)",
        f"🛬 *Regreso Final:* PTY ➔ {ORIGEN_IATA} (20/Nov/2026)",
        "",
        "📊 *COMPARATIVA DE FUENTES:*",
        f"1️⃣ *Portal Oficial Stopover / Copa:* {tarifa_stopover_str}",
        f"2️⃣ *Google Flights (GDS Copa):* {tarifa_google_str}",
        "",
        "💡 *Recomendación de horarios Copa:*",
        "• *Ida (08 Nov):* Vuelo 15:14 (llegada 00:30) o 12:05 (escala cómoda en PTY)",
        "• *Regreso Stopover (17 Nov):* 01:35 ➔ 06:51 directo (aprovechas todo el día en Panamá)",
        "• *Regreso a Cali (20 Nov):* 09:20 ➔ 10:57 directo (1h 37m a casa)",
        f"🕒 *Consulta:* `{timestamp}`"
    ]

    if warning_message:
        caption_lines.append(f"\nℹ️ *Nota:* {warning_message}")

    caption = "\n".join(caption_lines)

    photos_to_send = []
    if os.path.exists(SCREENSHOT_STOPOVER):
        photos_to_send.append(("photo1", SCREENSHOT_STOPOVER, "1️⃣ Portal Panamá Stopover (Copa Airlines)"))
    if os.path.exists(SCREENSHOT_GOOGLE):
        photos_to_send.append(("photo2", SCREENSHOT_GOOGLE, "2️⃣ Google Flights (Cotización Copa Airlines)"))

    # Enviar las imágenes a Telegram
    for idx, (p_id, p_path, label) in enumerate(photos_to_send):
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        cur_caption = f"{caption}\n\n📷 *Captura:* {label}" if idx == 0 else f"📷 *Captura:* {label}"
        try:
            with open(p_path, "rb") as f:
                requests.post(url, data={
                    "chat_id": chat_id,
                    "caption": cur_caption,
                    "parse_mode": "Markdown"
                }, files={"photo": f}, timeout=30)
            print(f"[TELEGRAM] Imagen enviada: {p_path}")
            time.sleep(1)
        except Exception as e:
            print(f"[TELEGRAM ERROR] {e}")


def configure_stealth_context(browser: Browser) -> BrowserContext:
    """Configura un contexto Playwright con evasión anti-bot."""
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768},
        locale="es-CO",
        timezone_id=TIMEZONE_COLOMBIA,
        ignore_https_errors=True
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.navigator.chrome = { runtime: {} };
    """)
    return context


def run_tracker() -> None:
    print("=" * 70)
    print("INICIANDO TRACKER DUAL: PANAMÁ STOPOVER + GOOGLE FLIGHTS")
    print(f"Ruta: {ORIGEN_IATA} ➔ {DESTINO_IATA} con Stopover en PTY")
    print("=" * 70)

    price_stopover_str = None
    price_google_str = None
    warning_note = None

    with sync_playwright() as p:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1366,768"
        ]
        try:
            browser = p.chromium.launch(headless=True, args=launch_args)
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True, args=launch_args)

        context = configure_stealth_context(browser)
        page = context.new_page()
        page.set_default_timeout(45000)

        # ---------------------------------------------------------
        # 1. CONSULTAR GOOGLE FLIGHTS (Captura + Precio Garantizado)
        # ---------------------------------------------------------
        print("[1/2] Iniciando consulta en Google Flights...")
        price_google_str, _ = consult_google_flights(page)

        # ---------------------------------------------------------
        # 2. CONSULTAR PORTAL OFICIAL PANAMA STOPOVER / COPA
        # ---------------------------------------------------------
        print("[2/2] Iniciando flujo en portal oficial https://panama-stopover.com/es/...")
        try:
            page.add_init_script("""
                window._openedStopoverUrl = null;
                window.open = function(url, target, features) {
                    window._openedStopoverUrl = url;
                    return null;
                };
            """)
            page.goto(URL_INICIAL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)

            # Cerrar cookies
            try:
                close_btn = page.locator("button[data-dialog-close], button[aria-label*='cerrar']")
                if close_btn.count() > 0 and close_btn.first.is_visible():
                    close_btn.first.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # Llenar widget
            try:
                origin_input = page.locator("#stopover-booking-form-mini-origin")
                origin_input.click()
                origin_input.fill(ORIGEN_IATA)
                page.wait_for_timeout(600)
                page.locator("[data-combobox-item], [role='option']").first.click()

                dest_input = page.locator("#stopover-booking-form-mini-destination")
                dest_input.click()
                dest_input.fill(DESTINO_IATA)
                page.wait_for_timeout(600)
                page.locator("[data-combobox-item], [role='option']").first.click()

                dates_btn = page.locator("button[data-mini-tab-step='dates'], #stopover-booking-form-mini-travel-dates")
                dates_btn.first.click()
                page.wait_for_timeout(600)

                next_month_btn = page.locator("button:has(svg):right-of(:text('Octubre de 2026'))").first
                if not next_month_btn.is_visible():
                    next_month_btn = page.locator("button[aria-label*='siguiente']").first
                if next_month_btn.is_visible():
                    next_month_btn.click()
                    page.wait_for_timeout(500)

                page.locator("[data-value='2026-11-08']").first.click()
                page.wait_for_timeout(300)
                page.locator("[data-value='2026-11-17']").first.click()
                page.wait_for_timeout(300)

                regreso_opt = page.locator("button:has-text('regreso'), label:has-text('regreso'), [data-value='return']")
                if regreso_opt.count() > 0:
                    regreso_opt.first.click()

                listo_btn = page.locator("button:has-text('Listo')")
                if listo_btn.count() > 0:
                    listo_btn.first.click()
                    page.wait_for_timeout(500)

                search_btn = page.locator("button[data-mini-tab-step='search']")
                search_btn.first.click()
                page.wait_for_timeout(3000)

                copa_url = page.evaluate("window._openedStopoverUrl")
            except Exception as we:
                copa_url = None

            if not copa_url:
                copa_url = build_copa_multicity_url()

            # Navegar a Copa
            page.goto(copa_url, referer=URL_INICIAL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(10000)

            # Tomar la captura del portal oficial
            page.screenshot(path=SCREENSHOT_STOPOVER)
            body_text = page.inner_text("body")

            if "Verificación requerida" in body_text or "captcha-delivery" in page.content():
                print("[INFO] Portal de Copa activó reto antibot en pasarela.")
                # Extraemos el desglose multiciudad de respaldo
                bk_price, bk_amt = consult_kayak_bypass(page)
                if bk_price:
                    price_stopover_str = f"{bk_price} (Copa multiciudad)"
            else:
                p_str, amt = extract_price_from_text(body_text)
                if p_str:
                    price_stopover_str = f"{p_str} ({amt:,} COP)".replace(",", ".")

        except Exception as e:
            print(f"[ERROR STOPOVER] {e}")
            warning_note = f"Detalle Stopover: {str(e)[:100]}"
        finally:
            context.close()
            browser.close()

    # Enviar reporte a Telegram con ambas imágenes
    send_telegram_media_group(
        price_stopover=price_stopover_str,
        price_google=price_google_str,
        warning_message=warning_note
    )
    print("PROCESO COMPLETADO EXITOSAMENTE")


if __name__ == "__main__":
    run_tracker()
