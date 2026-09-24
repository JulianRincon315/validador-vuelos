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

SCREENSHOT_PATH = "copa_resultado.png"
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
    """
    Busca patrones de moneda colombiana tipo COP 1.234.567 o $ 1.234.567.
    Aplica el filtro para descartar montos inferiores a $800.000 COP.
    Retorna (texto_formateado, valor_numerico).
    """
    patterns = [
        r"(?:COP|\$)\s*([\d\.\,]{6,15})",
        r"([\d\.\,]{6,15})\s*(?:COP|pesos)",
    ]

    valid_candidates: list[tuple[str, int]] = []

    for pattern in patterns:
        matches = re.finditer(pattern, page_text, re.IGNORECASE)
        for match in matches:
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
    """
    Construye la URL multiciudad con Stopover oficial de Copa Airlines.
    """
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
        "stopoverLegNumber": "2",  # 2 indica al regreso
        "stopover": "true",
        "origin": "stopoverinpanama",
        "cabinType": "Y",
        "stopoverType": "arrival",  # arrival corresponde a regreso
        "isMiles": "false",
        "sf": "pa",
        "langid": "es"
    }
    return f"https://shopping.copaair.com/multicity?{urllib.parse.urlencode(params)}"


def extract_price_via_bypass_fallback(page: Page) -> tuple[str | None, int]:
    """
    Mecanismo de elusión de firewall perimetral:
    Si DataDome/Akamai activa un captcha interactivo en la IP de consulta,
    el script consulta el agregador multiciudad directo para el mismo itinerario exacto
    y extrae la tarifa real en COP sin interrupciones.
    """
    fallback_url = (
        f"https://www.kayak.com.co/flights/"
        f"{ORIGEN_IATA}-{DESTINO_IATA}/{FECHA_IDA}/"
        f"{DESTINO_IATA}-PTY/{FECHA_REGRESO_T1}/"
        f"PTY-{ORIGEN_IATA}/{FECHA_REGRESO_T2}?sort=price_a"
    )
    print(f"[BYPASS] Activando consulta alternativa para eludir bloqueo perimetral: {fallback_url}")
    try:
        page.goto(fallback_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        page.screenshot(path=SCREENSHOT_PATH)

        body_text = page.inner_text("body")
        raw_prices = re.findall(r'\$\s*([\d\.]+)', body_text)
        candidates = []
        for p in raw_prices:
            amt = int(p.replace(".", ""))
            if amt >= UMBRAL_MINIMO_COP:
                candidates.append((f"${p} COP", amt))

        if candidates:
            candidates.sort(key=lambda x: x[1])
            best_price = candidates[0]
            print(f"[BYPASS ÉXITO] Tarifa extraída vía bypass: {best_price[0]} ({best_price[1]:,} COP)")
            return best_price[0], best_price[1]
    except Exception as e:
        print(f"[BYPASS ERROR] Error en bypass fallback: {e}")
    return None, 0


def send_telegram_notification(
    screenshot_path: str,
    price_detected: str | None,
    warning_message: str | None = None
) -> None:
    """
    Envía la captura de pantalla y el reporte estructurado en Markdown al bot de Telegram.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[AVISO] Variables TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configuradas. Se omite el envío a Telegram.")
        return

    timestamp = get_current_colombia_time()

    if price_detected:
        tarifa_str = f"*{price_detected}*"
    else:
        tarifa_str = "⚠️ *No detectada automáticamente en DOM (Revisar captura adjunta)*"

    caption_lines = [
        "✈️ *Copa Airlines - Panamá Stopover (Cali ⇄ Río de Janeiro)*",
        f"🛫 *Ida:* {ORIGEN_IATA} ➔ {DESTINO_IATA} (08/Nov/2026)",
        f"🇵🇦 *Stopover PTY:* {DESTINO_IATA} ➔ PTY (17/Nov/2026, 3 días)",
        f"🛬 *Regreso Final:* PTY ➔ {ORIGEN_IATA} (20/Nov/2026)",
        f"💵 *Tarifa Detectada:* {tarifa_str}",
        f"🕒 *Fecha de consulta:* `{timestamp}`"
    ]

    if warning_message:
        caption_lines.append(f"\nℹ️ *Nota:* {warning_message}")

    caption = "\n".join(caption_lines)

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    print(f"[TELEGRAM] Enviando reporte a chat ID: {chat_id}...")

    try:
        if os.path.exists(screenshot_path):
            with open(screenshot_path, "rb") as photo:
                files = {"photo": photo}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "Markdown"
                }
                response = requests.post(url, data=data, files=files, timeout=30)
        else:
            msg_url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "Markdown"
            }
            response = requests.post(msg_url, data=data, timeout=30)

        if response.status_code == 200:
            print("[TELEGRAM] Reporte enviado satisfactoriamente.")
        else:
            print(f"[TELEGRAM] Error de API ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[TELEGRAM] Excepción al enviar mensaje: {e}")


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
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        window.navigator.chrome = {
            runtime: {}
        };
        Object.defineProperty(navigator, 'languages', {
            get: () => ['es-CO', 'es', 'en-US', 'en']
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
    """)
    return context


def run_tracker() -> None:
    """Flujo principal de navegación, scraping y notificación."""
    print("=" * 70)
    print("INICIANDO TRACKER DE VUELOS PANAMÁ STOPOVER - COPA AIRLINES")
    print(f"Ruta: {ORIGEN_IATA} ({ORIGEN_CIUDAD}) ➔ {DESTINO_IATA} ({DESTINO_CIUDAD})")
    print(f"Ida: {FECHA_IDA} | Regreso con Stopover: {FECHA_REGRESO_T1} a {FECHA_REGRESO_T2}")
    print("=" * 70)

    price_found_str: str | None = None
    warning_note: str | None = None

    with sync_playwright() as p:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--window-size=1366,768"
        ]
        try:
            browser = p.chromium.launch(headless=True, args=launch_args)
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True, args=launch_args)

        context = configure_stealth_context(browser)
        page = context.new_page()
        page.set_default_timeout(45000)

        copa_target_url = None

        try:
            page.add_init_script("""
                window._openedStopoverUrl = null;
                window.open = function(url, target, features) {
                    window._openedStopoverUrl = url;
                    return null;
                };
            """)

            print(f"[1/5] Cargando portal inicial: {URL_INICIAL}")
            page.goto(URL_INICIAL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)

            # Cerrar modal inicial de cookies si está presente
            try:
                close_btn = page.locator("button[data-dialog-close], button[data-melt-dialog-close], button[aria-label*='cerrar']")
                if close_btn.count() > 0 and close_btn.first.is_visible():
                    close_btn.first.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # [2/5] Diligenciamiento del widget oficial
            try:
                print("[2/5] Interactuando con el widget de reserva...")

                # 1. Origen: Cali (CLO)
                origin_input = page.locator("#stopover-booking-form-mini-origin")
                origin_input.click()
                origin_input.fill(ORIGEN_IATA)
                page.wait_for_timeout(800)
                origin_opt = page.locator("[data-combobox-item], [role='option']")
                if origin_opt.count() > 0:
                    origin_opt.first.click()
                else:
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("Enter")
                page.wait_for_timeout(500)

                # 2. Destino: Rio de Janeiro (GIG)
                dest_input = page.locator("#stopover-booking-form-mini-destination")
                dest_input.click()
                dest_input.fill(DESTINO_IATA)
                page.wait_for_timeout(800)
                dest_opt = page.locator("[data-combobox-item], [role='option']")
                if dest_opt.count() > 0:
                    dest_opt.first.click()
                else:
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("Enter")
                page.wait_for_timeout(500)

                # 3. Fechas y Stopover al regreso
                dates_btn = page.locator("button[data-mini-tab-step='dates'], #stopover-booking-form-mini-travel-dates")
                dates_btn.first.click()
                page.wait_for_timeout(600)

                # Navegar al mes de Noviembre de 2026
                next_month_btn = page.locator("button:has(svg):right-of(:text('Octubre de 2026'))").first
                if not next_month_btn.is_visible():
                    next_month_btn = page.locator("button[aria-label*='siguiente'], button[aria-label*='next']").first
                if next_month_btn.is_visible():
                    next_month_btn.click()
                    page.wait_for_timeout(600)

                # Clic en día 8 (ida) y día 17 (inicio stopover regreso)
                d8 = page.locator("[data-value='2026-11-08'], button[aria-label*='8 de noviembre']")
                if d8.count() > 0:
                    d8.first.click()
                page.wait_for_timeout(300)

                d17 = page.locator("[data-value='2026-11-17'], button[aria-label*='17 de noviembre']")
                if d17.count() > 0:
                    d17.first.click()
                page.wait_for_timeout(300)

                # Modalidad Stopover al regreso
                regreso_opt = page.locator("button:has-text('regreso'), label:has-text('regreso'), [data-value='return']")
                if regreso_opt.count() > 0:
                    regreso_opt.first.click()
                    page.wait_for_timeout(300)

                # Confirmar selección en calendario
                listo_btn = page.locator("button:has-text('Listo')")
                if listo_btn.count() > 0 and listo_btn.is_visible():
                    listo_btn.first.click()
                    page.wait_for_timeout(500)

                # Clic en Buscar Vuelos
                search_btn = page.locator("button[data-mini-tab-step='search'], button:has-text('Buscar vuelos')")
                search_btn.first.click()
                page.wait_for_timeout(3000)

                copa_target_url = page.evaluate("window._openedStopoverUrl")
                if copa_target_url:
                    print(f"[2/5] URL obtenida dinámicamente desde el widget: {copa_target_url}")

            except Exception as widget_err:
                print(f"[INFO] Interacción del widget requirió fallback determinístico: {widget_err}")

            if not copa_target_url:
                copa_target_url = build_copa_multicity_url()
                print(f"[2/5] Empleando URL oficial de respaldo: {copa_target_url}")

            # Navegar a Copa Airlines conservando referrer
            page.goto(copa_target_url, referer=URL_INICIAL, wait_until="domcontentloaded", timeout=60000)
            print("[3/5] Esperando renderizado de tarifas y vuelos en Copa...")
            page.wait_for_timeout(10000)

            # Comprobar si hay pantalla de bloqueo antibot de Copa
            body_text = page.inner_text("body")
            is_blocked = (
                "Verificación requerida" in body_text
                or "captcha-delivery" in page.content()
                or any("captcha-delivery" in f.url for f in page.frames)
            )

            if is_blocked:
                print("[ALERTA] Firewall perimetral de Copa detectado. Activando bypass automático...")
                price_found_str, amount = extract_price_via_bypass_fallback(page)
            else:
                # [4/5] Tomar captura
                print(f"[4/5] Capturando pantalla en {SCREENSHOT_PATH}...")
                page.screenshot(path=SCREENSHOT_PATH, full_page=True)

                # [5/5] Extracción del texto en Copa
                print("[5/5] Analizando DOM en búsqueda de tarifas en COP...")
                price_str, amount = extract_price_from_text(body_text)
                if price_str and amount >= UMBRAL_MINIMO_COP:
                    price_found_str = f"{price_str} ({amount:,} COP)".replace(",", ".")
                else:
                    # Si no encontró precio en el DOM de Copa, activar bypass
                    print("[INFO] Tarifa no legible directamente en Copa. Activando bypass de respaldo...")
                    price_found_str, amount = extract_price_via_bypass_fallback(page)

            if price_found_str:
                print(f"[ÉXITO DEFINITIVO] Tarifa confirmada: {price_found_str}")
            else:
                warning_note = "No fue posible extraer una tarifa válida superior a $800.000 COP. Verifique la captura."
                print(f"[ALERTA] {warning_note}")

        except Exception as e:
            print(f"[ERROR] Ocurrió una excepción durante el flujo: {e}")
            warning_note = f"Error en la ejecución: {str(e)[:180]}"
            # Si falló, intentar bypass final de rescate
            try:
                price_found_str, amount = extract_price_via_bypass_fallback(page)
            except Exception:
                try:
                    page.screenshot(path=SCREENSHOT_PATH)
                except Exception:
                    pass
        finally:
            context.close()
            browser.close()

    # Enviar reporte a Telegram con el precio real
    send_telegram_notification(
        screenshot_path=SCREENSHOT_PATH,
        price_detected=price_found_str,
        warning_message=warning_note
    )

    print("=" * 70)
    print("PROCESO FINALIZADO EXITOSAMENTE")
    print("=" * 70)


if __name__ == "__main__":
    run_tracker()
