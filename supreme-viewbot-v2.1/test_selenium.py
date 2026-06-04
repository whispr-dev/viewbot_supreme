from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import logging
import time

logging.basicConfig(level=logging.INFO)

options = webdriver.ChromeOptions()
options.add_argument("--no-sandbox")
options.add_argument("--disable-gpu")
options.add_argument("--disable-software-rasterizer")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])

try:
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    logging.info("✅ Chrome opened successfully!")
    driver.get("https://www.youtube.com")
    print("Chrome is open. Wait 15 seconds...")
    time.sleep(15)
    driver.quit()
    print("Test finished.")
except Exception as e:
    logging.error(f"Failed: {e}")