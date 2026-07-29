"""Dev utility: dump the element snapshot and page text for a camera URL.

Usage: uv run python probe.py <url> [--headed]
"""

import sys

from core.browser import Browser


def main():
    url = sys.argv[1]
    headed = "--headed" in sys.argv
    b = Browser(headed=headed)
    b.start()
    try:
        from urllib.parse import urlparse

        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        b.goto(origin)
        b.goto(url)
        b.page.wait_for_timeout(2000)
        print("========== SNAPSHOT ==========")
        print(b.snapshot())
        print("\n========== PAGE TEXT ==========")
        print(b.page_text())
    finally:
        b.close()


if __name__ == "__main__":
    main()
