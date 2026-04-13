#!/usr/bin/env python3
"""
Hermes Agent - neovegasherlock_bot
執行於 Zeabur VPS，使用 kimi-k2.5 模型分析輸入並產出 JSONL 指令
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional

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

# 初始化 OpenAI 客戶端
client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
)

# System Prompt
SYSTEM_PROMPT = """你是 Sherlock，一個專業的分析偵探 AI。

當你完成分析後，必須在回覆末尾附上 JSONL 格式的結構化指令，每行一個 JSON 物件。

格式規範：
- 第一行：type=analysis，包含摘要與信心度
- 後續行：type=action，指定目標 bot 與動作

目標 bot：
- conan：雲端執行者（Zeabur），適合網路搜尋、查詢、警報
- aria：本地執行者（Home Workstation），適合本地掃描、檔案入庫、腳本執行
- all：廣播給所有 bot

範例輸出：
{"schema":"sherlock/v1","ts":"2026-04-12T12:00:00Z","session":"abc123","type":"analysis","summary":"偵測到異常登入行為","confidence":0.92}
{"schema":"sherlock/v1","ts":"2026-04-12T12:00:00Z","session":"abc123","type":"action","target":"conan","action_type":"alert","payload":{"level":"high","message":"異常 IP 登入"}}
{"schema":"sherlock/v1","ts":"2026-04-12T12:00:00Z","session":"abc123","type":"action","target":"aria","action_type":"local_scan","payload":{"path":"D:\\\\knowledge-vault","pattern":"*.log"}}

請確保：
1. 所有 JSON 物件符合 sherlock/v1 schema
2. ts 欄位為 ISO8601 格式
3. session 欄位使用唯一 ID
4. target 欄位正確指定 bot
"""


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
    """發送 JSONL 到 n8n webhook"""
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
                    logger.error(f"n8n 回應錯誤: {response.status}")
                    return False
    except Exception as e:
        logger.error(f"發送到 n8n 失敗: {e}")
        return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理用戶訊息"""
    if not update.message or not update.message.text:
        return
    
    user_text = update.message.text
    session_id = f"sess_{datetime.now().strftime('%Y%m%d%H%M%S')}_{update.message.message_id}"
    
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
        
        # 發送到 n8n
        success = await send_to_n8n(jsonl_data, session_id)
        
        if success:
            # 解析目標 bot
            targets = []
            for line in jsonl_lines:
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "action":
                        target = obj.get("target", "unknown")
                        if target not in targets:
                            targets.append(target)
                except:
                    pass
            
            target_text = ", ".join(targets) if targets else "unknown"
            
            await processing_msg.edit_text(
                f"✅ 分析完成！\n\n"
                f"📤 已發送指令給: {target_text}\n"
                f"🆔 Session: {session_id}\n\n"
                f"```jsonl\n{jsonl_data[:300]}...\n```",
                parse_mode="Markdown",
            )
        else:
            await processing_msg.edit_text(
                f"⚠️ 分析完成但發送到 n8n 失敗\n\n"
                f"```jsonl\n{jsonl_data[:500]}\n```",
                parse_mode="Markdown",
            )
        
    except Exception as e:
        logger.error(f"處理訊息錯誤: {e}")
        await processing_msg.edit_text(f"❌ 處理錯誤: {str(e)}")


def main() -> None:
    """主程式"""
    logger.info("neovegasherlock_bot (Hermes Agent) 啟動")
    logger.info(f"模型: {OPENAI_MODEL}")
    logger.info(f"n8n Webhook: {N8N_WEBHOOK_URL}")
    
    # 建立 Application
    app = Application.builder().token(TOKEN).build()
    
    # 添加 handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 啟動 polling
    logger.info("開始 polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
