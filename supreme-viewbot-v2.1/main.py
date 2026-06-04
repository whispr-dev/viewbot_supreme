import asyncio
import argparse
import logging
from config.settings import get_config
from utils.logging import setup_logging

async def main():
    parser = argparse.ArgumentParser(description="Supreme ViewBot v2.1 - YouTube Edition")
    parser.add_argument("--url", required=True, help="YouTube video URL")
    parser.add_argument("--mode", choices=["full", "diagnostics"], default="full", help="full = engagement, diagnostics = lab")
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--proxy-file", default="proxies.txt")
    args = parser.parse_args()

    config = get_config(args)
    setup_logging(config)

    if args.mode == "diagnostics":
        logging.info("Starting Diagnostics Lab")
        from viewbot_lab.cli import main as diagnostics_main
        await diagnostics_main()
    else:
        logging.info(f"Starting Full YouTube Engagement on {args.url}")
        from platforms.youtube.viewbot import run_youtube_bot
        await run_youtube_bot(config)

if __name__ == "__main__":
    asyncio.run(main())