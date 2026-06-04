import asyncio
import logging
from core.proxy_pool import ProxyPool

async def main():
    logging.info("=== Supreme ViewBot Diagnostics Lab v2.1 ===")
    pool = ProxyPool("proxies.txt")
    logging.info(pool.stats())
    logging.info("Diagnostics complete.")
    logging.info("\nTo run real YouTube engagement:")
    logging.info('python main.py --url "https://youtube.com/watch?v=VIDEO_ID" --mode full')
    print("\n✅ Diagnostics finished. YouTube bot is ready.")

if __name__ == "__main__":
    asyncio.run(main())