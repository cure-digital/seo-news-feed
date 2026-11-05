import os
import re
from datetime import datetime, timezone, timedelta
from dateutil import parser as date_parser
import feedparser
from feedgen.feed import FeedGenerator

# High-signal SEO news sources
SOURCES = [
    {
        "name": "Google Search Central",
        "url": "https://developers.google.com/search/blog/rss.xml",
    },
    {
        "name": "Search Engine Roundtable",
        "url": "https://www.seroundtable.com/index.xml",
    },
    {
        "name": "Search Engine Land (News)",
        "url": "https://searchengineland.com/category/news/feed",
    },
    {
        "name": "Search Engine Journal (News)",
        "url": "https://www.searchenginejournal.com/category/news/feed/",
    },
]

# Patterns that indicate evergreen advice/tutorials rather than timely industry news
GUIDE_PATTERNS = [
    r"\bhow to\b",
    r"\bguide\b",
    r"\btips\b",
    r"\bbest practices\b",
    r"\bchecklist\b",
    r"\bstep-by-step\b",
    r"\bways to\b",
    r"\bstrategies for\b",
    r"\btutorial\b",
    r"\bwhy you need\b",
    r"\bwhat is\b",
    r"\bhow you can\b",
    r"\ba beginner's guide\b",
]

GUIDE_REGEX = re.compile("|".join(GUIDE_PATTERNS), re.IGNORECASE)

def is_breaking_news(title: str) -> bool:
    """Filter out non-news articles based on title patterns."""
    if GUIDE_REGEX.search(title):
        return False
    return True

def parse_date(entry) -> datetime:
    """Extract and parse publication date into a timezone-aware UTC datetime."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "published"):
        try:
            dt = date_parser.parse(entry.published)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)

def main():
    os.makedirs("public", exist_ok=True)

    fg = FeedGenerator()
    fg.id("https://seo-news-feed.internal")
    fg.title("Agency SEO News Wire")
    fg.link(href="feed.xml", rel="self")
    fg.description("Hourly curated breaking news and algorithm updates for the agency.")
    fg.language("en")

    seen_titles = set()
    collected_entries = []
    
    # Only collect stories from the last 5 days to keep the feed fresh
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)

    for source in SOURCES:
        try:
            print(f"Fetching: {source['name']} ({source['url']})")
            feed = feedparser.parse(source["url"])
            for item in feed.entries:
                title = getattr(item, "title", "").strip()
                link = getattr(item, "link", "").strip()
                if not title or not link:
                    continue

                if not is_breaking_news(title):
                    continue

                # Normalized title for cross-source deduplication
                normalized_title = "".join(filter(str.isalnum, title.lower()))
                if normalized_title in seen_titles:
                    continue
                seen_titles.add(normalized_title)

                pub_date = parse_date(item)
                if pub_date < cutoff:
                    continue

                summary = getattr(item, "summary", "") or getattr(item, "description", "")

                collected_entries.append({
                    "title": f"[{source['name']}] {title}",
                    "link": link,
                    "description": summary,
                    "pub_date": pub_date,
                    "source": source["name"]
                })
        except Exception as e:
            print(f"Error fetching {source['name']}: {e}")

    # Sort strictly newest to oldest
    collected_entries.sort(key=lambda x: x["pub_date"], reverse=True)

    # Populate RSS Feed
    for entry in collected_entries:
        fe = fg.add_entry()
        fe.id(entry["link"])
        fe.title(entry["title"])
        fe.link(href=entry["link"])
        fe.description(entry["description"])
        fe.pubDate(entry["pub_date"])

    output_path = os.path.join("public", "feed.xml")
    fg.rss_file(output_path, pretty=True)
    print(f"Generated {output_path} with {len(collected_entries)} news items.")

    # Generate a lightweight HTML companion page for colleagues browsing via browser
    html_items = ""
    for item in collected_entries[:30]:
        formatted_date = item["pub_date"].strftime("%b %d, %Y %H:%M UTC")
        html_items += f"""
        <li style="margin-bottom: 1.25rem;">
            <div style="font-size: 0.85rem; color: #64748b;">{item['source']} &bull; {formatted_date}</div>
            <a href="{item['link']}" target="_blank" rel="noopener" style="font-size: 1.1rem; font-weight: 600; color: #1d4ed8; text-decoration: none;">
                {item['title']}
            </a>
        </li>
        """

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agency SEO News Wire</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            max-width: 760px;
            margin: 40px auto;
            padding: 0 20px;
            color: #1e293b;
            line-height: 1.5;
        }}
        .header {{
            border-bottom: 2px solid #e2e8f0;
            padding-bottom: 1rem;
            margin-bottom: 2rem;
        }}
        .rss-btn {{
            display: inline-block;
            background: #f97316;
            color: white;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 0.875rem;
            margin-top: 0.5rem;
        }}
        ul {{
            list-style: none;
            padding: 0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Agency SEO News Wire</h1>
        <p>Live hourly SEO industry updates, official Google announcements, and algorithm reporting.</p>
        <a class="rss-btn" href="feed.xml" target="_blank">Subscribe via RSS URL</a>
    </div>
    <ul>
        {html_items}
    </ul>
</body>
</html>
"""
    with open(os.path.join("public", "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    print("Generated public/index.html.")

if __name__ == "__main__":
    main()
