from eastmed_shared import configure_error_monitoring, get_settings
from eastmed_shared.logging import configure_logging
from redis import Redis
from rq import Queue, Worker


def _run_worker(queue_names: list[str]) -> None:
    settings = get_settings()
    configure_error_monitoring(settings)
    configure_logging(settings.log_level)
    connection = Redis.from_url(settings.redis_url)
    worker = Worker(
        [Queue(name, connection=connection) for name in queue_names],
        connection=connection,
    )
    worker.work(with_scheduler=True)


def main() -> None:
    _run_worker(["ingestion-operations", "ingestion", "publication", "deliveries"])


def ingestion_main() -> None:
    _run_worker(["ingestion-operations", "ingestion"])


def publication_main() -> None:
    _run_worker(["publication"])


def delivery_main() -> None:
    _run_worker(["deliveries"])


def analysis_main() -> None:
    _run_worker(["analysis"])


def embedding_main() -> None:
    _run_worker(["embeddings"])


if __name__ == "__main__":
    main()
