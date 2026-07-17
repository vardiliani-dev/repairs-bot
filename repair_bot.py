import logging
import asyncio
import json
import re
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationHandlerStop,
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import gspread
from google.oauth2.service_account import Credentials
import anthropic

# ══════════════════════════════════════════════
# НАЛАШТУВАННЯ
# ══════════════════════════════════════════════
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
SHEET_ID   = "1Nq-RKRAF16ZOs2gq7RS7IZ5-6PFsxZrNT4MkJ75dJ9U"
CREDS_FILE = "create-497113-eed86744057e.json"

MANAGER_IDS = [805571381, 692989160, 321443422]
MANAGER_NAMES = {
    805571381: "Олександр",
    692989160: "Виталій",
    321443422: "О.О.",
}
DIRECTOR_ID   = 299617056
ACCOUNTANT_ID = 5030873843

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Список всіх тягачів
TRUCKS = [
    "ВА1254ЕМ","BA1495EM","BA8603EM","ВА2387ЕХ","ВА5684НС",
    "BA8476EP","AM6937HE","ВА7289НІ","ВА7286НІ","ВА6675НІ",
    "ВА6678НІ","ВА2049НС","ВА9914ЕТ","BA6468EP","ВА7287НІ",
    "ВА8467НІ","ВА5952НС","ВА7990ЕН","ВА7954АО","ВА1483ЕО",
    "BA2187BK","ВА9244ЕН","ВА8712ЕР","KA3566HE",
]

# Список всіх цистерн
TANKS = [
    "BA4872XO","AA3677XG","AA3622XG","BХ1209XF","BX0764XF",
    "BA0583XF","AA5938XG","ВА7565XF","ВА7566XF","ВА7716XF",
    "ВА7718XF","АA2511XG","AA5942XG","BA0582XF","ВА7567XF",
    "ВА7719XF","СЕ2735ХР","СЕ2747ХР","ВА4694ХТ","ВА4847ХО",
    "BA6713XP","ВА4695ХТ","BA4821ХО","ВА5253ХР","ВА4954ХО",
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# GOOGLE SHEETS
# ══════════════════════════════════════════════
def get_spreadsheet():
    # Спочатку пробуємо взяти credentials зі змінної середовища (безпечніше)
    creds_json_env = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_json_env:
        try:
            info = json.loads(creds_json_env)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            client = gspread.authorize(creds)
            return client.open_by_key(SHEET_ID)
        except Exception as e:
            logger.error(f"GOOGLE_CREDS_JSON parse error: {e}")

    # Інакше шукаємо JSON файл з credentials
    candidates = [
        CREDS_FILE,
        "create-497113-eed86744057e.json..json",
        "create-497113-eed86744057e.json.json",
    ]
    try:
        for f in os.listdir("."):
            if f.endswith(".json") and "create-" in f.lower():
                if f not in candidates:
                    candidates.append(f)
    except Exception:
        pass

    creds_path = None
    for path in candidates:
        if os.path.exists(path):
            creds_path = path
            break

    if not creds_path:
        available = [f for f in os.listdir(".") if f.endswith(".json")] if os.path.exists(".") else []
        raise FileNotFoundError(
            f"Google credentials JSON не знайдено. Доступні JSON: {available}"
        )

    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

def get_or_create_sheet(name, headers):
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(name, rows=1000, cols=len(headers))
        ws.append_row(headers)
        ws.freeze(rows=1)
    return ws

def get_repairs_sheet():
    return get_or_create_sheet("Ремонти", [
        "ID", "Дата подачі", "Тип", "Машина", "Тип машини",
        "Опис робіт / Запчастини", "Сума", "Форма оплати",
        "СТО / Постачальник", "Номер рахунку", "Менеджер",
        "Статус", "Дата погодження", "Дата оплати"
    ])

def get_stock_sheet():
    return get_or_create_sheet("Склад", [
        "Позиція", "Одиниця", "Кількість", "Ціна за одиницю",
        "Загальна вартість", "Дата останнього оновлення"
    ])

def get_movements_sheet():
    return get_or_create_sheet("Рух складу", [
        "ID", "Дата", "Тип", "Позиція", "Кількість",
        "Машина", "Менеджер", "Статус", "Примітка"
    ])

# ══════════════════════════════════════════════
# СКЛАД - ЛОГІКА
# ══════════════════════════════════════════════
def get_stock_items():
    ws = get_stock_sheet()
    records = ws.get_all_records()
    return [r for r in records if r.get("Кількість", 0)]

def update_stock(position, unit, quantity_delta, price_per_unit=0):
    ws = get_stock_sheet()
    records = ws.get_all_records()
    for i, r in enumerate(records, start=2):
        if r.get("Позиція", "").lower() == position.lower():
            new_qty = float(r.get("Кількість", 0)) + quantity_delta
            new_total = new_qty * float(r.get("Ціна за одиницю", price_per_unit) or price_per_unit)
            ws.update_cell(i, 3, round(new_qty, 3))
            ws.update_cell(i, 5, round(new_total, 2))
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y"))
            return True
    if quantity_delta > 0:
        total = quantity_delta * price_per_unit
        ws.append_row([
            position, unit, round(quantity_delta, 3),
            round(price_per_unit, 2), round(total, 2),
            datetime.now().strftime("%d.%m.%Y")
        ])
    return True

# ══════════════════════════════════════════════
# AI РОЗПІЗНАВАННЯ
# ══════════════════════════════════════════════
async def recognize_document(file_content: bytes, mime_type: str, is_pdf: bool = False) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    trucks_str = ", ".join(TRUCKS)
    tanks_str  = ", ".join(TANKS)

    prompt = f"""Ти аналізуєш рахунок-фактуру від СТО або постачальника товарів/послуг.

Список тягачів компанії: {trucks_str}
Список цистерн компанії: {tanks_str}

ВАЖЛИВО про постачальника:
- Постачальник (contractor) — це ПРОДАВЕЦЬ, той хто ВИСТАВИВ рахунок і надав послуги/товари.
- В рахунку це поле зазвичай позначене як "Постачальник", "Продавець", "Виконавець", "Виконавець послуг".
- Наша компанія "ПП Транзит-Траст" (або "Транзит-Траст") є ПОКУПЦЕМ / ЗАМОВНИКОМ — її НЕ треба вказувати як постачальника!
- Якщо в рахунку вказано "Покупець: Транзит-Траст" — це наша компанія, ігноруй її.
- Шукай саме того, ХТО ВИСТАВИВ рахунок (зазвичай зверху документу або в полі "Постачальник").

Витягни з документа наступну інформацію у JSON:
- date: дата рахунку (формат ДД.ММ.РРРР)
- vehicle: номер машини зі списку вище (якщо знайдено, точно як у списку)
- vehicle_type: "тягач" або "цистерна" (визначи за номером)
- description: опис робіт або перелік запчастин (коротко)
- amount: сума (тільки число)
- payment_type: "безнал" або "готівка"
- contractor: назва ПОСТАЧАЛЬНИКА/ПРОДАВЦЯ (НЕ покупця, НЕ нашої компанії Транзит-Траст!)
- invoice_number: номер рахунку або документу

Якщо якесь поле не знайдено — встав null.
Відповідай ТІЛЬКИ JSON без жодного іншого тексту."""

    import base64
    b64 = base64.b64encode(file_content).decode()

    if is_pdf:
        content = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
            {"type": "text", "text": prompt}
        ]
    else:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
            {"type": "text", "text": prompt}
        ]

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": content}]
    )

    text = response.content[0].text.strip()
    text = re.sub(r'^```json\s*|\s*```$', '', text)
    return json.loads(text)


async def recognize_text_with_ai(text_content: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    trucks_str = ", ".join(TRUCKS)
    tanks_str  = ", ".join(TANKS)

    prompt = f"""Ти аналізуєш текст рахунку-фактури, витягнутий з Excel таблиці.

Список тягачів компанії: {trucks_str}
Список цистерн компанії: {tanks_str}

ВАЖЛИВО про постачальника:
- Постачальник (contractor) — це ПРОДАВЕЦЬ, той хто ВИСТАВИВ рахунок і надав послуги/товари.
- В рахунку це поле зазвичай позначене як "Постачальник", "Продавець", "Виконавець".
- Наша компанія "ПП Транзит-Траст" (або "Транзит-Траст") є ПОКУПЦЕМ — її НЕ треба вказувати як постачальника!
- Якщо в тексті є "Покупець: Транзит-Траст" — це наша компанія, ігноруй її.

Текст рахунку:
---
{text_content[:8000]}
---

Витягни наступну інформацію у JSON:
- date: дата рахунку (формат ДД.ММ.РРРР)
- vehicle: номер машини зі списку вище (якщо знайдено, точно як у списку)
- vehicle_type: "тягач" або "цистерна"
- description: опис робіт або перелік позицій (коротко, до 100 символів)
- amount: сума загальна (тільки число, без пробілів)
- payment_type: "безнал" або "готівка"
- contractor: назва ПОСТАЧАЛЬНИКА (НЕ Транзит-Траст!)
- invoice_number: номер рахунку

Якщо якесь поле не знайдено — встав null.
Відповідай ТІЛЬКИ JSON без жодного іншого тексту."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    result_text = response.content[0].text.strip()
    result_text = re.sub(r'^```json\s*|\s*```$', '', result_text)
    return json.loads(result_text)

# ══════════════════════════════════════════════
# ГОЛОВНЕ МЕНЮ
# ══════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in MANAGER_IDS:
        await update.message.reply_text("⛔ Доступ заборонено.")
        return
    await show_main_menu(update, context)

async def show_main_menu(update, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔧 Ремонт", callback_data="menu_repair")],
        [InlineKeyboardButton("📥 Закупка на склад", callback_data="menu_purchase")],
        [InlineKeyboardButton("📤 Списання зі складу", callback_data="menu_writeoff")],
    ])
    text = "Що додаємо?\n\nОберіть тип операції:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in MANAGER_IDS:
        return
    context.user_data.clear()
    await show_main_menu(update, context)

# ══════════════════════════════════════════════
# ОБРОБКА CALLBACK
# ══════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_repair":
        context.user_data["op_type"] = "repair"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 Готівка (вручну)", callback_data="pay_cash")],
            [InlineKeyboardButton("🏦 Безнал (рахунок/фото)", callback_data="pay_invoice")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ])
        await query.edit_message_text("🔧 Ремонт\n\nСпосіб оплати:", reply_markup=keyboard)
        return

    if data == "menu_purchase":
        context.user_data["op_type"] = "purchase"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 Готівка (вручну)", callback_data="pay_cash")],
            [InlineKeyboardButton("🏦 Безнал (рахунок/фото)", callback_data="pay_invoice")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
        ])
        await query.edit_message_text("📥 Закупка на склад\n\nСпосіб оплати:", reply_markup=keyboard)
        return

    if data == "menu_writeoff":
        context.user_data["op_type"] = "writeoff"
        await show_stock_selection(query, context)
        return

    if data == "back_main":
        context.user_data.clear()
        await show_main_menu(update, context)
        return

    if data == "pay_cash":
        context.user_data["payment"] = "готівка"
        op = context.user_data.get("op_type")
        if op == "repair":
            await ask_vehicle(query, context)
        else:
            context.user_data["step"] = "manual_purchase"
            await query.edit_message_text(
                "📥 Введіть дані закупки в один рядок:\n\n"
                "<code>Назва позиції, кількість, одиниця, сума, постачальник</code>\n\n"
                "Приклад:\n"
                "<code>Масло моторне 5W-40, 200, л, 50000, Укрнафта</code>",
                parse_mode="HTML"
            )
        return

    if data == "pay_invoice":
        context.user_data["payment"] = "безнал"
        context.user_data["step"] = "awaiting_file"
        await query.edit_message_text(
            "📎 Надішліть рахунок:\n\n"
            "• PDF файл\n"
            "• Фото рахунку\n"
            "• Скан документу\n\n"
            "AI розпізнає дані автоматично."
        )
        return

    if data.startswith("truck_"):
        vehicle = data[6:]
        context.user_data["vehicle"] = vehicle
        context.user_data["vehicle_type"] = "тягач"
        await ask_manual_repair(query, context)
        return

    if data.startswith("tank_"):
        vehicle = data[5:]
        context.user_data["vehicle"] = vehicle
        context.user_data["vehicle_type"] = "цистерна"
        await ask_manual_repair(query, context)
        return

    if data == "vehicle_page_trucks":
        await show_vehicle_selection(query, context, "trucks", 0)
        return

    if data == "vehicle_page_tanks":
        await show_vehicle_selection(query, context, "tanks", 0)
        return

    if data.startswith("vpage_"):
        _, vtype, page = data.split("_")
        await show_vehicle_selection(query, context, vtype, int(page))
        return

    if data.startswith("stock_item_"):
        item_idx = int(data[11:])
        items = get_stock_items()
        if item_idx < len(items):
            item = items[item_idx]
            context.user_data["stock_item"] = item
            context.user_data["step"] = "writeoff_qty"
            await query.edit_message_text(
                f"📤 Списання: <b>{item['Позиція']}</b>\n"
                f"На складі: {item['Кількість']} {item['Одиниця']}\n\n"
                f"Введіть кількість для списання:",
                parse_mode="HTML"
            )
        return

    if data == "confirm_data":
        await submit_for_approval(query, context)
        return

    if data == "edit_data":
        await show_edit_menu(query, context)
        return

    if data.startswith("edit_field_"):
        field = data[11:]
        context.user_data["editing_field"] = field
        context.user_data["step"] = "editing_field"
        field_labels = {
            "vehicle": "🚗 машину (введіть номер, наприклад BA2187BK)",
            "description": "📝 опис робіт / запчастин",
            "amount": "💰 суму (тільки число)",
            "contractor": "🏪 постачальника / СТО",
            "date": "📅 дату (формат ДД.ММ.РРРР)",
            "invoice": "🔢 номер рахунку",
        }
        await query.edit_message_text(
            f"Введіть нове значення для поля {field_labels.get(field, field)}:"
        )
        return

    if data == "confirm_vehicle":
        await ask_manual_repair(query, context)
        return

    if data.startswith("director_approve_"):
        record_id = int(data[17:])
        await director_approve(query, context, record_id)
        return

    if data.startswith("director_reject_"):
        record_id = int(data[16:])
        await director_reject(query, context, record_id)
        return

    if data.startswith("accountant_paid_"):
        record_id = int(data[16:])
        await accountant_paid(query, context, record_id)
        return

    if data.startswith("wo_truck_"):
        vehicle = data[9:]
        context.user_data["vehicle"] = vehicle
        context.user_data["vehicle_type"] = "тягач"
        await submit_writeoff(query, context)
        return

    if data.startswith("wo_tank_"):
        vehicle = data[8:]
        context.user_data["vehicle"] = vehicle
        context.user_data["vehicle_type"] = "цистерна"
        await submit_writeoff(query, context)
        return

    if data == "wo_page_trucks":
        await show_writeoff_vehicle(query, context, "trucks", 0)
        return

    if data == "wo_page_tanks":
        await show_writeoff_vehicle(query, context, "tanks", 0)
        return

    if data.startswith("wopage_"):
        _, vtype, page = data.split("_")
        await show_writeoff_vehicle(query, context, vtype, int(page))
        return


# ══════════════════════════════════════════════
# ВИБІР МАШИНИ
# ══════════════════════════════════════════════
async def ask_vehicle(query, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚛 Тягач", callback_data="vehicle_page_trucks")],
        [InlineKeyboardButton("🛢 Цистерна", callback_data="vehicle_page_tanks")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ])
    op = context.user_data.get("op_type", "")
    op_label = "🔧 Ремонт" if op == "repair" else "📥 Закупка"
    await query.edit_message_text(
        f"{op_label}\n\nОберіть тип транспорту:",
        reply_markup=keyboard
    )

async def show_vehicle_selection(query, context, vtype, page):
    items = TRUCKS if vtype == "trucks" else TANKS
    prefix = "truck_" if vtype == "trucks" else "tank_"
    per_page = 8
    start = page * per_page
    end = min(start + per_page, len(items))
    chunk = items[start:end]

    rows = []
    for i in range(0, len(chunk), 2):
        row = [InlineKeyboardButton(chunk[i], callback_data=f"{prefix}{chunk[i]}")]
        if i + 1 < len(chunk):
            row.append(InlineKeyboardButton(chunk[i+1], callback_data=f"{prefix}{chunk[i+1]}"))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"vpage_{vtype}_{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"vpage_{vtype}_{page+1}"))
    if nav:
        rows.append(nav)

    other = "tanks" if vtype == "trucks" else "trucks"
    other_label = "🛢 Цистерни" if vtype == "trucks" else "🚛 Тягачі"
    rows.append([InlineKeyboardButton(f"↩️ {other_label}", callback_data=f"vehicle_page_{other}")])

    label = "Тягачі" if vtype == "trucks" else "Цистерни"
    await query.edit_message_text(
        f"Оберіть {label} ({start+1}-{end} з {len(items)}):",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def ask_manual_repair(query, context):
    vehicle = context.user_data.get("vehicle", "")
    vtype   = context.user_data.get("vehicle_type", "")
    payment = context.user_data.get("payment", "готівка")
    op      = context.user_data.get("op_type", "repair")

    context.user_data["step"] = "manual_input"

    if op == "repair":
        await query.edit_message_text(
            f"🔧 Ремонт — {vtype} {vehicle}\n"
            f"Оплата: {payment}\n\n"
            f"Введіть дані в одному повідомленні:\n\n"
            f"<code>Опис робіт, сума, СТО, номер рахунку (якщо є)</code>\n\n"
            f"Приклад:\n"
            f"<code>Заміна гальмівних колодок, 8500, СТО Автомайстер, рах.128</code>",
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            f"📥 Закупка — {vtype} {vehicle}\n"
            f"Оплата: {payment}\n\n"
            f"Введіть дані:\n\n"
            f"<code>Назва, кількість, одиниця, сума, постачальник</code>\n\n"
            f"Приклад:\n"
            f"<code>Фільтр оливи, 12, шт, 3600, Автозапчастини</code>",
            parse_mode="HTML"
        )

# ══════════════════════════════════════════════
# ВИБІР ПОЗИЦІЇ ЗІ СКЛАДУ
# ══════════════════════════════════════════════
async def show_stock_selection(query, context):
    try:
        items = get_stock_items()
    except Exception:
        items = []

    if not items:
        await query.edit_message_text(
            "📦 Склад порожній.\n\n"
            "Спочатку зробіть закупку через 📥 Закупка на склад."
        )
        return

    rows = []
    for i, item in enumerate(items[:10]):
        label = f"{item['Позиція']} — {item['Кількість']} {item['Одиниця']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"stock_item_{i}")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])

    await query.edit_message_text(
        "📤 Списання зі складу\n\nОберіть позицію:",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def show_writeoff_vehicle(query, context, vtype, page):
    items = TRUCKS if vtype == "trucks" else TANKS
    prefix = "wo_truck_" if vtype == "trucks" else "wo_tank_"
    per_page = 8
    start = page * per_page
    end = min(start + per_page, len(items))
    chunk = items[start:end]

    rows = []
    for i in range(0, len(chunk), 2):
        row = [InlineKeyboardButton(chunk[i], callback_data=f"{prefix}{chunk[i]}")]
        if i + 1 < len(chunk):
            row.append(InlineKeyboardButton(chunk[i+1], callback_data=f"{prefix}{chunk[i+1]}"))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"wopage_{vtype}_{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"wopage_{vtype}_{page+1}"))
    if nav:
        rows.append(nav)

    other = "tanks" if vtype == "trucks" else "trucks"
    other_label = "🛢 Цистерни" if vtype == "trucks" else "🚛 Тягачі"
    rows.append([InlineKeyboardButton(f"↩️ {other_label}", callback_data=f"wo_page_{other}")])

    label = "Тягачі" if vtype == "trucks" else "Цистерни"
    item = context.user_data.get("stock_item", {})
    await query.edit_message_text(
        f"📤 Списання: {item.get('Позиція','')}\n"
        f"Кількість: {context.user_data.get('writeoff_qty','')} {item.get('Одиниця','')}\n\n"
        f"Оберіть {label} ({start+1}-{end} з {len(items)}):",
        reply_markup=InlineKeyboardMarkup(rows)
    )

# ══════════════════════════════════════════════
# ОБРОБКА ТЕКСТОВИХ ПОВІДОМЛЕНЬ
# ══════════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in MANAGER_IDS:
        return
    if update.message.text.startswith("/"):
        return

    step = context.user_data.get("step")

    # Редагування конкретного поля
    if step == "editing_field":
        field = context.user_data.get("editing_field")
        new_value = update.message.text.strip()

        if field == "vehicle":
            found = False
            for v in TRUCKS + TANKS:
                if v.upper() == new_value.upper():
                    context.user_data["vehicle"] = v
                    context.user_data["vehicle_type"] = "тягач" if v in TRUCKS else "цистерна"
                    found = True
                    break
            if not found:
                context.user_data["vehicle"] = new_value
        else:
            field_map = {
                "description": "description",
                "amount": "amount",
                "contractor": "contractor",
                "date": "date",
                "invoice": "invoice",
            }
            key = field_map.get(field, field)
            if field == "amount":
                new_value = new_value.replace(" ", "").replace(",", ".")
            context.user_data[key] = new_value

        context.user_data.pop("editing_field", None)
        context.user_data["step"] = "confirming"
        await show_confirmation(update.message, context)
        return

    # Кількість для списання
    if step == "writeoff_qty":
        text = update.message.text.strip().replace(",", ".")
        try:
            qty = float(text)
        except ValueError:
            await update.message.reply_text("Введіть число (кількість). Наприклад: 20 або 20.5")
            return
        item = context.user_data.get("stock_item", {})
        available = float(item.get("Кількість", 0))
        if qty > available:
            await update.message.reply_text(
                f"❗️ На складі тільки {available} {item.get('Одиниця','')}.\n"
                f"Введіть менше або рівно {available}:"
            )
            return
        context.user_data["writeoff_qty"] = qty
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Тягач", callback_data="wo_page_trucks")],
            [InlineKeyboardButton("🛢 Цистерна", callback_data="wo_page_tanks")],
        ])
        await update.message.reply_text(
            f"📤 {item.get('Позиція','')} — {qty} {item.get('Одиниця','')}\n\n"
            f"На яку машину списуємо?",
            reply_markup=keyboard
        )
        return

    if step == "manual_input":
        parts = [p.strip() for p in update.message.text.split(",")]
        op = context.user_data.get("op_type")

        if op == "repair":
            description = parts[0] if len(parts) > 0 else ""
            amount = parts[1] if len(parts) > 1 else ""
            contractor = parts[2] if len(parts) > 2 else ""
            invoice = parts[3] if len(parts) > 3 else ""

            context.user_data.update({
                "description": description,
                "amount": amount.replace(" ", ""),
                "contractor": contractor,
                "invoice": invoice,
                "date": datetime.now().strftime("%d.%m.%Y"),
            })
        else:
            name = parts[0] if len(parts) > 0 else ""
            qty  = parts[1] if len(parts) > 1 else ""
            unit = parts[2] if len(parts) > 2 else ""
            amount = parts[3] if len(parts) > 3 else ""
            contractor = parts[4] if len(parts) > 4 else ""

            context.user_data.update({
                "description": f"{name} {qty} {unit}",
                "stock_name": name,
                "stock_qty": qty,
                "stock_unit": unit,
                "amount": amount.replace(" ", ""),
                "contractor": contractor,
                "invoice": "",
                "date": datetime.now().strftime("%d.%m.%Y"),
            })

        context.user_data["step"] = "confirming"
        await show_confirmation(update.message, context)
        return

    if step == "manual_purchase":
        parts = [p.strip() for p in update.message.text.split(",")]
        name = parts[0] if len(parts) > 0 else ""
        qty  = parts[1] if len(parts) > 1 else ""
        unit = parts[2] if len(parts) > 2 else ""
        amount = parts[3] if len(parts) > 3 else ""
        contractor = parts[4] if len(parts) > 4 else ""

        context.user_data.update({
            "description": f"{name} {qty} {unit}",
            "stock_name": name, "stock_qty": qty, "stock_unit": unit,
            "amount": amount.replace(" ", ""), "contractor": contractor,
            "invoice": "", "date": datetime.now().strftime("%d.%m.%Y"),
            "vehicle": "", "vehicle_type": "",
            "step": "confirming",
        })
        await show_confirmation(update.message, context)
        return


async def show_edit_menu(msg_obj, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Машина", callback_data="edit_field_vehicle")],
        [InlineKeyboardButton("📝 Опис", callback_data="edit_field_description")],
        [InlineKeyboardButton("💰 Сума", callback_data="edit_field_amount")],
        [InlineKeyboardButton("🏪 Постачальник/СТО", callback_data="edit_field_contractor")],
        [InlineKeyboardButton("📅 Дата", callback_data="edit_field_date")],
        [InlineKeyboardButton("🔢 Номер рахунку", callback_data="edit_field_invoice")],
        [InlineKeyboardButton("✅ Готово, відправити", callback_data="confirm_data")],
    ])
    text = "✏️ <b>Що виправити?</b>\n\nОберіть поле для редагування:"

    if hasattr(msg_obj, 'reply_text'):
        await msg_obj.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        try:
            await msg_obj.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await msg_obj.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def show_confirmation(msg_obj, context):
    op      = context.user_data.get("op_type", "")
    vehicle = context.user_data.get("vehicle", "—")
    vtype   = context.user_data.get("vehicle_type", "")
    desc    = context.user_data.get("description", "")
    amount  = context.user_data.get("amount", "")
    pay     = context.user_data.get("payment", "")
    contr   = context.user_data.get("contractor", "")
    invoice = context.user_data.get("invoice", "")
    date    = context.user_data.get("date", "")

    op_labels = {"repair": "🔧 Ремонт", "purchase": "📥 Закупка", "writeoff": "📤 Списання"}
    op_label = op_labels.get(op, op)

    text = (
        f"<b>Перевірте дані:</b>\n\n"
        f"📋 Тип: {op_label}\n"
        f"🚗 Машина: {vtype} {vehicle}\n"
        f"📅 Дата: {date}\n"
        f"📝 Опис: {desc}\n"
        f"💰 Сума: {amount} грн\n"
        f"💳 Оплата: {pay}\n"
        f"🏪 Постачальник/СТО: {contr}\n"
    )
    if invoice:
        text += f"🔢 Рахунок: {invoice}\n"
    text += "\nВсе вірно?"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Так, відправити", callback_data="confirm_data"),
            InlineKeyboardButton("✏️ Виправити", callback_data="edit_data"),
        ]
    ])

    if hasattr(msg_obj, 'reply_text'):
        await msg_obj.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        try:
            await msg_obj.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await msg_obj.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

# ══════════════════════════════════════════════
# ОБРОБКА ФАЙЛІВ (PDF, фото, Excel)
# ══════════════════════════════════════════════
def extract_excel_text(file_bytes: bytes) -> str:
    """Витягує весь текст з Excel файлу."""
    import io
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        all_text = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            for row in ws.iter_rows(values_only=True):
                row_text = []
                for cell in row:
                    if cell is not None:
                        row_text.append(str(cell))
                if row_text:
                    all_text.append(" | ".join(row_text))
        return "\n".join(all_text)
    except Exception as e:
        logger.error(f"openpyxl error: {e}")
        try:
            import xlrd
            wb_xls = xlrd.open_workbook(file_contents=file_bytes)
            all_text = []
            for sheet_idx in range(wb_xls.nsheets):
                ws_xls = wb_xls.sheet_by_index(sheet_idx)
                for r in range(ws_xls.nrows):
                    row_text = []
                    for c in range(ws_xls.ncols):
                        v = ws_xls.cell_value(r, c)
                        if v:
                            row_text.append(str(v))
                    if row_text:
                        all_text.append(" | ".join(row_text))
            return "\n".join(all_text)
        except Exception as e2:
            logger.error(f"xlrd error: {e2}")
            return ""


async def parse_excel_invoice(file_bytes: bytes) -> dict:
    """Парсить Excel рахунок: витягує текст і розпізнає через AI."""
    text = extract_excel_text(file_bytes)
    if not text:
        return {}

    try:
        return await recognize_text_with_ai(text)
    except Exception as e:
        logger.error(f"Excel AI recognition error: {e}")
        return extract_invoice_data_from_text(text)


def extract_invoice_data_from_text(text: str) -> dict:
    """Резервний regex-парсер."""
    result = {}

    amount_match = re.search(r'[Уу]сього\s+з\s+ПДВ[:\s]+([0-9\s,\.]+)', text)
    if not amount_match:
        amount_match = re.search(r'[Вв]сього[:\s]+([0-9\s,\.]+)\s*грн', text)
    if not amount_match:
        numbers = re.findall(r'(\d{3,7}(?:[,\.]\d{2})?)', text)
        if numbers:
            amounts = [float(n.replace(',', '.').replace(' ', '')) for n in numbers]
            result["amount"] = int(max(amounts))
    else:
        amt = amount_match.group(1).strip().replace(' ', '').replace(',', '.')
        try:
            result["amount"] = int(float(amt))
        except Exception:
            pass

    inv_match = re.search(r'[Рр]ахунок[^\d]*(?:№\s*)?(\d+)', text)
    if inv_match:
        result["invoice"] = inv_match.group(1)

    date_match = re.search(r'(\d{1,2})[.\s]+([а-яА-ЯёЁіІїЇєЄ]+)\s+(\d{4})', text)
    if date_match:
        months = {"січня":1,"лютого":2,"березня":3,"квітня":4,"травня":5,"червня":6,
                  "липня":7,"серпня":8,"вересня":9,"жовтня":10,"листопада":11,"грудня":12}
        m = months.get(date_match.group(2).lower())
        if m:
            result["date"] = f"{int(date_match.group(1)):02d}.{m:02d}.{date_match.group(3)}"
    if not result.get("date"):
        date_match2 = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', text)
        if date_match2:
            result["date"] = f"{date_match2.group(1)}.{date_match2.group(2)}.{date_match2.group(3)}"

    sup_match = re.search(r"[Пп]остачальник[^\n]{0,5}([А-ЯҐЄІЇёA-Z][^\n]{3,50})", text)
    if sup_match:
        result["contractor"] = sup_match.group(1).strip()

    desc_patterns = [r"Заправка[^\n]{0,50}", r"Ремонт[^\n]{0,50}", r"[Зз]аміна[^\n]{0,50}", r"ТО[^\n]{0,30}"]
    for pat in desc_patterns:
        dm = re.search(pat, text)
        if dm:
            result["description"] = dm.group(0).strip()[:100]
            break

    for vehicle in TRUCKS + TANKS:
        if vehicle.upper() in text.upper():
            result["vehicle"] = vehicle
            result["vehicle_type"] = "тягач" if vehicle in TRUCKS else "цистерна"
            break

    return result


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in MANAGER_IDS:
        return
    if context.user_data.get("step") != "awaiting_file":
        return

    await update.message.reply_text("🤖 AI розпізнає документ, зачекайте...")

    try:
        file_bytes = None
        mime = "application/pdf"
        is_pdf = False
        is_excel = False

        if update.message.document:
            file = await context.bot.get_file(update.message.document.file_id)
            file_bytes = bytes(await file.download_as_bytearray())
            mime = update.message.document.mime_type or "application/pdf"
            is_pdf = "pdf" in mime.lower()
            is_excel = any(x in mime.lower() for x in ["excel", "spreadsheet", "xls"]) or \
                       update.message.document.file_name.endswith((".xls", ".xlsx"))
            context.user_data["file_id"] = update.message.document.file_id
            context.user_data["file_type"] = "document"
        elif update.message.photo:
            file = await context.bot.get_file(update.message.photo[-1].file_id)
            file_bytes = bytes(await file.download_as_bytearray())
            mime = "image/jpeg"
            context.user_data["file_id"] = update.message.photo[-1].file_id
            context.user_data["file_type"] = "photo"
        else:
            await update.message.reply_text("Надішліть PDF, фото або Excel файл рахунку.")
            return

        data = {}
        if is_excel:
            try:
                data = await parse_excel_invoice(file_bytes)
            except Exception as e:
                logger.error(f"Excel parse error: {e}")
        else:
            try:
                data = await recognize_document(file_bytes, mime, is_pdf)
            except Exception as e:
                logger.error(f"AI recognition error: {e}")

        context.user_data.update({
            "date":         data.get("date") or datetime.now().strftime("%d.%m.%Y"),
            "vehicle":      data.get("vehicle") or context.user_data.get("vehicle", ""),
            "vehicle_type": data.get("vehicle_type") or context.user_data.get("vehicle_type", ""),
            "description":  data.get("description") or "",
            "amount":       str(data.get("amount") or ""),
            "contractor":   data.get("contractor") or "",
            "invoice":      data.get("invoice") or "",
            "step":         "confirming",
        })

        await show_confirmation(update.message, context)

    except Exception as e:
        logger.error(f"handle_file error: {e}")
        await update.message.reply_text(
            "❗️ Не вдалось розпізнати документ.\n\n"
            "Введіть дані вручну:\n"
            "<code>Опис, сума, СТО, номер рахунку</code>",
            parse_mode="HTML"
        )
        context.user_data["step"] = "manual_input"

# ══════════════════════════════════════════════
# ВІДПРАВКА НА ПОГОДЖЕННЯ
# ══════════════════════════════════════════════
async def submit_for_approval(query, context):
    try:
        ws = get_repairs_sheet()
        all_rows = ws.get_all_values()
        record_id = len(all_rows)

        op      = context.user_data.get("op_type", "")
        vehicle = context.user_data.get("vehicle", "")
        vtype   = context.user_data.get("vehicle_type", "")
        desc    = context.user_data.get("description", "")
        amount  = context.user_data.get("amount", "")
        pay     = context.user_data.get("payment", "готівка")
        contr   = context.user_data.get("contractor", "")
        invoice = context.user_data.get("invoice", "")
        date    = context.user_data.get("date", "")
        manager = MANAGER_NAMES.get(query.from_user.id, query.from_user.first_name)

        op_labels = {"repair": "Ремонт", "purchase": "Закупка", "writeoff": "Списання"}
        op_label = op_labels.get(op, op)

        ws.append_row([
            record_id,
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            op_label,
            vehicle,
            vtype,
            desc,
            amount,
            pay,
            contr,
            invoice,
            manager,
            "На погодженні",
            "", ""
        ])

        context.application.bot_data[f"repair_{record_id}"] = dict(context.user_data)
        context.application.bot_data[f"repair_{record_id}"]["record_id"] = record_id
        context.application.bot_data[f"repair_{record_id}"]["manager_id"] = query.from_user.id

        await query.edit_message_text(
            f"✅ Заявку #{record_id} відправлено на погодження директору.\n\n"
            f"Очікуйте рішення."
        )

        director_text = (
            f"📋 <b>Нова заявка #{record_id}</b>\n\n"
            f"👤 Менеджер: {manager}\n"
            f"📋 Тип: {op_label}\n"
            f"🚗 Машина: {vtype} {vehicle}\n"
            f"📅 Дата: {date}\n"
            f"📝 Опис: {desc}\n"
            f"💰 Сума: {amount} грн\n"
            f"💳 Оплата: {pay}\n"
            f"🏪 {contr}\n"
        )
        if invoice:
            director_text += f"🔢 Рахунок: {invoice}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Погодити", callback_data=f"director_approve_{record_id}"),
                InlineKeyboardButton("❌ Відхилити", callback_data=f"director_reject_{record_id}"),
            ]
        ])

        file_id   = context.user_data.get("file_id")
        file_type = context.user_data.get("file_type")

        if file_id:
            if file_type == "document":
                await context.bot.send_document(
                    chat_id=DIRECTOR_ID, document=file_id,
                    caption=director_text, parse_mode="HTML", reply_markup=keyboard
                )
            else:
                await context.bot.send_photo(
                    chat_id=DIRECTOR_ID, photo=file_id,
                    caption=director_text, parse_mode="HTML", reply_markup=keyboard
                )
        else:
            await context.bot.send_message(
                chat_id=DIRECTOR_ID, text=director_text,
                parse_mode="HTML", reply_markup=keyboard
            )

        context.user_data.clear()

    except Exception as e:
        logger.error(f"submit_for_approval error: {e}", exc_info=True)
        error_msg = str(e)[:200]
        try:
            await query.edit_message_text(
                f"❌ Помилка збереження:\n<code>{error_msg}</code>\n\nСпробуйте ще раз або зверніться до адміна.",
                parse_mode="HTML"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"❌ Помилка збереження:\n{error_msg}\n\nСпробуйте ще раз."
            )

# ══════════════════════════════════════════════
# ПОГОДЖЕННЯ ДИРЕКТОРА
# ══════════════════════════════════════════════
async def director_approve(query, context, record_id):
    if query.from_user.id != DIRECTOR_ID:
        await query.answer("⛔ Тільки директор може погоджувати.", show_alert=True)
        return
    try:
        ws = get_repairs_sheet()
        cell = ws.find(str(record_id))
        ws.update_cell(cell.row, 12, "Погоджено")
        ws.update_cell(cell.row, 13, datetime.now().strftime("%d.%m.%Y %H:%M"))

        if query.message.caption:
            await query.edit_message_caption(
                query.message.caption + "\n\n✅ <b>Погоджено директором</b>",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                query.message.text + "\n\n✅ <b>Погоджено директором</b>",
                parse_mode="HTML"
            )

        data = context.application.bot_data.get(f"repair_{record_id}", {})
        accountant_text = (
            f"💳 <b>До оплати — заявка #{record_id}</b>\n\n"
            f"📋 Тип: {data.get('op_type','')}\n"
            f"🚗 Машина: {data.get('vehicle_type','')} {data.get('vehicle','')}\n"
            f"📝 {data.get('description','')}\n"
            f"💰 Сума: {data.get('amount','')} грн\n"
            f"💳 Оплата: {data.get('payment','')}\n"
            f"🏪 {data.get('contractor','')}\n"
            f"✅ Погоджено директором"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Оплачено", callback_data=f"accountant_paid_{record_id}")]
        ])

        file_id   = data.get("file_id")
        file_type = data.get("file_type")

        if file_id:
            if file_type == "document":
                await context.bot.send_document(
                    chat_id=ACCOUNTANT_ID, document=file_id,
                    caption=accountant_text, parse_mode="HTML", reply_markup=keyboard
                )
            else:
                await context.bot.send_photo(
                    chat_id=ACCOUNTANT_ID, photo=file_id,
                    caption=accountant_text, parse_mode="HTML", reply_markup=keyboard
                )
        else:
            await context.bot.send_message(
                chat_id=ACCOUNTANT_ID, text=accountant_text,
                parse_mode="HTML", reply_markup=keyboard
            )

        manager_id = data.get("manager_id")
        if manager_id:
            await context.bot.send_message(
                chat_id=manager_id,
                text=f"✅ Заявку #{record_id} <b>погоджено директором</b>. Передано бухгалтеру на оплату.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"director_approve error: {e}")

async def director_reject(query, context, record_id):
    if query.from_user.id != DIRECTOR_ID:
        await query.answer("⛔ Тільки директор може відхиляти.", show_alert=True)
        return
    try:
        ws = get_repairs_sheet()
        cell = ws.find(str(record_id))
        ws.update_cell(cell.row, 12, "Відхилено директором")

        if query.message.caption:
            await query.edit_message_caption(
                query.message.caption + "\n\n❌ <b>Відхилено директором</b>",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                query.message.text + "\n\n❌ <b>Відхилено директором</b>",
                parse_mode="HTML"
            )

        data = context.application.bot_data.get(f"repair_{record_id}", {})
        manager_id = data.get("manager_id")
        if manager_id:
            await context.bot.send_message(
                chat_id=manager_id,
                text=f"❌ Заявку #{record_id} <b>відхилено директором</b>.",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"director_reject error: {e}")

# ══════════════════════════════════════════════
# ПІДТВЕРДЖЕННЯ БУХГАЛТЕРА
# ══════════════════════════════════════════════
async def accountant_paid(query, context, record_id):
    if query.from_user.id != ACCOUNTANT_ID:
        await query.answer("⛔ Тільки бухгалтер може підтверджувати оплату.", show_alert=True)
        return
    try:
        ws = get_repairs_sheet()
        cell = ws.find(str(record_id))
        ws.update_cell(cell.row, 12, "Оплачено")
        ws.update_cell(cell.row, 14, datetime.now().strftime("%d.%m.%Y %H:%M"))

        if query.message.caption:
            await query.edit_message_caption(
                query.message.caption + "\n\n✅ <b>Оплачено</b>",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                query.message.text + "\n\n✅ <b>Оплачено</b>",
                parse_mode="HTML"
            )

        data = context.application.bot_data.get(f"repair_{record_id}", {})
        manager_id = data.get("manager_id")
        if manager_id:
            await context.bot.send_message(
                chat_id=manager_id,
                text=f"✅ Заявку #{record_id} <b>оплачено</b> бухгалтером.",
                parse_mode="HTML"
            )

        op = data.get("op_type", "")
        if op == "purchase":
            name  = data.get("stock_name", "")
            qty   = float(data.get("stock_qty", 0) or 0)
            unit  = data.get("stock_unit", "")
            amount = float(data.get("amount", 0) or 0)
            price_per_unit = round(amount / qty, 2) if qty else 0
            if name and qty:
                update_stock(name, unit, qty, price_per_unit)

    except Exception as e:
        logger.error(f"accountant_paid error: {e}")

# ══════════════════════════════════════════════
# СПИСАННЯ
# ══════════════════════════════════════════════
async def submit_writeoff(query, context):
    try:
        item    = context.user_data.get("stock_item", {})
        qty     = context.user_data.get("writeoff_qty", 0)
        vehicle = context.user_data.get("vehicle", "")
        vtype   = context.user_data.get("vehicle_type", "")

        name  = item.get("Позиція", "")
        unit  = item.get("Одиниця", "")
        price = float(item.get("Ціна за одиницю", 0) or 0)
        total = round(qty * price, 2)

        context.user_data["step"] = "confirm_writeoff"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Так, відправити", callback_data="confirm_data"),
                InlineKeyboardButton("✏️ Виправити", callback_data="back_main"),
            ]
        ])
        await query.edit_message_text(
            f"<b>Перевірте дані списання:</b>\n\n"
            f"📤 Тип: Списання\n"
            f"📦 Позиція: {name}\n"
            f"📊 Кількість: {qty} {unit}\n"
            f"🚗 Машина: {vtype} {vehicle}\n"
            f"💰 Вартість: {total} грн\n\n"
            f"Відправити директору на погодження?",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        context.user_data.update({
            "op_type": "writeoff",
            "description": f"Списання зі складу: {name} {qty} {unit}",
            "amount": str(total),
            "payment": "—",
            "contractor": "Склад",
            "invoice": "",
            "date": datetime.now().strftime("%d.%m.%Y"),
        })

    except Exception as e:
        logger.error(f"submit_writeoff error: {e}")

# ══════════════════════════════════════════════
# ЗВІТ /report
# ══════════════════════════════════════════════
async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in MANAGER_IDS + [DIRECTOR_ID]:
        return
    try:
        ws = get_repairs_sheet()
        records = ws.get_all_records()
        current_month = datetime.now().strftime("%m.%Y")

        paid = [r for r in records
                if r.get("Статус") == "Оплачено"
                and current_month in str(r.get("Дата подачі", ""))]

        if not paid:
            await update.message.reply_text(f"За {current_month} оплачених заявок немає.")
            return

        by_vehicle = {}
        for r in paid:
            v = r.get("Машина", "невідомо")
            amount = float(str(r.get("Сума", 0)).replace(" ", "") or 0)
            by_vehicle[v] = by_vehicle.get(v, 0) + amount

        total = sum(by_vehicle.values())
        top5  = sorted(by_vehicle.items(), key=lambda x: x[1], reverse=True)[:5]
        cash  = sum(float(str(r.get("Сума",0)).replace(" ","") or 0)
                    for r in paid if r.get("Форма оплати") == "готівка")
        bank  = total - cash

        lines = [f"📊 <b>Звіт за {current_month}</b>\n"]
        lines.append(f"💰 Загальна сума: <b>{total:,.0f} грн</b>")
        lines.append(f"💵 Готівка: {cash:,.0f} грн")
        lines.append(f"🏦 Безнал: {bank:,.0f} грн")
        lines.append(f"📋 Заявок: {len(paid)}\n")
        lines.append("<b>ТОП-5 машин:</b>")
        for i, (v, amt) in enumerate(top5, 1):
            lines.append(f"{i}. {v} — {amt:,.0f} грн")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"report_cmd error: {e}")
        await update.message.reply_text("Помилка при формуванні звіту.")

# ══════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не встановлено! Додайте змінну BOT_TOKEN в Railway.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new",   new_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.PHOTO,
        handle_file
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text
    ))

    logger.info("Repair bot started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
