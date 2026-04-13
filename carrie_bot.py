#!/usr/bin/env python3
"""
neovegacarrie_bot - 本地執行者
位置: Home Workstation
職責: 接收 neovegasherlock_bot 的 JSONL 指令，執行本地動作

使用方式:
    python carrie_bot.py

環境變數:
    CARRIE_BOT_TOKEN: Telegram Bot Token
    VAULT_PATH: Knowledge vault 路徑 (預設: D:\\knowledge-vault)
"""

import os
import json
import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

# 設定
TOKEN = os.getenv("CARRIE_BOT_TOKEN", "8615424711:AAGLoHijlMpqWX7yD_JhJjKeTS0Dd5H5GTg")
VAULT_PATH = Path(os.getenv("VAULT_PATH", "D:\\knowledge-vault"))
RAW_INBOX = VAULT_PATH / "raw" / "inbox"

# 安全驗證：只接受 neovegasherlock_bot 的指令
# Sherlock bot user ID (從 Telegram getMe API 取得後填入)
SHERLOCK_BOT_ID = int(os.getenv("SHERLOCK_BOT_ID", "0"))

# 允許執行的腳本白名單
SCRIPT_WHITELIST = {
	"ingest_graphify": str(VAULT_PATH / "ops" / "scripts" / "ingest_graphify.ps1"),
	"sync_vault": str(VAULT_PATH / "ops" / "scripts" / "sync_vault.ps1"),
}

logging.basicConfig(
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
	level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""/start 指令"""
	await update.message.reply_text(
		"🏠 neovegacarrie_bot 已啟動\n\n"
		"我接收 Sherlock 的 JSONL 指令並執行本地動作。\n"
		"使用 /status 查看狀態。"
	)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""/status 指令"""
	status_text = (
		f"🏠 Carrie Bot 狀態\n"
		f"• Vault 路徑: {VAULT_PATH}\n"
		f"• Inbox: {RAW_INBOX}\n"
		f"• Sherlock Bot ID: {SHERLOCK_BOT_ID or '未設定'}\n"
		f"• 腳本白名單: {', '.join(SCRIPT_WHITELIST.keys())}"
	)
	await update.message.reply_text(status_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""接收並解析 JSONL 指令"""
	if not update.message or not update.message.text:
		return

	text = update.message.text
	sender_id = update.message.from_user.id if update.message.from_user else 0

	# 安全驗證：只接受 Sherlock bot 的訊息（若已設定 ID）
	if SHERLOCK_BOT_ID and sender_id != SHERLOCK_BOT_ID:
		logger.warning(f"拒絕來自未授權 sender {sender_id} 的訊息")
		return

	# 解析 JSONL
	processed = 0
	for line in text.strip().split('\n'):
		line = line.strip()
		if not line:
			continue
		try:
			obj = json.loads(line)
			# 驗證 schema
			if obj.get("schema") != "sherlock/v1":
				continue
			if obj.get("type") == "action" and obj.get("target") in ("aria", "all"):
				action_type = obj.get("action_type")
				payload = obj.get("payload", {})
				await dispatch_action(action_type, payload, update, context)
				processed += 1
		except json.JSONDecodeError:
			pass

	if processed == 0 and text.startswith('{'):
		await update.message.reply_text("⚠️ 未找到有效的 sherlock/v1 指令")


async def dispatch_action(
	action_type: str,
	payload: dict,
	update: Update,
	context: ContextTypes.DEFAULT_TYPE
) -> None:
	"""分發動作到對應處理函式"""
	handlers = {
		"local_scan": handle_local_scan,
		"ingest_url": handle_ingest_url,
		"run_script": handle_run_script,
		"alert": handle_alert,
		"report": handle_report,
	}
	handler = handlers.get(action_type)
	if handler:
		await handler(payload, update, context)
	else:
		await update.message.reply_text(f"⚠️ 未知動作類型: {action_type}")


async def handle_local_scan(payload: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""掃描本地路徑"""
	path = Path(payload.get("path", str(VAULT_PATH)))
	pattern = payload.get("pattern", "*")
	since = payload.get("since")

	await update.message.reply_text(f"🔎 開始掃描: {path}/{pattern}")

	try:
		if not path.exists():
			await update.message.reply_text(f"❌ 路徑不存在: {path}")
			return

		files = list(path.rglob(pattern))
		if since:
			since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
			files = [f for f in files if datetime.fromtimestamp(f.stat().st_mtime, tz=since_dt.tzinfo) >= since_dt]

		result = f"✅ 掃描完成\n路徑: {path}\n模式: {pattern}\n找到: {len(files)} 個檔案"
		if files[:5]:
			result += "\n最新:\n" + "\n".join(f"  • {f.name}" for f in files[:5])
		await update.message.reply_text(result)
	except Exception as e:
		await update.message.reply_text(f"❌ 掃描失敗: {e}")


async def handle_ingest_url(payload: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""下載 URL 並入庫"""
	url = payload.get("url")
	if not url:
		await update.message.reply_text("❌ 缺少 url 參數")
		return

	await update.message.reply_text(f"⏳ 正在入庫: {url}")

	try:
		import trafilatura
		downloaded = trafilatura.fetch_url(url)
		if not downloaded:
			await update.message.reply_text(f"❌ 無法下載: {url}")
			return

		content = trafilatura.extract(downloaded, include_tables=True)
		metadata = trafilatura.extract_metadata(downloaded)
		title = metadata.title if metadata else "Untitled"

		today = datetime.now().strftime("%Y-%m-%d")
		safe_title = "".join(c for c in title if c.isalnum() or c in "_- ")[:50]
		filename = f"{today}_{safe_title}.md"
		filepath = RAW_INBOX / filename

		RAW_INBOX.mkdir(parents=True, exist_ok=True)
		with open(filepath, "w", encoding="utf-8") as f:
			f.write(f"---\ntitle: {title}\nsource: {url}\ndate: {datetime.now().isoformat()}\nbot: carrie\n---\n\n# {title}\n\n{content or ''}")

		await update.message.reply_text(f"✅ 已入庫: {filename}")
	except Exception as e:
		await update.message.reply_text(f"❌ 入庫失敗: {e}")


async def handle_run_script(payload: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""執行本地 PowerShell 腳本（白名單驗證）"""
	script_key = payload.get("script")
	if script_key not in SCRIPT_WHITELIST:
		await update.message.reply_text(f"❌ 腳本不在白名單: {script_key}\n允許: {', '.join(SCRIPT_WHITELIST.keys())}")
		return

	script_path = SCRIPT_WHITELIST[script_key]
	await update.message.reply_text(f"⚙️ 執行腳本: {script_key}")

	try:
		result = subprocess.run(
			["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path],
			capture_output=True, text=True, timeout=300
		)
		status = "✅ 成功" if result.returncode == 0 else "❌ 失敗"
		output = (result.stdout + result.stderr)[:500]
		await update.message.reply_text(f"{status}\n{output}")
	except subprocess.TimeoutExpired:
		await update.message.reply_text("❌ 腳本執行超時 (300s)")
	except Exception as e:
		await update.message.reply_text(f"❌ 執行失敗: {e}")


async def handle_alert(payload: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""顯示警報"""
	msg = payload.get("message", "")
	level = payload.get("level", "info")
	recommend = payload.get("recommend", "")
	emoji = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}.get(level, "📢")
	text = f"{emoji} 警報 [{level.upper()}]\n{msg}"
	if recommend:
		text += f"\n\n建議: {recommend}"
	await update.message.reply_text(text)


async def handle_report(payload: dict, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	"""接收報告"""
	content = payload.get("content", "")
	title = payload.get("title", "報告")
	await update.message.reply_text(f"📊 {title}\n\n{content[:1000]}")


def main() -> None:
	app = Application.builder().token(TOKEN).build()
	app.add_handler(CommandHandler("start", start_command))
	app.add_handler(CommandHandler("status", status_command))
	app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
	logger.info("neovegacarrie_bot 啟動 (polling 模式)")
	app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
	main()
