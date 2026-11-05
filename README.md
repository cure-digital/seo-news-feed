# Cure SEO News Wire

Automated hourly aggregator that collects breaking SEO industry news, algorithm shift reports, and official Google Search announcements while filtering out evergreen how-to guides and tutorials.

## Live Outputs
- **RSS Feed:** `https://cure-digital.github.io/seo-news-feed/feed.xml`
- **Web Reader:** `https://cure-digital.github.io/seo-news-feed/`

## Monitored Sources
- Google Search Central Blog
- Search Engine Roundtable
- Search Engine Land (News Category)
- Search Engine Journal (News Category)

## Local Testing
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the aggregator:
   ```bash
   python aggregate.py
   ```
3. Open `public/index.html` or inspect `public/feed.xml`.

## Slack Integration
Agency colleagues can subscribe to the live feed in any public or private Slack channel using the native Slack RSS integration:
```text
/feed subscribe https://cure-digital.github.io/seo-news-feed/feed.xml
```
