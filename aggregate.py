import os
import re
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from dateutil import parser as date_parser
import feedparser
from feedgen.feed import FeedGenerator
import requests

# High-signal SEO news sources
SOURCES = [
    {
        "name": "Google Search Central",
        "url": "https://developers.google.com/search/blog/rss.xml",
        "badge_color": "#2563eb",
        "default_img": "https://images.unsplash.com/photo-1573804633927-bfcbcd909acd?w=600&auto=format&fit=crop&q=80"
    },
    {
        "name": "Search Engine Roundtable",
        "url": "https://www.seroundtable.com/index.xml",
        "badge_color": "#d97706",
        "default_img": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=600&auto=format&fit=crop&q=80"
    },
    {
        "name": "Search Engine Land",
        "url": "https://searchengineland.com/feed",
        "badge_color": "#dc2626",
        "default_img": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=600&auto=format&fit=crop&q=80"
    },
    {
        "name": "Search Engine Journal",
        "url": "https://www.searchenginejournal.com/category/news/feed/",
        "badge_color": "#0284c7",
        "default_img": "https://images.unsplash.com/photo-1504868584819-f8e8b4b6d7e3?w=600&auto=format&fit=crop&q=80"
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
IMG_REGEX = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

def is_breaking_news(title: str) -> bool:
    """Filter out non-news articles based on title patterns."""
    if GUIDE_REGEX.search(title):
        return False
    return True

def extract_rss_image(entry) -> str | None:
    """Extract lead article image from media tags or enclosures if present in RSS."""
    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            url = media.get("url")
            if url and not url.endswith(".svg") and "pixel" not in url.lower():
                return url

    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        for media in entry.media_thumbnail:
            url = media.get("url")
            if url and not url.endswith(".svg") and "pixel" not in url.lower():
                return url

    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            href = enc.get("href")
            enc_type = enc.get("type", "")
            if href and ("image" in enc_type or re.search(r"\.(jpg|jpeg|png|webp)", href, re.IGNORECASE)):
                return href

    html_corpus = ""
    if hasattr(entry, "content") and entry.content:
        for c in entry.content:
            html_corpus += c.get("value", "") + " "
    if hasattr(entry, "summary"):
        html_corpus += entry.summary + " "
    if hasattr(entry, "description"):
        html_corpus += entry.description + " "

    match = IMG_REGEX.search(html_corpus)
    if match:
        img_url = match.group(1)
        if not any(bad in img_url.lower() for bad in ["pixel", "tracker", "avatar", "icon", "feed-icon"]):
            return img_url

    return None

def fetch_og_image(url: str, headers: dict, timeout: int = 5) -> str | None:
    """Fetch the individual post page and extract the open graph or twitter header image."""
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
        if resp.status_code != 200:
            return None

        chunks = []
        total_len = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=True):
            if chunk:
                chunks.append(chunk)
                total_len += len(chunk)
                if "</head>" in chunk.lower() or total_len > 65536:
                    break
        html_head = "".join(chunks)

        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html_head, re.IGNORECASE)
            if m:
                img_url = html.unescape(m.group(1)).strip()
                if img_url.startswith("http") and not any(bad in img_url.lower() for bad in ["pixel", "tracker", "avatar", "feed-icon"]):
                    return img_url
    except Exception:
        pass
    return None

def clean_snippet(html_text: str, max_chars: int = 175) -> str:
    """Strip HTML tags and trim description to a clean summary snippet."""
    clean = re.sub(r"<[^>]+>", " ", html_text)
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"The post .* appeared first on .*", "", clean).strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars].rstrip() + "..."
    return clean

def parse_date(entry) -> datetime:
    """Extract and parse publication date into a timezone-aware UTC datetime."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "qpdated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "published"):
        try:
            dt = date_parser.parse(entry.published)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)

def format_relative_time(dt: datetime) -> str:
    """Render friendly relative time (e.g. 2h ago, yesterday)."""
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    else:
        days = seconds // 86400
        return f"{days}d ago"

def main():
    os.makedirs("public", exist_ok=True)

    fg = FeedGenerator()
    fg.id("https://cure-digital.github.io/seo-news-feed/")
    fg.title("Cure SEO News Wire")
    fg.link(href="https://cure-digital.github.io/seo-news-feed/feed.xml", rel="self")
    fg.link(href="https://cure-digital.github.io/seo-news-feed/", rel="alternate")
    fg.description("Hourly curated breaking industry news, Google announcements, and search algorithm reporting by Cure Digital.")
    fg.language("en")

    seen_titles = set()
    collected_entries = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AgencySEOBot/1.0"
    }

    for source in SOURCES:
        try:
            print(f"Fetching: {source['name']} ({source['url']})")
            feed = feedparser.parse(source["url"], request_headers=headers)
            for item in feed.entries:
                title = getattr(item, "title", "").strip()
                link = getattr(item, "link", "").strip()
                if not title or not link:
                    continue

                # Strip "via" and everything following it from article titles
                clean_title = re.sub(r"\s*[\(\[\,\-\:\|\·\•]*\s*\bvia\b.*$", "", title, flags=re.IGNORECASE).strip()
                clean_title = re.sub(r"[\s\,\-\:\|\(\[]+$", "", clean_title).strip()
                if clean_title:
                    title = clean_title

                if not is_breaking_news(title):
                    continue

                norm_title = "".join(filter(str.isalnum, title.lower()))
                if norm_title in seen_titles:
                    continue
                seen_titles.add(norm_title)

                pub_date = parse_date(item)
                if pub_date < cutoff:
                    continue

                raw_summary = getattr(item, "summary", "") or getattr(item, "description", "")
                snippet = clean_snippet(raw_summary)
                rss_img = extract_rss_image(item)

                collected_entries.append({
                    "title": title,
                    "display_title": f"[{source['name']}] {title}",
                    "link": link,
                    "snippet": snippet,
                    "image": rss_img,
                    "default_img": source["default_img"],
                    "pub_date": pub_date,
                    "relative_time": format_relative_time(pub_date),
                    "source": source["name"],
                    "badge_color": source["badge_color"],
                })
        except Exception as e:
            print(f"Error fetching {source['name']}: {e}")

    # Resolve post header images concurrently
    print(f"Resolving post header images for {len(collected_entries)} stories...")
    def resolve_entry_image(entry):
        if not entry["image"]:
            og_img = fetch_og_image(entry["link"], headers)
            if og_img:
                entry["image"] = og_img
            else:
                entry["image"] = entry["default_img"]
        return entry

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(resolve_entry_image, e) for e in collected_entries]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print(f"Error resolving post image: {e}")

    # Sort strictly newest first
    collected_entries.sort(key=lambda x: x["pub_date"], reverse=True)

    # Populate RSS Feed with rich HTML and image enclosures
    for entry in collected_entries:
        fe = fg.add_entry()
        fe.id(entry["link"])
        fe.title(entry["display_title"])
        fe.link(href=entry["link"])
        
        rich_description = (
            f'<p><img src="{entry["image"]}" alt="{entry["title"]}" style="max-width:100%; border-radius:8px;" /></p>'
            f'<p><strong>{entry["source"]}</strong>: {entry["snippet"]}</p>'
            f'<p><a href="{entry["link"]}">Read full article at {entry["source"]}</a></p>'
        )
        fe.description(rich_description)
        fe.pubDate(entry["pub_date"])
        fe.enclosure(entry["image"], 0, "image/jpeg")

    output_path = os.path.join("public", "feed.xml")
    fg.rss_file(output_path, pretty=True)
    print(f"Generated {output_path} with {len(collected_entries)} items.")

    cards_html = ""
    for idx, item in enumerate(collected_entries):
        cards_html += f"""
        <article class="news-card" data-source="{item['source'].lower()}">
            <div class="card-media">
                <img src="{item['image']}" alt="{item['title']}" loading="lazy" onerror="this.onerror=null; this.src='{item['default_img']}';" />
                <span class="badge" style="background-color: {item['badge_color']};">{item['source']}</span>
            </div>
            <div class="card-content">
                <div class="card-meta">
                    <span class="card-time">{item['relative_time']}</span>
                </div>
                <h2 class="card-title">
                    <a href="{item['link']}" target="_blank" rel="noopener noreferrer">{item['title']}</a>
                </h2>
                <p class="card-snippet">{item['snippet']}</p>
                <div class="card-footer">
                    <a href="{item['link']}" class="read-link" target="_blank" rel="noopener noreferrer">
                        Read Story <span>&rarr;</span>
                    </a>
                </div>
            </div>
        </article>
        """

    total_count = str(len(collected_entries))

    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cure SEO News Wire</title>
    <meta name="description" content="Hourly curated breaking SEO industry news, algorithm shift reports, and Google announcements from Cure Digital.">
    <link rel="icon" type="image/svg+xml" href="favicon.svg">
    <link rel="apple-touch-icon" href="favicon.svg">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-body: #0b0f19;
            --bg-card: #111827;
            --border-color: #1f2937;
            --text-primary: #f9fafb;
            --text-secondary: #94a3b8;
            --accent: #f97316;
            --card-radius: 14px;
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-body);
            color: var(--text-primary);
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
        }
        .container {
            max-width: 1240px;
            margin: 0 auto;
            padding: 0 1.5rem;
        }
        header {
            border-bottom: 1px solid var(--border-color);
            background: rgba(11, 15, 25, 0.85);
            backdrop-filter: blur(12px);
            position: sticky;
            top: 0;
            z-index: 50;
            padding: 1.1rem 0;
        }
        .header-inner {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            flex-wrap: wrap;
        }
        .brand-cluster {
            display: flex;
            align-items: center;
            gap: 0.85rem;
        }
        .pulse-indicator {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: #10b981;
            box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }
        .brand-title {
            font-size: 1.25rem;
            font-weight: 800;
            letter-spacing: -0.025em;
            background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .action-cluster {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        .last-updated-text {
            font-size: 0.775rem;
            color: var(--text-secondary);
            font-weight: 500;
        }
        .btn-refresh {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            font-weight: 600;
            font-size: 0.875rem;
            padding: 0.52rem 0.95rem;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .btn-refresh:hover {
            background: #1e293b;
            border-color: #374151;
            color: #ffffff;
        }
        .refresh-icon.spinning {
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        .btn-copy {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
            color: #ffffff;
            font-weight: 600;
            font-size: 0.875rem;
            padding: 0.55rem 1.1rem;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            transition: transform 0.15s ease, filter 0.15s ease;
            box-shadow: 0 4px 14px rgba(249, 115, 22, 0.3);
            text-decoration: none;
        }
        .btn-copy:hover {
            filter: brightness(1.1);
            transform: translateY(-1px);
        }
        .hero {
            padding: 2.75rem 0 2rem;
        }
        .hero-desc {
            color: var(--text-secondary);
            font-size: 1.05rem;
            max-width: 650px;
            margin-bottom: 1.75rem;
        }
        .controls-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            flex-wrap: wrap;
            padding-bottom: 1.25rem;
            border-bottom: 1px solid var(--border-color);
        }
        .filter-tags {
            display: flex;
            gap: 0.5rem;
            font-wrap: wrap;
        }
        .filter-btn {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 0.4rem 0.85rem;
            border-radius: 20px;
            font-size: 0.825rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .filter-btn:hover {
            color: var(--text-primary);
            border-color: #374151;
        }
        .filter-btn.active {
            background: #ffffff;
            color: #0b0f19;
            border-color: #ffffff;
        }
        .search-input {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.45rem 0.9rem;
            color: #ffffff;
            font-size: 0.875rem;
            outline: none;
            width: 250px;
            transition: border-color 0.15s ease;
        }
        .search-input:focus {
            border-color: #f97316;
        }
        .news-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 1.75rem;
            padding: 2rem 0 4rem;
        }
        .news-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--card-radius);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .news-card:hover {
            transform: translateY(-4px);
            border-color: #334155;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
        }
        .card-media {
            position: relative;
            width: 100%;
            height: 195px;
            background: #1e293b;
            overflow: hidden;
        }
        .card-media img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.4s ease;
        }
        .news-card:hover .card-media img {
            transform: scale(1.04);
        }
        .badge {
            position: absolute;
            top: 12px;
            left: 12px;
            color: #ffffff;
            font-size: 0.725rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            padding: 0.3rem 0.65rem;
            border-radius: 6px;
            backdrop-filter: blur(8px);
        }
        .card-content {
            padding: 1.35rem;
            display: flex;
            flex-direction: column;
            flex-grow: 1;
        }
        .card-meta {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 0.65rem;
        }
        .card-time {
            font-size: 0.8rem;
            color: #64748b;
            font-weight: 600;
        }
        .card-title {
            font-size: 1.1rem;
            font-weight: 700;
            line-height: 1.38;
            margin-bottom: 0.65rem;
        }
        .card-title a {
            color: var(--text-primary);
            text-decoration: none;
            transition: color 0.15s ease;
        }
        .card-title a:hover {
            color: #f97316;
        }
        .card-snippet {
            color: var(--text-secondary);
            font-size: 0.875rem;
            line-height: 1.55;
            margin-bottom: 1.25rem;
            flex-grow: 1;
        }
        .card-footer {
            padding-top: 0.85rem;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .read-link {
            color: #38bdf8;
            font-size: 0.85rem;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            transition: gap 0.15s ease;
        }
        .read-link:hover {
            gap: 0.6rem;
        }
        @media (max-width: 768px) {
            .news-grid {
                grid-template-columns: 1fr;
            }
            .search-input {
                width: 100%;
            }
            .last-updated-text {
                display: none;
            }
            .action-cluster {
                width: 100%;
                justify-content: flex-end;
                gap: 0.5rem;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container header-inner">
            <div class="brand-cluster">
                <svg class="brand-logo" width="30" height="30" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <defs>
                        <linearGradient id="headerGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                            <stop offset="0%" stop-color="#f97316"/>
                            <stop offset="100%" stop-color="#ea580c"/>
                        </linearGradient>
                    </defs>
                    <rect width="32" height="32" rx="7" fill="#111827" stroke="#1f2937" stroke-width="1.5"/>
                    <path d="M21 11C19.5 9.5 17.5 8.5 15 8.5C10.5 8.5 7 12 7 16.5C7 21 10.5 24.5 15 24.5C17.5 24.5 19.5 23.5 21 22" fill="none" stroke="url(#headerGrad)" stroke-width="3.5" stroke-linecap="round"/>
                    <circle cx="21.5" cy="10.5" r="2.2" fill="#10b981"/>
                </svg>
                <div class="brand-title">Cure SEO News Wire</div>
                <div class="pulse-indicator" title="Live hourly feed"></div>
            </div>
            <div class="action-cluster">
                <span class="last-updated-text" id="liveStatusText" title="Auto-refreshes every 10 minutes">Auto-refreshes every 10m</span>
                <button class="btn-refresh" id="refreshBtn" onclick="manualRefresh()" title="Check for new stories">
                    <svg class="refresh-icon" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    <span id="refreshBtnLabel">Refresh</span>
                </button>
                <a href="feed.xml" class="btn-copy" id="rssBtn" onclick="copyFeedUrl(event)">
                    <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24"><path d="M6.18 15.64a2.18 2.18 0 0 1 2.18 2.18C8.36 19 7.38 20 6.18 20C5 20 4 19 4 17.82a2.18 2.18 0 0 1 2.18-2.18M4 4.44A15.56 15.56 0 0 1 19.56 20h-2.83A12.73 12.73 0 0 0 4 7.27V4.44m0 5.66a9.9 9.9 0 0 1 9.9 9.9h-2.83A7.07 7.07 0 0 0 4 12.93v-2.83Z"/></svg>
                    <span>Copy RSS URL</span>
                </a>
            </div>
        </div>
    </header>

    <main class="container">
        <section class="hero">
            <p class="hero-desc">
                Live automated wire aggregating breaking industry updates, search algorithm changes, and Google communications. Filtered hourly for verified reporting.
            </p>
            <div class="controls-bar">
                <div class="filter-tags">
                    <button class="filter-btn active" onclick="filterBySource(this, 'all')">All ({{TOTAL_COUNT}})</button>
                    <button class="filter-btn" onclick="filterBySource(this, 'google')">Google</button>
                    <button class="filter-btn" onclick="filterBySource(this, 'search engine roundtable')">Roundtable</button>
                    <button class="filter-btn" onclick="filterBySource(this, 'search engine land')">SEL</button>
                    <button class="filter-btn" onclick="filterBySource(this, 'search engine journal')">SEJ</button>
                </div>
                <input type="text" id="searchInput" class="search-input" placeholder="Search breaking stories..." oninput="handleSearch()" />
            </div>
        </section>

        <section class="news-grid" id="newsGrid">
            {{CARDS_HTML}}
        </section>
    </main>

    <script>
        let activeSource = 'all';
        let lastRefreshTime = Date.now();
        const REFRESH_INTERVAL_MS = 10 * 60 * 1000;

        function applyFilter(source) {
            activeSource = source;
            const cards = document.querySelectorAll('.news-card');
            cards.forEach(card => {
                if (source === 'all') {
                    card.style.display = 'flex';
                } else {
                    const cardSource = card.getAttribute('data-source') || '';
                    card.style.display = cardSource.includes(source) ? 'flex' : 'none';
                }
            });
        }

        function filterBySource(btn, source) {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
            applyFilter(source);
            handleSearch();
        }

        function handleSearch() {
            const query = document.getElementById('searchInput').value.toLowerCase();
            const cards = document.querySelectorAll('.news-card');
            cards.forEach(card => {
                const cardSource = card.getAttribute('data-source') || '';
                const matchesSource = (activeSource === 'all') || cardSource.includes(activeSource);
                const text = card.innerText.toLowerCase();
                const matchesQuery = text.includes(query);
                card.style.display = (matchesSource && matchesQuery) ? 'flex' : 'none';
            });
        }

        function copyFeedUrl(e) {
            e.preventDefault();
            const feedUrl = window.location.origin + window.location.pathname.replace(/index\.html$/, '') + 'feed.xml';
            navigator.clipboard.writeText(feedUrl).then(() => {
                const btn = document.getElementById('rssBtn');
                const orig = btn.innerHTML;
                btn.innerHTML = '<span>Copied URL!</span>';
                setTimeout(() => { btn.innerHTML = orig; }, 2000);
            });
        }

        async function fetchLatestNews(isManual = false) {
            const btn = document.getElementById('refreshBtn');
            const icon = btn ? btn.querySelector('.refresh-icon') : null;
            const label = document.getElementById('refreshBtnLabel');
            const statusText = document.getElementById('liveStatusText');

            if (icon) icon.classList.add('spinning');
            if (isManual && label) label.innerText = 'Refreshing...';

            try {
                const response = await fetch(window.location.pathname + '?_t=' + Date.now(), { cache: 'no-store' });
                if (!response.ok) throw new Error('Network error');
                const htmlText = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(htmlText, 'text/html');

                const newGrid = doc.getElementById('newsGrid');
                const currentGrid = document.getElementById('newsGrid');
                if (newGrid && currentGrid) {
                    currentGrid.innerHTML = newGrid.innerHTML;
                }

                const allBtn = document.querySelector('.filter-btn');
                const newAllBtn = doc.querySelector('.filter-btn');
                if (allBtn && newAllBtn) {
                    allBtn.innerText = newAllBtn.innerText;
                }

                applyFilter(activeSource);
                handleSearch();

                lastRefreshTime = Date.now();
                if (statusText) statusText.innerText = 'Updated just now';
                if (label) label.innerText = isManual ? 'Updated!' : 'Refresh';
            } catch (err) {
                console.warn('Auto-refresh check failed:', err);
                if (label && isManual) label.innerText = 'Failed';
            } finally {
                if (icon) icon.classList.remove('spinning');
                setTimeout(() => {
                    if (label) label.innerText = 'Refresh';
                    if (statusText) statusText.innerText = 'Auto-refreshes every 10m';
                }, 2500);
            }
        }

        function manualRefresh() {
            fetchLatestNews(true);
        }

        setInterval(() => {
            fetchLatestNews(false);
        }, REFRESH_INTERVAL_MS);

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                if (Date.now() - lastRefreshTime > REFRESH_INTERVAL_MS) {
                    fetchLatestNews(false);
                }
            }
        });
    </script>
</body>
</html>
"""
    final_html = html_template.replace("{{TOTAL_COUNT}}", total_count).replace("{{CARDS_HTML}}", cards_html)
    with open(os.path.join("public", "index.html"), "w", encoding="utf-8") as f:
        f.write(final_html)
    print("Generated public/index.html.")

    favicon_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f97316"/>
      <stop offset="100%" stop-color="#ea580c"/>
    </linearGradient>
  </defs>
  <rect width="32" height="32" rx="7" fill="#0b0f19" stroke="#1e293b" stroke-width="1.5"/>
  <path d="M21 11C19.5 9.5 17.5 8.5 15 8.5C10.5 8.5 7 12 7 16.5C7 21 10.5 24.5 15 24.5C17.5 24.5 19.5 23.5 21 22" fill="none" stroke="url(#g)" stroke-width="3.5" stroke-linecap="round"/>
  <circle cx="21.5" cy="10.5" r="2.2" fill="#10b981"/>
</svg>"""
    with open(os.path.join("public", "favicon.svg"), "w", encoding="utf-8") as f:
        f.write(favicon_svg)
    print("Generated public/favicon.svg.")

if __name__ == "__main__":
    main()
