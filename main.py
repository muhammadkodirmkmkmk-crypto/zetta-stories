import asyncio
import logging
import schedule
import time
import threading

from telegram import Update
from telegram.error import Conflict, NetworkError, TimedOut

from bot import run_daily_generation, build_application

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_app = None


def _scheduler_thread(loop: asyncio.AbstractEventLoop):
    def job():
        logger.info("Scheduled trigger: running daily generation")
        asyncio.run_coroutine_threadsafe(run_daily_generation(_app), loop)

    # Daily at 04:00 UTC = 09:00 Tashkent (UTC+5)
    schedule.every().day.at("04:00").do(job)
    logger.info("Scheduler started — daily generation at 04:00 UTC (09:00 Tashkent)")

    while True:
        schedule.run_pending()
        time.sleep(30)


async def main():
    global _app

    _app = build_application()

    async with _app:
        await _app.start()

        # Explicitly delete any existing webhook and drop stale updates
        # This terminates any other polling session before we start ours
        logger.info("Deleting existing webhook and clearing pending updates...")
        await _app.bot.delete_webhook(drop_pending_updates=True)

        # Brief pause to let any other running instance fully release the connection
        await asyncio.sleep(3)

        logger.info("Starting polling...")
        await _app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
        )

        loop = asyncio.get_event_loop()
        scheduler_thread = threading.Thread(
            target=_scheduler_thread, args=(loop,), daemon=True
        )
        scheduler_thread.start()

        logger.info("Bot is polling for callbacks 24/7. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received")
        finally:
            logger.info("Stopping polling...")
            await _app.updater.stop()
            await _app.stop()


if __name__ == "__main__":
    asyncio.run(main())
