import logging
import tempfile
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

class BrowserManager:
    def __init__(self, headless=False):
        self.headless = headless

    def get_driver(self):
        options = webdriver.ChromeOptions()

        # Minimal flags
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Use a completely fresh temporary profile (this fixes many "exited" crashes)
        temp_profile = tempfile.mkdtemp()
        options.add_argument(f"--user-data-dir={temp_profile}")

        if self.headless:
            options.add_argument("--headless=new")

        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            logging.info("✅ Chrome opened successfully with clean profile")
            return driver
        except Exception as e:
            logging.error(f"Failed to start Chrome: {e}")
            raise