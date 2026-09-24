# ✈️ Panama Stopover Price Monitor (Copa Airlines)

Automatización serverless basada en **Python**, **Playwright** y **GitHub Actions** para monitorear diariamente la tarifa en Pesos Colombianos (COP) de vuelos con la modalidad **Panamá Stopover al regreso**, tomando capturas de pantalla de la cotización y enviando alertas automáticas a Telegram.

---

## 📋 Itinerario Configurado

| Parámetro | Valor |
| :--- | :--- |
| **Portal Inicial** | `https://panama-stopover.com/es/` |
| **Origen** | Cali, Colombia (`CLO` - Alfonso Bonilla Aragón) |
| **Destino Final** | Río de Janeiro, Brasil (`GIG` - Galeão) |
| **Ida (CLO ➔ GIG)** | 08 de noviembre de 2026 (`2026-11-08`) |
| **Modalidad Stopover** | Al regreso (3 días en Panamá PTY) |
| **Tramo 1 Regreso (GIG ➔ PTY)** | 17 de noviembre de 2026 (`2026-11-17`) |
| **Tramo 2 Regreso (PTY ➔ CLO)** | 20 de noviembre de 2026 (`2026-11-20`) |
| **Llegada Final a Cali** | 20 de noviembre de 2026 |
| **Moneda Objetivo** | Pesos Colombianos (`COP`) |
| **Filtro de Seguridad** | Tarifa mayor a `$ 800.000 COP` (descarta falsos positivos de equipaje) |

---

## 🛠️ Arquitectura y Tecnologías

- **Navegación & Scraping:** Playwright Sync API con Chromium en modo `headless=True`.
- **Evasión Anti-Detección (Akamai):**
  - Supresión de `navigator.webdriver`.
  - Emulación de User-Agent de escritorio reciente (Chrome 124+).
  - Contexto de navegación localizado en Colombia (`es-CO`, `America/Bogota`, 1366x768).
  - Mecanismo de enlace directo de alta resiliencia si el widget de Svelte presenta variaciones en el DOM.
- **Notificaciones Telegram:** API REST de Telegram (`requests.post`), enviando la captura de pantalla (`copa_resultado.png`) con reporte estructurado en Markdown.
- **Ejecución en la Nube:** GitHub Actions (100% gratuito) programado a las 08:00 AM hora de Colombia (`cron: '0 13 * * *'`) y disparable manualmente vía `workflow_dispatch`.

---

## 🚀 Configuración de Secretos en GitHub

Para que el script pueda enviar el reporte a tu chat o canal de Telegram, debes configurar dos secretos en tu repositorio:

1. Ve a tu repositorio en GitHub.
2. Ingresa a **Settings** > **Secrets and variables** > **Actions**.
3. Haz clic en **New repository secret** y añade:

| Nombre del Secreto | Descripción |
| :--- | :--- |
| `TELEGRAM_TOKEN` | Token de tu bot proporcionado por [@BotFather](https://t.me/BotFather) (ejemplo: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`). |
| `TELEGRAM_CHAT_ID` | Tu ID numérico de chat personal o del canal/grupo (puedes obtenerlo enviando un mensaje a [@userinfobot](https://t.me/userinfobot)). |

---

## 🕹️ Ejecución Manual en GitHub Actions

No es necesario esperar a las 08:00 AM para probar la automatización:

1. Dirígete a la pestaña **Actions** en tu repositorio de GitHub.
2. En la lista izquierda, selecciona **Panama Stopover Price Monitor**.
3. Haz clic en el botón desplegable **Run workflow**.
4. Selecciona la rama principal (`main`) y pulsa **Run workflow**.
5. En unos 2-3 minutos recibirás la foto con la cotización en tu chat de Telegram.

---

## 💻 Ejecución en Entorno Local

Si deseas correr el script en tu equipo local:

```bash
# 1. Crear entorno virtual (opcional pero recomendado)
python -m venv venv
venv\Scripts\activate   # En Windows
# source venv/bin/activate # En Linux/macOS

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Descargar el navegador Chromium para Playwright
playwright install chromium

# 4. Configurar variables de entorno
set TELEGRAM_TOKEN=tu_token_aqui
set TELEGRAM_CHAT_ID=tu_chat_id_aqui

# 5. Ejecutar el monitor
python tracker.py
```

Al finalizar la ejecución, se generará el archivo `copa_resultado.png` y se enviará la notificación correspondiente a Telegram.
