import logging

def setup_logging(config):
    logging.basicConfig(
        level=config.get("log_level", "INFO"),
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(config.get("log_file", "botnet.log")),
            logging.StreamHandler()
        ]
    )
    logging.info("Logging initialized")