import requests
import threading
import time
import random
import aiohttp
import asyncio
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


# Rotating proxy class with IPv4 and IPv6 proxies
class ProxyPool:
    def __init__(self):
        self.ipv6_proxies = [
            {"proxy": "http://ipv6_proxy1:port", "weight": 5, "failures": 0},
            {"proxy": "http://ipv6_proxy2:port", "weight": 2, "failures": 0},
            {"proxy": "http://ipv6_proxy3:port", "weight": 1, "failures": 0},
        ]
        self.ipv4_proxies = [
            {"proxy": "http://ipv4_proxy1:port", "weight": 5, "failures": 0},
            {"proxy": "http://ipv4_proxy2:port", "weight": 3, "failures": 0},
            {"proxy": "http://ipv4_proxy3:port", "weight": 1, "failures": 0},
        ]
        self.max_failures = 3

    def get_ipv6_proxy(self):
        total_weight = sum(proxy["weight"] for proxy in self.ipv6_proxies)
        random_choice = random.uniform(0, total_weight)
        cumulative_weight = 0
        for proxy in self.ipv6_proxies:
            cumulative_weight += proxy["weight"]
            if random_choice <= cumulative_weight:
                return proxy["proxy"]

    def get_ipv4_proxy(self):
        total_weight = sum(proxy["weight"] for proxy in self.ipv4_proxies)
        random_choice = random.uniform(0, total_weight)
        cumulative_weight = 0
        for proxy in self.ipv4_proxies:
            cumulative_weight += proxy["weight"]
            if random_choice <= cumulative_weight:
                return proxy["proxy"]

    def report_failure(self, proxy_url):
        for proxies in [self.ipv6_proxies, self.ipv4_proxies]:
            for proxy in proxies:
                if proxy["proxy"] == proxy_url:
                    proxy["failures"] += 1
                    if proxy["failures"] >= self.max_failures:
                        print(f"Removing {proxy_url} due to repeated failures")
                        proxies.remove(proxy)
                    break

    def report_success(self, proxy_url):
        for proxies in [self.ipv6_proxies, self.ipv4_proxies]:
            for proxy in proxies:
                if proxy["proxy"] == proxy_url:
                    proxy["failures"] = 0
                    break

# Asynchronous fetch with proxy support
async def fetch(session, url, proxy_pool, ipv6=True):
    proxy = proxy_pool.get_ipv6_proxy() if ipv6 else proxy_pool.get_ipv4_proxy()
    headers = {"User-Agent": random.choice(user_agents)}
    try:
        async with session.get(url, proxy=proxy, headers=headers) as response:
            print(f"Request sent via {proxy}, status code: {response.status}")
            proxy_pool.report_success(proxy)
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}. Retrying with a different proxy...")
        proxy_pool.report_failure(proxy)

# Main async function to run the requests
async def main():
    proxy_pool = ProxyPool()
    async with aiohttp.ClientSession() as session:
        tasks = []
        for _ in range(NUM_THREADS):
            tasks.append(fetch(session, URL, proxy_pool))
        await asyncio.gather(*tasks)

# User-Agent randomization setup
user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/89.0",
]

# Configuration
URL = "http://example.com"  # Replace with the target URL
NUM_THREADS = 10  # Number of concurrent threads

# Start the bot
if __name__ == "__main__":
    asyncio.run(main())

# CAPTCHA solving and browser automation (Selenium setup)
options = Options()
options.add_argument("--headless")
options.add_argument("--disable-gpu")

driver = webdriver.Chrome(options=options)
driver.get("http://example.com/page1")
print(driver.page_source)
driver.quit()

# 2Captcha Integration
API_KEY = 'your_2captcha_api'

def get_recaptcha_response_with_retries_rate_limiting_and_logging(site_key, url, max_retries=5, rate_limit=5):
    last_request_time = 0
    retries = 0

    while retries < max_retries:
        if time.time() - last_request_time < rate_limit:
            wait_time = rate_limit - (time.time() - last_request_time)
            print(f"Rate limit hit. Waiting for {wait_time} seconds...")
            time.sleep(wait_time)

        payload = {
            'key': API_KEY,
            'method': 'userrecaptcha',
            'googlekey': site_key,
            'pageurl': url,
            'json': 1
        }

        try:
            response = requests.post('http://2captcha.com/in.php', data=payload)
            captcha_id = response.json().get('request')

            result_payload = {
                'key': API_KEY,
                'action': 'get',
                'id': captcha_id,
                'json': 1
            }

            while True:
                result = requests.get('http://2captcha.com/res.php', params=result_payload)
                if result.json().get('status') == 1:
                    captcha_text = result.json().get('request')
                    logging.info(f"CAPTCHA solved: {captcha_text}")
                    return captcha_text
                time.sleep(5)
        except Exception as e:
            print(f"Error solving CAPTCHA: {e}")
            retries += 1
            wait_time = 2 ** retries + random.uniform(0, 1)
            print(f"Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    print("Failed to solve CAPTCHA after multiple retries.")
    return None

# Validate CAPTCHA responses
def submit_captcha_solution(captcha_text):
    response = submit_form(captcha_text)
    if "CAPTCHA incorrect" in response.text:
        print("CAPTCHA solution was incorrect.")
        return False
    elif "CAPTCHA solved" in response.text:
        print("CAPTCHA solved successfully!")
        return True
    else:
        print("Unexpected response from server.")
        return None
