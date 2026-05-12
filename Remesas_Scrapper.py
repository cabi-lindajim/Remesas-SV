from playwright.async_api import async_playwright
import requests
import asyncio
import re
import os
from azure.storage.blob import BlobServiceClient

# ================= CONFIGURACIÓN =================
BUSCAR = "remesas familiares"
AZURE_CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
CONTAINER_NAME = "cabidataanalyticsdev"
BLOB_FOLDER    = "Data Bases - Documents/Monthly/Remesas SV"
# =================================================


def get_recency_score(nombre: str):
    texto = nombre.lower()
    score = 0

    año_match = re.search(r'20(\d{2})', texto)
    if año_match:
        score += int("20" + año_match.group(1)) * 10000

    meses = [
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'
    ]
    meses_encontrados = sum(1 for mes in meses if mes in texto)
    score += meses_encontrados * 200

    for i, mes in enumerate(meses):
        if mes in texto:
            score += (12 - i) * 50
            break

    if "enero" in texto and ("-" in texto or "hasta" in texto):
        score += 150

    return (score, nombre)


async def descargar_y_subir_blob():
    candidatos = []

    async with async_playwright() as p:
        print("🚀 Iniciando navegador en modo headless...")

        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--disable-web-security'
            ]
        )

        page = await browser.new_page()
        await page.set_extra_http_headers({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/134.0.0.0 Safari/537.36"
            )
        })

        print("🌐 Cargando página...")
        try:
            await page.goto(
                "https://www.bcr.gob.sv/documental/Inicio/",
                wait_until="domcontentloaded",
                timeout=90000
            )
            await asyncio.sleep(6)
        except Exception as e:
            print(f"⚠️  Error cargando página: {e}")
            await asyncio.sleep(10)

        try:
            search = page.locator('input[placeholder*="Nombre / Autor / Tipo / Fecha"]')
            if await search.count() > 0:
                await search.fill(BUSCAR)
                await asyncio.sleep(4)
        except Exception:
            pass

        print("🔍 Buscando informes...")

        page_number = 1
        while True:
            print(f"   Página {page_number}...")
            rows = await page.locator("table tbody tr").all()

            for row in rows:
                try:
                    cells = await row.locator("td").all()
                    if len(cells) < 4:
                        continue

                    fecha_pub = (await cells[0].inner_text()).strip()
                    nombre    = (await cells[1].inner_text()).strip()

                    if "remesas" not in nombre.lower() or "estadístico" not in nombre.lower():
                        continue

                    link = row.locator('a[href*="descarga/"]').first
                    if await link.count() == 0:
                        continue

                    href = await link.get_attribute("href")
                    url  = f"https://www.bcr.gob.sv{href}" if href.startswith("/") else href

                    score = get_recency_score(nombre)
                    candidatos.append((score, nombre, url, fecha_pub))
                    print(f"   → Encontrado: {nombre}")

                except Exception:
                    continue

            try:
                next_btn = page.locator('a.paginate_button.next:not(.disabled)')
                if await next_btn.count() > 0:
                    await next_btn.click()
                    await asyncio.sleep(3)
                    page_number += 1
                else:
                    break
            except Exception:
                break

        await browser.close()

    if not candidatos:
        print("❌ No se encontraron informes")
        return None

    candidatos.sort(reverse=True)
    _, nombre_mejor, url_mejor, fecha_pub = candidatos[0]
    nombre_archivo = re.sub(r'[\\/*?:"<>|]', "_", nombre_mejor) + ".xlsx"

    print("\n" + "=" * 90)
    print("✅ INFORME MÁS RECIENTE:")
    print(f"   Nombre    : {nombre_mejor}")
    print(f"   Publicado : {fecha_pub}")
    print(f"   Archivo   : {nombre_archivo}")
    print("=" * 90 + "\n")

    # ── Descarga ──────────────────────────────────────────────────────────────
    print("⬇️  Descargando Excel en memoria...")
    resp = requests.get(url_mejor, timeout=40)

    if resp.status_code != 200:
        print(f"❌ Error en descarga: HTTP {resp.status_code}")
        return None

    excel_bytes = resp.content
    print(f"✅ Descargado ({len(excel_bytes) / 1024:.1f} KB)")

    # ── Subida a Blob Storage ─────────────────────────────────────────────────
    blob_path = f"{BLOB_FOLDER}/{nombre_archivo}"
    print(f"☁️  Subiendo a Blob Storage → {CONTAINER_NAME}/{blob_path}")

    try:
        blob_service = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
        blob_client  = blob_service.get_blob_client(
            container=CONTAINER_NAME,
            blob=blob_path
        )
        blob_client.upload_blob(excel_bytes, overwrite=True)
        print(f"✅ Subido correctamente: {blob_path}")
    except Exception as e:
        print(f"❌ Error al subir a Blob Storage: {e}")
        return None

    return {
        'blob_path'        : blob_path,
        'nombre_archivo'   : nombre_archivo,
        'nombre_original'  : nombre_mejor,
        'fecha_publicacion': fecha_pub
    }


# ===================== EJECUTAR =====================
async def main():
    resultado = await descargar_y_subir_blob()
    if resultado:
        print(f"\n📁 Archivo disponible en Blob: {resultado['blob_path']}")
    else:
        raise SystemExit("❌ El pipeline terminó sin subir ningún archivo.")

asyncio.run(main())
