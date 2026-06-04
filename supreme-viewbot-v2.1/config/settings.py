def get_config(args):
    return {
        "target_url": args.url,
        "mode": args.mode,
        "max_concurrent_threads": getattr(args, "threads", 3),
        "headless": getattr(args, "headless", False),
        "proxy_file": getattr(args, "proxy_file", "proxies.txt"),
        "log_level": "INFO",
        "log_file": "botnet.log",
        "read_time_min": 20,
        "read_time_max": 60,
        "like_probability": 0.7,
        "comment_probability": 0.3,
        "sub_probability": 0.4,
    }