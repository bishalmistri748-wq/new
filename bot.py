#!/usr/bin/env python3
# Telegram License + Encryption Bot
# Permanent files intended: bot.py + enc_tool_online.py
#
# Environment:
#   BOT_TOKEN=...
#   ENC_SERVER_URL=https://your-api.vercel.app
#   ADMIN_SECRET=...
#   ADMIN_TELEGRAM_ID=123456789
#
# Install:
#   pip install python-telegram-bot
#   pip install pycryptodome cython
#
# Run:
#   python bot.py

import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent
ENC_TOOL = BASE_DIR / "enc_tool_online.py"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8339077391:AAGrBWVz5AKBvBHbJpQspkyqb75la3I0LuQ").strip()
SERVER_URL = os.environ.get("ENC_SERVER_URL", "https://vercel-firebase-license-api.vercel.app").strip().rstrip("/")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "SlFVoNDazRPb3A0n1DvWmXuEdcfoIfiMOjL7diW-hVLR-u4DC9MgqkpVK8JSN2NyUrvYVBC-wviN5D6KBoIzlwRvsw4VC9hYR9yi2V6yPzUV4sHhClDRqPVufwqivGXGEUxX9gY74ZxS9m1jSrNq9jP_PWzJwecJox0BeGBS9DA3yuwuVzTG5XLqI2r6pXEaY4CgNNbhz0jkpoKVzWiwnEhAbgFhTVHpaafmXJo1Ipx0PIVklKZdmjVf1t1Pgt-IaYC1ZVq394JxmT6uKTjGdd1Cm7RqOvJyEYNtlx5MfoRglVBJTbIRpSVGUN7cL-bfhGKNR3tarOSZI4eM9EL9rQ").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_TELEGRAM_ID", "5159972988").strip()

try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else 0
except ValueError:
    ADMIN_ID = 0

# Conversation states
KEY_INPUT, ENCRYPT_KEY, ENCRYPT_FILE, ACTIVATE_APP_ID, MY_LICENSE_KEY, STATUS_KEY, REVOKE_KEY, UNREVOKE_KEY, RESET_KEY = range(9)
CREATE_DAYS, CREATE_CUSTOM_DAYS, CREATE_DEVICES, CREATE_APP_ID = range(9, 13)

MAX_TELEGRAM_FILE_MB = 2000  # Telegram Bot API limit depends on deployment/API mode.
EDIT_INTERVAL = 2.0

# The encryption tool must receive the exact Telegram filename.
# A single lock prevents two simultaneous jobs from overwriting same-name files.
ENCRYPT_LOCK = asyncio.Lock()


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID and user_id == ADMIN_ID)


def human_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    n = float(size)
    for unit in units:
        if n < 1024 or unit == units[-1]:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{size} B"


def fmt_dt(ms) -> str:
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return "Unknown"


def remaining(ms) -> str:
    try:
        seconds = max(0, int(int(ms) / 1000 - time.time()))
        d, rem = divmod(seconds, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        if d:
            return f"{d}d {h:02d}h {m:02d}m"
        return f"{h:02d}h {m:02d}m {s:02d}s"
    except Exception:
        return "Unknown"


def extract_license_key(lic: dict) -> str:
    """Read the real license key from common API/Firebase field names."""
    if not isinstance(lic, dict):
        return ""
    for field in ("license_key", "licenseKey", "key", "key_value", "keyValue", "license"):
        value = lic.get(field)
        if value is not None:
            value = str(value).strip()
            if value and value.lower() != "unknown":
                return value
    return ""


def mask_license_key(key: str) -> str:
    return "ENC-XXXXXXXXXXXX" if str(key or "").strip().startswith("ENC-") else "XXXXXXXXXXXX"


def license_status_text(lic: dict, key: str | None = None) -> str:
    key = key or extract_license_key(lic) or "Unknown"
    revoked = lic.get("revoked") is True
    expires = int(lic.get("expires_at", 0) or 0)
    devices = lic.get("devices") or {}
    limit = int(lic.get("max_devices", 1) or 0)

    if revoked:
        status = "🚫 REVOKED"
    elif expires and int(time.time() * 1000) >= expires:
        status = "⛔ EXPIRED"
    else:
        status = "🟢 ACTIVE"

    device_text = "♾️ Unlimited" if limit == 0 else f"{len(devices)} / {limit}"

    return (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "  🔎 LICENSE DETAILS\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"🔑 Key: `{key}`\n"
        f"🆔 App ID: `{lic.get('app_id', '')}`\n"
        f"📅 Created: {fmt_dt(lic.get('created_at'))}\n"
        f"⏳ Expires: {fmt_dt(expires)}\n"
        f"⌛ Remaining: {remaining(expires)}\n"
        f"📱 Devices: {device_text}\n"
        f"📌 Status: {status}"
    )


def main_keyboard(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔐 Encrypt File", callback_data="encrypt"),
            InlineKeyboardButton("🔑 Activate / Check License", callback_data="activate"),
        ],
        [
            InlineKeyboardButton("📋 My License", callback_data="my_license"),
            InlineKeyboardButton("❓ Help", callback_data="help"),
        ],
        [
            InlineKeyboardButton("➕ Create License", callback_data="create"),
            InlineKeyboardButton("🔎 License Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton("📋 List Licenses", callback_data="list"),
        ],
    ]
    if admin:
        rows += [
            [
                InlineKeyboardButton("🚫 Revoke License", callback_data="revoke"),
                InlineKeyboardButton("✅ Unrevoke License", callback_data="unrevoke"),
            ],
            [
                InlineKeyboardButton("🔄 Reset Devices", callback_data="reset"),
            ],
        ]
    return InlineKeyboardMarkup(rows)


def back_keyboard(admin: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Main Menu", callback_data="menu")]]
    )


async def api_request(method: str, path: str, payload=None, admin=False, timeout=30):
    if not SERVER_URL:
        raise RuntimeError("ENC_SERVER_URL is not configured.")

    url = SERVER_URL + path
    headers = {"Content-Type": "application/json"}
    if admin:
        if not ADMIN_SECRET:
            raise RuntimeError("ADMIN_SECRET is not configured.")
        headers["x-admin-secret"] = ADMIN_SECRET

    data = None
    if payload is not None:
        data = json.dumps(payload).encode()

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    def do():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, json.loads(raw or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw or "{}")
            except Exception:
                body = {"error": raw or str(e)}
            return e.code, body

    return await asyncio.to_thread(do)


async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except Exception:
        pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    admin = is_admin(update.effective_user.id)
    text = (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "     🔐 SECURE CENTER\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "Welcome. Select an option below."
    )
    if update.callback_query:
        await safe_edit(update.callback_query.message, text, main_keyboard(admin))
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_keyboard(admin)
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.message.edit_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "     🔐 SECURE CENTER\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Operation cancelled.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(is_admin(q.from_user.id)),
        )
    elif update.message:
        await update.message.reply_text(
            "Operation cancelled.",
            reply_markup=main_keyboard(is_admin(update.effective_user.id)),
        )
    return ConversationHandler.END


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await start(update, context)


async def activate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text(
        "🔑 **Activate / Check License**\n\n"
        "Send your license key:",
        parse_mode="Markdown",
        reply_markup=back_keyboard(is_admin(q.from_user.id)),
    )
    return KEY_INPUT


async def activate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    if not key:
        await update.message.reply_text("❌ Send a valid license key.")
        return KEY_INPUT

    context.user_data["activate_key"] = key
    await update.message.reply_text(
        "🆔 Now send the App ID used when the license was created.",
        parse_mode="Markdown",
    )
    return ACTIVATE_APP_ID

async def encrypt_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["encrypt_key"] = None
    await q.message.edit_text(
        "🔐 **ENCRYPT FILE**\n\n"
        "Step 1/2 — Send your license key:",
        parse_mode="Markdown",
        reply_markup=back_keyboard(is_admin(q.from_user.id)),
    )
    return ENCRYPT_KEY


async def encrypt_key_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["encrypt_key"] = update.message.text.strip()
    await update.message.reply_text(
        "📤 **Step 2/2 — Upload your file now.**\n\n"
        "Supported by the existing encryption tool: `.py`, `.so`, `.pyd`, `.dll`, `.exe`.",
        parse_mode="Markdown",
    )
    return ENCRYPT_FILE


async def run_encrypt(input_path: Path, key: str, app_id: str, original_filename: str):
    """Run enc_tool_online using the exact uploaded filename.

    No per-job directory and no renamed tg_* filename are used. The input path
    and the final output path are both directly under BASE_DIR.
    """
    original = Path(original_filename).name
    if Path(original).suffix.lower() == ".py":
        final_name = f"{Path(original).stem}.enc"
        tool_output_name = final_name
    else:
        final_name = f"{original}.enc"
        tool_output_name = final_name

    output_path = BASE_DIR / final_name

    env = os.environ.copy()
    env["ENC_SERVER_URL"] = SERVER_URL

    # Run the original Python encryption tool directly.
    cmd = [
        sys.executable,
        str(ENC_TOOL),
        str(input_path),
        "--key",
        key,
        "--app-id",
        app_id,
        "--server",
        SERVER_URL,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(BASE_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )

    output = bytearray()
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        output.extend(chunk)

    rc = await proc.wait()
    tool_output_path = BASE_DIR / tool_output_name

    # enc_tool_online writes the result beside the exact input filename.
    # Do not rename it to a tg_* name: the user's original basename is the
    # required final filename. If it already has the expected name, keep it.
    if rc == 0 and tool_output_path.exists():
        output_path = tool_output_path

    return rc, output.decode("utf-8", "replace"), output_path


async def encryption_progress(message, filename, size, started):
    while True:
        await asyncio.sleep(EDIT_INTERVAL)
        elapsed = int(time.monotonic() - started)
        text = (
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "   🔐 ENCRYPTING...\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"📄 File: `{filename}`\n"
            f"📦 Size: `{human_size(size)}`\n"
            f"⏱️ Actual time: `{elapsed}s`\n\n"
            "⚙️ Encryption is still running...\n"
            "Please wait; your input file is being kept until the `.enc` file is ready."
        )
        await safe_edit(message, text)


async def activate_app_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("activate_key", "")
    app_id = update.message.text.strip()
    if not key or not app_id:
        await update.message.reply_text("❌ Key/App ID missing.")
        return ConversationHandler.END

    device_id = f"telegram:{update.effective_user.id}"
    try:
        status, body = await api_request(
            "POST",
            "/verify",
            {"license_key": key, "app_id": app_id, "device_id": device_id},
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ **API Error**\n`{str(e)[:500]}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(is_admin(update.effective_user.id)),
        )
        return ConversationHandler.END

    if status == 200 and body.get("ok"):
        lic = {
            "license_key": body.get("license_key", key),
            "app_id": body.get("app_id", app_id),
            "expires_at": body.get("expires_at"),
            "max_devices": body.get("max_devices"),
        }
        context.user_data["license_key"] = key
        context.user_data["app_id"] = app_id
        await update.message.reply_text(
            "✅ **LICENSE VERIFIED**\n\n" + license_status_text(lic, key),
            parse_mode="Markdown",
            reply_markup=main_keyboard(is_admin(update.effective_user.id)),
        )
    else:
        reason = body.get("reason") or body.get("error") or "verification_failed"
        labels = {
            "invalid_license": "Invalid license key",
            "revoked": "License is revoked",
            "expired": "License has expired",
            "app_mismatch": "App ID does not match",
            "device_limit": "Device limit reached",
            "missing_fields": "Required fields are missing",
        }
        await update.message.reply_text(
            f"❌ **LICENSE REJECTED**\n\nReason: `{labels.get(reason, reason)}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(is_admin(update.effective_user.id)),
        )
    context.user_data.pop("activate_key", None)
    return ConversationHandler.END


async def encrypt_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("encrypt_key")
    if not key:
        await update.message.reply_text("❌ Encryption session expired. Start again.")
        return ConversationHandler.END

    tg_file = update.message.document
    if not tg_file:
        await update.message.reply_text("📤 Please upload the file as a document.")
        return ENCRYPT_FILE

    filename = Path(update.message.document.file_name or "input").name
    if not filename or filename in (".", ".."):
        await update.message.reply_text("❌ Invalid filename.")
        return ENCRYPT_FILE

    allowed = {".py", ".so", ".pyd", ".dll", ".exe"}
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        await update.message.reply_text(
            "❌ Unsupported file type.\n\nAllowed: `.py`, `.so`, `.pyd`, `.dll`, `.exe`",
            parse_mode="Markdown",
        )
        return ENCRYPT_FILE

    file_size = int(tg_file.file_size or 0)
    if file_size > MAX_TELEGRAM_FILE_MB * 1024 * 1024:
        await update.message.reply_text("❌ File is too large for this bot.")
        return ConversationHandler.END

    # IMPORTANT: use the exact Telegram filename directly under BASE_DIR.
    # No .enc_job_* directory and no tg_* filename is created.
    # The lock prevents simultaneous uploads with the same filename from colliding.
    if filename in {"bot.py", "enc_tool_online.py"}:
        await update.message.reply_text("❌ This filename is reserved by the bot.")
        return ConversationHandler.END

    input_path = BASE_DIR / filename

    try:
        async with ENCRYPT_LOCK:
            status_msg = await update.message.reply_text(
                "📥 **Receiving file...**",
                parse_mode="Markdown",
            )
            await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

            remote = await tg_file.get_file()
            await remote.download_to_drive(custom_path=str(input_path))

            # App ID follows the existing tool's filename-based convention.
            app_id = Path(filename).stem.strip() or "telegram"
            started = time.monotonic()

            await safe_edit(
                status_msg,
                "╭━━━━━━━━━━━━━━━━━━━━╮\n"
                "   🔐 ENCRYPTION STARTED\n"
                "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"📄 File: `{filename}`\n"
                f"📦 Size: `{human_size(file_size)}`\n"
                "⏱️ Actual time: `0s`\n\n"
                "⚙️ Processing...",
            )

            progress_task = asyncio.create_task(
                encryption_progress(status_msg, filename, file_size, started)
            )

            try:
                rc, tool_output, output_path = await run_encrypt(
                    input_path, key, app_id, filename
                )
            finally:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            elapsed = time.monotonic() - started

            if rc != 0 or not output_path.exists():
                # Keep the input after failure for safe retry/inspection.
                detail = tool_output[-1800:].strip() or "Encryption failed without output."
                await safe_edit(
                    status_msg,
                    "❌ **ENCRYPTION FAILED**\n\n"
                    f"📄 File: `{filename}`\n"
                    f"📦 Size: `{human_size(file_size)}`\n"
                    f"⏱️ Actual time: `{elapsed:.1f}s`\n\n"
                    f"```text\n{detail}\n```",
                )
                return ConversationHandler.END

            await safe_edit(
                status_msg,
                "✅ **ENCRYPTION COMPLETE**\n\n"
                f"📄 Input: `{filename}`\n"
                f"📦 Size: `{human_size(file_size)}`\n"
                f"⏱️ Actual time: `{elapsed:.1f}s`\n\n"
                "📤 Sending `.enc` file...",
            )

            await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
            with output_path.open("rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=output_path.name,
                    caption=(
                        "🔐 **ENCRYPTED FILE READY**\n\n"
                        f"📄 `{output_path.name}`\n"
                        f"⏱️ Encryption time: `{elapsed:.1f}s`\n"
                        "✅ Delivery completed."
                    ),
                    parse_mode="Markdown",
                )

            # Delete only after successful Telegram send.
            try:
                input_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
            except Exception:
                pass

            await status_msg.edit_text(
                "✅ **DONE**\n\n"
                "The encrypted `.enc` file was delivered successfully.\n"
                "Temporary input/output files have been cleaned up.",
                parse_mode="Markdown",
                reply_markup=main_keyboard(is_admin(update.effective_user.id)),
            )

    except Exception as e:
        # Do NOT delete the input on unexpected failure.
        try:
            await update.message.reply_text(
                "⚠️ **Unexpected error**\n\n"
                f"`{str(e)[:700]}`\n\n"
                "Your uploaded input file is being kept for safety.",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    return ConversationHandler.END


async def my_license_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text(
        "📋 **MY LICENSE**\n\nSend your license key:",
        parse_mode="Markdown",
        reply_markup=back_keyboard(is_admin(q.from_user.id)),
    )
    return MY_LICENSE_KEY


async def my_license_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    context.user_data["my_license_key"] = key
    await update.message.reply_text(
        "🆔 Now send the App ID used when the license was created."
    )
    return STATUS_KEY

async def status_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text(
        "🔎 **LICENSE STATUS**\n\nSend the license key:",
        parse_mode="Markdown",
        reply_markup=back_keyboard(is_admin(q.from_user.id)),
    )
    return STATUS_KEY


async def status_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # STATUS_KEY is also used as the second step of My License/Activate.
    if context.user_data.get("activate_key"):
        return await activate_app_id(update, context)

    if context.user_data.get("my_license_key"):
        key = context.user_data.pop("my_license_key")
        app_id = update.message.text.strip()
        device_id = f"telegram:{update.effective_user.id}"
        try:
            status, body = await api_request(
                "POST",
                "/verify",
                {"license_key": key, "app_id": app_id, "device_id": device_id},
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ API Error: `{str(e)[:500]}`", parse_mode="Markdown"
            )
            return ConversationHandler.END

        if status == 200 and body.get("ok"):
            lic = {
                "license_key": body.get("license_key", key),
                "app_id": body.get("app_id", app_id),
                "expires_at": body.get("expires_at"),
                "max_devices": body.get("max_devices"),
            }
            await update.message.reply_text(
                license_status_text(lic, key),
                parse_mode="Markdown",
                reply_markup=main_keyboard(is_admin(update.effective_user.id)),
            )
        else:
            reason = body.get("reason") or body.get("error") or "verification_failed"
            await update.message.reply_text(
                f"❌ License check failed: `{reason}`",
                parse_mode="Markdown",
                reply_markup=main_keyboard(is_admin(update.effective_user.id)),
            )
        return ConversationHandler.END

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return ConversationHandler.END

    key = update.message.text.strip()
    try:
        status, body = await api_request("GET", "/admin/status/" + urllib.parse.quote(key, safe=""), admin=True)
    except Exception as e:
        await update.message.reply_text(f"❌ API Error: `{str(e)[:500]}`", parse_mode="Markdown")
        return ConversationHandler.END

    if status == 200 and body.get("ok"):
        await update.message.reply_text(
            license_status_text(body, key),
            parse_mode="Markdown",
            reply_markup=main_keyboard(True),
        )
    else:
        await update.message.reply_text(
            f"❌ License not found.\n`{body.get('error', body)}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(True),
        )
    return ConversationHandler.END


async def create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.pop("create_days", None)
    await q.message.edit_text(
        "➕ **CREATE LICENSE**\n\n"
        "Choose duration:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("1 Day", callback_data="days:1"),
                InlineKeyboardButton("7 Days", callback_data="days:7"),
            ],
            [
                InlineKeyboardButton("15 Days", callback_data="days:15"),
                InlineKeyboardButton("30 Days", callback_data="days:30"),
            ],
            [InlineKeyboardButton("Custom Days", callback_data="days:custom")],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu")],
        ]),
    )
    return CREATE_DAYS


async def create_days_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    value = q.data.split(":", 1)[1]
    if value == "custom":
        await q.message.edit_text("🗓️ Send the custom number of days (1 or more):")
        return CREATE_CUSTOM_DAYS

    context.user_data["create_days"] = int(value)
    return await ask_devices(q.message)


async def create_custom_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())
        if days < 1 or days > 36500:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number of days (1–36500).")
        return CREATE_CUSTOM_DAYS

    context.user_data["create_days"] = days
    return await ask_devices(update.message)


async def ask_devices(message):
    await message.reply_text(
        "📱 **Device limit**\n\nChoose:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("1 Device", callback_data="devices:1"),
                InlineKeyboardButton("Unlimited", callback_data="devices:0"),
            ],
        ]),
    )
    return CREATE_DEVICES


async def create_devices_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    limit = int(q.data.split(":", 1)[1])
    context.user_data["create_devices"] = limit
    await q.message.edit_text(
        "🆔 **App ID**\n\nSend only the App ID/name.\n\n"
        "Example: `myapp`",
        parse_mode="Markdown",
    )
    return CREATE_APP_ID


async def create_app_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    app_id = update.message.text.strip()
    if not app_id or len(app_id) > 100:
        await update.message.reply_text("❌ Invalid App ID. Use 1–100 characters.")
        return CREATE_APP_ID

    days = context.user_data.get("create_days")
    limit = context.user_data.get("create_devices", 1)

    try:
        status, body = await api_request(
            "POST",
            "/admin/create-key",
            {"days": days, "max_devices": limit, "app_id": app_id},
            admin=True,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ API Error: `{str(e)[:500]}`", parse_mode="Markdown")
        return ConversationHandler.END

    if status == 201 and body.get("ok"):
        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━╮\n"
            "   ✅ LICENSE CREATED\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"🔑 Key: `{body.get('license_key')}`\n"
            f"🆔 App ID: `{body.get('app_id')}`\n"
            f"📅 Created: {fmt_dt(body.get('created_at'))}\n"
            f"⏳ Expires: {fmt_dt(body.get('expires_at'))}\n"
            f"⌛ Duration: {days} day(s)\n"
            f"📱 Devices: {'Unlimited' if limit == 0 else limit}",
            parse_mode="Markdown",
            reply_markup=main_keyboard(is_admin(update.effective_user.id)),
        )
    else:
        await update.message.reply_text(
            f"❌ Could not create license.\n`{body.get('error', body)}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(is_admin(update.effective_user.id)),
        )
    context.user_data.clear()
    return ConversationHandler.END


async def admin_action_start(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.edit_text("❌ Admin only.")
        return ConversationHandler.END
    context.user_data["admin_action"] = action
    labels = {
        "revoke": "🚫 Revoke License",
        "unrevoke": "✅ Unrevoke License",
        "reset": "🔄 Reset Devices",
    }
    await q.message.edit_text(
        f"{labels[action]}\n\nSend the license key:",
        reply_markup=back_keyboard(True),
    )
    return {"revoke": REVOKE_KEY, "unrevoke": UNREVOKE_KEY, "reset": RESET_KEY}[action]


async def do_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return ConversationHandler.END

    key = update.message.text.strip()
    endpoint = {
        "revoke": "/admin/revoke/",
        "unrevoke": "/admin/unrevoke/",
        "reset": "/admin/reset-devices/",
    }[action]

    try:
        status, body = await api_request(
            "POST",
            endpoint + urllib.parse.quote(key, safe=""),
            admin=True,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ API Error: `{str(e)[:500]}`", parse_mode="Markdown")
        return ConversationHandler.END

    if status == 200 and body.get("ok"):
        messages = {
            "revoke": "🚫 License revoked successfully.",
            "unrevoke": "✅ License unrevoked successfully.",
            "reset": "🔄 Device bindings reset successfully.",
        }
        await update.message.reply_text(
            messages[action],
            reply_markup=main_keyboard(True),
        )
    else:
        await update.message.reply_text(
            f"❌ Operation failed.\n`{body.get('error', body)}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(True),
        )
    context.user_data.clear()
    return ConversationHandler.END


async def revoke_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await do_admin_action(update, context, "revoke")


async def unrevoke_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await do_admin_action(update, context, "unrevoke")


async def reset_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await do_admin_action(update, context, "reset")


async def list_licenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    admin = is_admin(q.from_user.id)

    try:
        # /admin/list is protected by ADMIN_SECRET. The bot calls it server-side
        # and masks the real key for normal Telegram users.
        status, body = await api_request("GET", "/admin/list", admin=True)
    except Exception as e:
        await q.message.edit_text(
            f"❌ API Error: `{str(e)[:500]}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(admin),
        )
        return

    if status != 200 or not body.get("ok"):
        await q.message.edit_text(
            f"❌ Could not load licenses.\n`{body.get('error', body)}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(admin),
        )
        return

    licenses = body.get("licenses") or []
    if not licenses:
        await q.message.edit_text(
            "📋 **LICENSE LIST**\n\nNo licenses found.",
            parse_mode="Markdown",
            reply_markup=main_keyboard(admin),
        )
        return

    pages = []
    chunk = []
    for i, lic in enumerate(licenses, 1):
        key = extract_license_key(lic)
        display_key = key if admin else mask_license_key(key)
        expires = int(lic.get("expires_at", 0) or 0)
        devices = lic.get("devices") or {}
        limit = int(lic.get("max_devices", 1) or 0)
        if lic.get("revoked"):
            status_txt = "🚫 Revoked"
        elif expires and int(time.time() * 1000) >= expires:
            status_txt = "⛔ Expired"
        else:
            status_txt = "🟢 Active"

        block = (
            f"**#{i}**\n"
            f"🔑 Key - `{display_key}`\n"
            f"🆔 `{lic.get('app_id', '')}`\n"
            f"⏳ {remaining(expires)} — {fmt_dt(expires)}\n"
            f"📱 {'♾️ Unlimited' if limit == 0 else f'{len(devices)} / {limit}'}\n"
            f"{status_txt}\n"
        )
        if sum(len(x) for x in chunk) + len(block) > 3200 and chunk:
            pages.append("📋 **LICENSE LIST**\n\n" + "\n".join(chunk))
            chunk = []
        chunk.append(block)
    if chunk:
        pages.append("📋 **LICENSE LIST**\n\n" + "\n".join(chunk))

    context.user_data["license_pages"] = pages
    context.user_data["license_page"] = 0
    context.user_data["license_list_admin"] = admin
    await send_license_page(q.message, context, 0, admin)


async def send_license_page(message, context, page, admin):
    pages = context.user_data.get("license_pages", [])
    if not pages:
        return
    page = max(0, min(page, len(pages) - 1))
    context.user_data["license_page"] = page

    buttons = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data="page:prev"))
    if page < len(pages) - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="page:next"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu")])

    await safe_edit(message, pages[page], InlineKeyboardMarkup(buttons))


async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id) and not context.user_data.get("license_pages"):
        return
    page = int(context.user_data.get("license_page", 0))
    pages = context.user_data.get("license_pages", [])
    if q.data == "page:next":
        page += 1
    else:
        page -= 1
    if pages:
        await send_license_page(q.message, context, page, bool(context.user_data.get("license_list_admin", False)))


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "        ❓ HELP\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🔐 Encrypt File — encrypt a supported file using your license.\n"
        "🔑 Activate / Check License — verify a license against the online API.\n"
        "📋 My License — show the license information available through verification.\n"
        "➕ Create License — create a key through the configured license API.\n"
        "🔎 License Status — inspect a key (API admin endpoint).\n"
        "📋 List Licenses — list licenses (current API endpoint is admin-protected).\n\n"
        "Admin controls:\n"
        "🚫 Revoke • ✅ Unrevoke • 🔄 Reset Devices"
    )
    await q.message.edit_text(
        text, parse_mode="Markdown", reply_markup=main_keyboard(is_admin(q.from_user.id))
    )


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""

    if data == "menu":
        return await menu_callback(update, context)
    if data == "help":
        return await help_callback(update, context)
    if data == "encrypt":
        return await encrypt_start(update, context)
    if data == "activate":
        return await activate_start(update, context)
    if data == "my_license":
        return await my_license_start(update, context)
    if data == "status":
        return await status_start(update, context)
    if data == "create":
        return await create_start(update, context)
    if data == "list":
        return await list_licenses(update, context)
    if data == "revoke":
        return await admin_action_start(update, context, "revoke")
    if data == "unrevoke":
        return await admin_action_start(update, context, "unrevoke")
    if data == "reset":
        return await admin_action_start(update, context, "reset")
    if data.startswith("days:"):
        return await create_days_callback(update, context)
    if data.startswith("devices:"):
        return await create_devices_callback(update, context)
    if data.startswith("page:"):
        return await list_page_callback(update, context)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # Keep user-facing errors clean; full traceback stays in process logs.
    print(f"[BOT ERROR] {context.error!r}")


def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    if not SERVER_URL:
        raise RuntimeError("ENC_SERVER_URL is missing.")
    if not ENC_TOOL.exists():
        raise RuntimeError(f"enc_tool_online.py not found beside bot.py: {ENC_TOOL}")

    app = Application.builder().token(BOT_TOKEN).build()

    # One conversation handler for the multi-step workflows.
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(encrypt_start, pattern="^encrypt$"),
            CallbackQueryHandler(activate_start, pattern="^activate$"),
            CallbackQueryHandler(my_license_start, pattern="^my_license$"),
            CallbackQueryHandler(status_start, pattern="^status$"),
            CallbackQueryHandler(create_start, pattern="^create$"),
            CallbackQueryHandler(
                lambda u, c: admin_action_start(u, c, "revoke"), pattern="^revoke$"
            ),
            CallbackQueryHandler(
                lambda u, c: admin_action_start(u, c, "unrevoke"), pattern="^unrevoke$"
            ),
            CallbackQueryHandler(
                lambda u, c: admin_action_start(u, c, "reset"), pattern="^reset$"
            ),
        ],
        states={
            KEY_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, activate_key),
            ],
            ACTIVATE_APP_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, activate_app_id),
            ],
            ENCRYPT_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, encrypt_key_received),
            ],
            ENCRYPT_FILE: [
                MessageHandler(filters.Document.ALL, encrypt_file_received),
            ],
            MY_LICENSE_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, my_license_key),
            ],
            STATUS_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, status_key),
            ],
            REVOKE_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, revoke_key),
            ],
            UNREVOKE_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, unrevoke_key),
            ],
            RESET_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reset_key),
            ],
            CREATE_DAYS: [
                CallbackQueryHandler(create_days_callback, pattern=r"^days:(1|7|15|30|custom)$"),
            ],
            CREATE_CUSTOM_DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_custom_days),
            ],
            CREATE_DEVICES: [
                CallbackQueryHandler(create_devices_callback, pattern=r"^devices:(0|1)$"),
            ],
            CREATE_APP_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_app_id),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(menu_callback, pattern=r"^menu$"),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_router))
    app.add_error_handler(error_handler)
    return app


if __name__ == "__main__":
    print("🔐 Secure Telegram Bot starting...")
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)
