import asyncio
import logging
import random
from core.browser_manager import BrowserManager
from platforms.youtube.interactor import YouTubeInteractor

class YouTubeViewBot:
    def __init__(self, config):
        self.config = config
        self.browser_manager = BrowserManager(headless=False)

    async def run(self):
        logging.info(f"Starting YouTube engagement on {self.config['target_url']}")
        
        driver = self.browser_manager.get_driver()
        interactor = YouTubeInteractor(driver, self.config)

        try:
            await interactor.view_video(self.config["target_url"])
            logging.info("View completed")
            # TODO: like, subscribe, comment can be added here later
        finally:
            # Keep browser open so you can see it
            logging.info("Keeping browser open for 15 seconds so you can see it...")
            await asyncio.sleep(15)
            # driver.quit()   # commented out for debugging

async def run_youtube_bot(config):
    bot = YouTubeViewBot(config)
    await bot.run()