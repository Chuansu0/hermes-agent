#!/usr/bin/env python3
"""
Hermes Agent - neovegasherlock_bot
執行於 Zeabur VPS，使用 kimi-k2.5 模型分析輸入並產出 JSONL 指令
包含 Web UI 端口供外部連線
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
from aiohttp import web

import aiohttp
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from openai import AsyncOpenAI

# 設定日誌
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# 設定
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8505666076:AAFsPUQCBA7UVdIiw8ItBU3QHDbggI6Payg")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-NQXHpmDhh4SHISdAtMtFEGCcbkJjYEWKQ6xolQbPygsfcrtX6F7wBFYC9bSryTDw")
OPENAI_BASE_URL = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "kimi-k2.5")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "https://n8n.neovega.cc/webhook/sherlock-output")
WEB_PORT = int(os.getenv("PORT", "8080"))

# Bot tokens 與 chat_id 設定（用於直接轉發 JSONL）
CARRIE_BOT_TOKEN = os.getenv("CARRIE_BOT_TOKEN", "8615424711:AAGLoHijlMpqWX7yD_JhJjKeTS0Dd5H5GTg")
CONAN_BOT_TOKEN = os.getenv("CONAN_BOT_TOKEN", "8622712926:AAFjLECd5xFxeveZAlRDmqLyFN3sXRIfpvg")
# 發送者的 chat_id（Sherlock 用對方 bot token 發訊息給這個 chat）
DISPATCH_CHAT_ID = int(os.getenv("DISPATCH_CHAT_ID", "8240891231"))

# 初始化 OpenAI 客戶端
client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
)

# 儲存最近的分析結果
recent_analyses = []

# System Prompt
SYSTEM_PROMPT = """你是 Sherlock，一個專業的分析偵探 AI。

當你完成分析後，必須在回覆末尾附上 JSONL 格式的結構化指令，每行一個 JSON 物件。

格式規範：
- 第一行：type=analysis，包含摘要與信心度
- 後續行：type=action，指定目標 bot 與動作

目標 bot：
- conan：雲端執行者（Zeabur），適合網路搜尋、查詢、警報
- carrie：本地執行者（Home Workstation），適合本地掃描、檔案入庫、腳本執行
- all：廣播給所有 bot

多 Vault 系統（Carrie 管理的本地知識庫）：
- life：生活（日常、家庭、旅遊、飲食）
- work：工作（職場、專案、商業策略）
- rnd：研發（軟體開發、AI/ML、DevOps）
- humanities：人文（歷史、哲學、文學、藝術）
- science：科學（物理、化學、生物、數學）
- medicine：醫學（臨床、藥理、公衛、營養）
- wellbeing：身心靈（冥想、心理學、靈性成長）

當使用者要求入庫連結或文件時，你必須：
1. 分析內容屬於哪個領域
2. 在 payload 中加入 "vault" 欄位指定目標 vault ID
3. 如果不確定，預設使用 "rnd"

支援的 action_type：
- ingest_url：下載連結入庫（payload 需含 url 和 vault）
- local_scan：本地檔案掃描
- run_script：執行白名單腳本
- alert：發送警報
- web_search：網路搜尋

範例 — 使用者說「把這篇冥想文章存起來 https://example.com/meditation」：
{"schema":"sherlock/v1","ts":"2026-04-16T12:00:00Z","session":"abc123","type":"analysis","summary":"使用者要求入庫冥想相關文章到身心靈 vault","confidence":0.95}
{"schema":"sherlock/v1","ts":"2026-04-16T12:00:00Z","session":"abc123","type":"action","target":"carrie","action_type":"ingest_url","payload":{"url":"https://example.com/meditation","vault":"wellbeing"}}

範例 — 使用者說「存這個 AI 論文 https://arxiv.org/xxx」：
{"schema":"sherlock/v1","ts":"2026-04-16T12:00:00Z","session":"abc123","type":"analysis","summary":"AI 論文入庫到研發 vault","confidence":0.95}
{"schema":"sherlock/v1","ts":"2026-04-16T12:00:00Z","session":"abc123","type":"action","target":"carrie","action_type":"ingest_url","payload":{"url":"https://arxiv.org/xxx","vault":"rnd"}}

請確保：
1. 所有 JSON 物件符合 sherlock/v1 schema
2. ts 欄位為 ISO8601 格式
3. session 欄位使用唯一 ID
4. target 欄位正確指定 bot
5. ingest_url 的 payload 必須包含 vault 欄位
"""


# ========== Web UI Handlers ==========

async def web_index(request):
    """Web UI 首頁"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Hermes Agent - Sherlock</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }
            h1 { color: #333; }
            .status { background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 20px 0; }
            .analysis { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
            pre { background: #333; color: #0f0; padding: 10px; overflow-x: auto; }
            .info { color: #666; font-size: 14px; }
        </style>
    </head>
    <body>
        <h1>🔍 Hermes Agent - Sherlock</h1>
        <div class="status">
            <h3>狀態</h3>
            <p>🤖 Bot: neovegasherlock_bot</p>
            <p>🧠 模型: kimi-k2.5</p>
            <p>📡 n8n: Connected</p>
            <p>⏰ 啟動時間: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>
        </div>
        <h2>最近分析記錄</h2>
        <div id="analyses">
            <p class="info">使用 Telegram 與 @neovegasherlock_bot 對話...</p>
        </div>
        <hr>
        <p class="info">Hermes Agent v1.0 | <a href="/api/status">API Status</a></p>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')


async def web_api_status(request):
    """API 狀態端點"""
    return web.json_response({
        "status": "running",
        "bot": "neovegasherlock_bot",
        "model": OPENAI_MODEL,
        "recent_analyses_count": len(recent_analyses),
        "timestamp": datetime.now().isoformat()
    })


async def web_health(request):
    """健康檢查端點 - Zeabur 用"""
    return web.json_response({"status": "ok", "service": "hermes-agent"})


async def start_web_server():
    """啟動 Web Server"""
    app = web.Application()
    app.router.add_get('/', web_index)
    app.router.add_get('/api/status', web_api_status)
    app.router.add_get('/health', web_health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
    await site.start()
    logger.info(f"🌐 Web UI 已啟動: http://0.0.0.0:{WEB_PORT}")


# ========== Telegram Bot Handlers ==========

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start 指令"""
    await update.message.reply_text(
        "🔍 neovegasherlock_bot 已啟動\n\n"
        "我是 Hermes Agent 指揮中心，使用 kimi-k2.5 模型進行分析。\n"
        "發送任何訊息給我，我會分析並產出 JSONL 指令給 Conan 或 Carrie 執行。\n\n"
        "使用 /help 查看說明。"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help 指令"""
    help_text = (
        "🔍 Sherlock 指令說明\n\n"
        "我會分析您的訊息並自動決定：\n"
        "• 雲端任務 → 發送給 Conan (neovegaconan_bot)\n"
        "• 本地任務 → 發送給 Carrie (neovegacarrie_bot)\n\n"
        "支援的任務類型：\n"
        "• local_scan - 本地檔案掃描\n"
        "• ingest_url - URL 內容下載\n"
        "• run_script - 執行本地腳本\n"
        "• web_search - 網路搜尋\n"
        "• alert - 發送警報\n"
        "• report - 產出報告\n\n"
        "直接發送訊息即可開始分析！"
    )
    await update.message.reply_text(help_text)


async def diag_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/diag 診斷所有通道"""
    msg = await update.message.reply_text("🔍 診斷中...")
    results = []
    
    # 1. LLM API
    try:
        r = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        results.append(f"✅ LLM ({OPENAI_MODEL}): OK")
    except Exception as e:
        results.append(f"❌ LLM: {str(e)[:80]}")
    
    # 2. Carrie bot token
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{CARRIE_BOT_TOKEN}/getMe") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results.append(f"✅ Carrie Token: @{data['result']['username']}")
                else:
                    results.append(f"❌ Carrie Token: HTTP {resp.status}")
    except Exception as e:
        results.append(f"❌ Carrie Token: {str(e)[:80]}")
    
    # 3. Conan bot token
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/bot{CONAN_BOT_TOKEN}/getMe") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results.append(f"✅ Conan Token: @{data['result']['username']}")
                else:
                    results.append(f"❌ Conan Token: HTTP {resp.status}")
    except Exception as e:
        results.append(f"❌ Conan Token: {str(e)[:80]}")
    
    # 4. n8n webhook
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(N8N_WEBHOOK_URL, data="ping", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                results.append(f"{'✅' if resp.status == 200 else '⚠️'} n8n Webhook: HTTP {resp.status}")
    except Exception as e:
        results.append(f"❌ n8n Webhook: {str(e)[:80]}")
    
    # 5. Dispatch chat_id 測試（Carrie + Conan）
    for name, token in [("Carrie", CARRIE_BOT_TOKEN), ("Conan", CONAN_BOT_TOKEN)]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": DISPATCH_CHAT_ID, "text": f"🔧 [diag] Sherlock → {name} 通道測試"}
                ) as resp:
                    if resp.status == 200:
                        results.append(f"✅ {name} 轉發 (chat {DISPATCH_CHAT_ID}): OK")
                    else:
                        err = await resp.text()
                        results.append(f"❌ {name} 轉發: {err[:100]}")
        except Exception as e:
            results.append(f"❌ {name} 轉發: {str(e)[:80]}")
    
    await msg.edit_text(
        f"🔍 Sherlock 診斷報告\n\n" + "\n".join(results) +
        f"\n\n📊 分析次數: {len(recent_analyses)}"
    )


async def analyze_with_llm(text: str, session_id: str) -> str:
    """使用 LLM 分析輸入並產出 JSONL"""
    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.7,
            max_tokens=2000,
        )
        
        content = response.choices[0].message.content
        return content
    except Exception as e:
        logger.error(f"LLM 分析錯誤: {e}")
        return None


async def send_to_n8n(jsonl_data: str, session_id: str) -> bool:
    """發送 JSONL 到 n8n webhook（備用）"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                N8N_WEBHOOK_URL,
                headers={"Content-Type": "text/plain"},
                data=jsonl_data,
            ) as response:
                if response.status == 200:
                    logger.info(f"成功發送到 n8n: session={session_id}")
                    return True
                else:
                    logger.warning(f"n8n 回應: {response.status}（將使用直接轉發）")
                    return False
    except Exception as e:
        logger.warning(f"n8n 不可用: {e}（將使用直接轉發）")
        return False


async def dispatch_to_bots(jsonl_lines: list, session_id: str) -> dict:
    """直接透過 Telegram Bot API 轉發 JSONL 給 Carrie/Conan"""
    results = {"carrie": [], "conan": [], "errors": []}
    
    bot_tokens = {
        "aria": CARRIE_BOT_TOKEN,
        "carrie": CARRIE_BOT_TOKEN,
        "conan": CONAN_BOT_TOKEN,
        "all": None,  # 廣播
    }
    
    for line in jsonl_lines:
        try:
            obj = json.loads(line)
            if obj.get("type") != "action":
                continue
            
            target = obj.get("target", "")
            targets_to_send = []
            
            if target == "all":
                targets_to_send = [("carrie", CARRIE_BOT_TOKEN), ("conan", CONAN_BOT_TOKEN)]
            elif target in ("aria", "carrie"):
                targets_to_send = [("carrie", CARRIE_BOT_TOKEN)]
            elif target == "conan":
                targets_to_send = [("conan", CONAN_BOT_TOKEN)]
            
            for bot_name, bot_token in targets_to_send:
                try:
                    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json={
                            "chat_id": DISPATCH_CHAT_ID,
                            "text": line,
                        }) as resp:
                            if resp.status == 200:
                                results[bot_name].append(obj.get("action_type", "unknown"))
                                logger.info(f"✅ 已轉發給 {bot_name}: {obj.get('action_type')}")
                            else:
                                err = await resp.text()
                                results["errors"].append(f"{bot_name}: {resp.status}")
                                logger.error(f"轉發給 {bot_name} 失敗: {err}")
                except Exception as e:
                    results["errors"].append(f"{bot_name}: {str(e)}")
                    logger.error(f"轉發給 {bot_name} 異常: {e}")
        except json.JSONDecodeError:
            continue
    
    return results


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理用戶訊息"""
    if not update.message or not update.message.text:
        return
    
    user_text = update.message.text
    session_id = f"sess_{datetime.now().strftime('%Y%m%d%H%M%S')}_{update.message.message_id}"
    
    # 儲存到最近分析列表
    recent_analyses.append({
        "session_id": session_id,
        "text": user_text[:100],
        "timestamp": datetime.now().isoformat()
    })
    # 只保留最近 10 條
    if len(recent_analyses) > 10:
        recent_analyses.pop(0)
    
    # 顯示分析中
    processing_msg = await update.message.reply_text("🔍 Sherlock 分析中...")
    
    try:
        # 使用 LLM 分析
        llm_response = await analyze_with_llm(user_text, session_id)
        
        if not llm_response:
            await processing_msg.edit_text("❌ 分析失敗，請稍後再試")
            return
        
        # 提取 JSONL 部分（假設在最後）
        jsonl_lines = []
        for line in llm_response.split('\n'):
            line = line.strip()
            if line.startswith('{'):
                jsonl_lines.append(line)
        
        jsonl_data = '\n'.join(jsonl_lines)
        
        if not jsonl_data:
            await processing_msg.edit_text(f"⚠️ 無法產出 JSONL 指令\n\nLLM 回應:\n{llm_response[:500]}")
            return
        
        # 直接轉發 JSONL 給 Carrie/Conan（主要通道）
        dispatch_results = await dispatch_to_bots(jsonl_lines, session_id)
        
        # 也嘗試發送到 n8n（備用/記錄用）
        await send_to_n8n(jsonl_data, session_id)
        
        # 組裝結果訊息
        dispatched = []
        if dispatch_results["carrie"]:
            dispatched.append(f"🏠 Carrie: {', '.join(dispatch_results['carrie'])}")
        if dispatch_results["conan"]:
            dispatched.append(f"☁️ Conan: {', '.join(dispatch_results['conan'])}")
        
        dispatch_text = "\n".join(dispatched) if dispatched else "⚠️ 無目標 bot"
        error_text = f"\n❌ 錯誤: {', '.join(dispatch_results['errors'])}" if dispatch_results["errors"] else ""
        
        await processing_msg.edit_text(
            f"✅ 分析完成！\n\n"
            f"📤 已轉發指令：\n{dispatch_text}{error_text}\n\n"
            f"🆔 Session: {session_id}",
        )
        
    except Exception as e:
        logger.error(f"處理訊息錯誤: {e}")
        await processing_msg.edit_text(f"❌ 處理錯誤: {str(e)}")


async def main_async():
    """非同步主程式"""
    logger.info("neovegasherlock_bot (Hermes Agent) 啟動")
    logger.info(f"模型: {OPENAI_MODEL}")
    logger.info(f"n8n Webhook: {N8N_WEBHOOK_URL}")
    logger.info(f"Web Port: {WEB_PORT}")
    
    # 先啟動 Web Server（Zeabur 健康檢查需要）
    await start_web_server()
    logger.info("✅ Web Server 就緒，開始初始化 Telegram Bot...")
    
    # 建立 Telegram Application
    app = Application.builder().token(TOKEN).build()
    
    # 添加 handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("diag", diag_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 啟動 Telegram Bot
    await app.initialize()
    await app.start()
    logger.info("🤖 Telegram Bot 開始 polling...")
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    # 保持運行
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("正在關閉...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    """主程式入口"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
