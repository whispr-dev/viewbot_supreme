import asyncio
import logging
import random
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

class YouTubeInteractor:
    def __init__(self, driver, config):
        self.driver = driver
        self.config = config

    async def view_video(self, url: str):
        self.driver.get(url)
        logging.info(f"Opened YouTube video: {url}")

        # Explicit wait for page to load (YouTube player + title)
        try:
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1.title"))
            )
            logging.info("✅ YouTube page fully loaded")
        except:
            logging.warning("Page load wait timed out, continuing anyway")

        # Watch for a bit
        watch_time = random.randint(
            self.config.get("read_time_min", 20),
            self.config.get("read_time_max", 60)
        )
        await asyncio.sleep(watch_time)