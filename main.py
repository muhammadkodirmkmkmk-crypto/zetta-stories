import asyncio
import logging
import schedule
import time
import threading

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
        await _app.updater.start_polling(drop_pending_updates=True)

        loop = asyncio.get_event_loop()

        scheduler_thread = threading.Thread(
            target=_scheduler_thread, args=(loop,), daemon=True
        )
        scheduler_thread.start()

        logger.info("Bot started — running immediate story generation for testing...")
        await run_daily_generation(_app)

        logger.info("Bot is polling for callbacks 24/7. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received")
        finally:
            await _app.updater.stop()
            await _app.stop()


if __name__ == "__main__":
    asyncio.run(main())
