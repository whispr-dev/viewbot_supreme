import random
import logging
from typing import List, Optional

class ProxyState:
    def __init__(self, proxy: str):
        self.proxy = proxy
        self.failures = 0
        self.successes = 0

class ProxyPool:
    def __init__(self, proxy_file: str = "proxies.txt"):
        self.proxies: List[ProxyState] = []
        self.load_proxies(proxy_file)

    def load_proxies(self, proxy_file: str):
        try:
            with open(proxy_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            self.proxies = [ProxyState(line) for line in lines]
            logging.info(f"Loaded {len(self.proxies)} proxies from {proxy_file}")
        except Exception as e:
            logging.error(f"Failed to load proxies: {e}")
            self.proxies = []

    def get_random_proxy(self) -> Optional[str]:
        if not self.proxies:
            return None
        # Simple weighted selection (more successful proxies preferred)
        weights = [1.0 + p.successes - p.failures * 0.5 for p in self.proxies]
        return random.choices(self.proxies, weights=weights, k=1)[0].proxy

    def report_success(self, proxy: str):
        for p in self.proxies:
            if p.proxy == proxy:
                p.successes += 1
                break

    def report_failure(self, proxy: str):
        for p in self.proxies:
            if p.proxy == proxy:
                p.failures += 1
                break

    def stats(self):
        total = len(self.proxies)
        working = sum(1 for p in self.proxies if p.successes > p.failures)
        return f"Proxies: {working}/{total} working"