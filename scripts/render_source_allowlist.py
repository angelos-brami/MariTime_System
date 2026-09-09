from __future__ import annotations

from urllib.parse import urlsplit

from eastmed_pipeline.source_seed import load_candidates


def main() -> None:
    hosts = sorted(
        {
            host
            for candidate in load_candidates()
            if (host := urlsplit(candidate["feed_url"]).hostname) is not None
        }
    )
    print(",".join(hosts))


if __name__ == "__main__":
    main()
