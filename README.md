# Manga Downloader

A browser-based manga downloader for WordPress Madara theme sites.

Downloads manga chapters and exports them to CBZ (Comic Book ZIP) and PDF formats with metadata support for Komga, Kavita, and Mihon readers.

## Features

- **Browser-based downloading** — Uses Chrome with a persistent profile for site compatibility
- **Download Queue** — Sequential execution with pause, resume, and cancel. State persists across restarts
- **Search** — Search manga across supported sites by title, with fuzzy matching and Arabic support
- **Followed Manga** — Track favorite manga and check for new chapters
- **Update Checker** — Detect new chapters for followed manga automatically
- **Metadata Collection** — Extract title, author, genres, status, and cover image
- **CBZ Export** — Comic Book ZIP with ComicInfo.xml metadata (Komga, Kavita, Mihon compatible)
- **PDF Export** — Single PDF per chapter with quality settings
- **Download History** — SQLite-based persistent history with query support
- **Resume Support** — Skip already-downloaded images on restart
- **Image Integrity** — Validates every downloaded image with magic byte checking

## Supported Sites

- **Manga Starz** (manga-starz.net)
- **Lek Manga** (lek-manga.net)
- **rocks manga** (rocksmanga.com)
- **3asq** (3asq.org)
  
## Installation

### Requirements

- Python 3.10 or higher
- Google Chrome browser
- Playwright

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd manga-downloader

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright Chromium browser
playwright install chromium
```

## Quick Start

```bash
# Initial setup (opens Chrome, follow on-screen instructions)
python setup_cookies.py

# Run the downloader
python -m manga_downloader.main
```

### Usage Examples

```
Enter URL: https://manga-starz.net/manga/berserk
# Downloads all chapters with interactive selection

Enter URL: https://manga-starz.net/manga/berserk/5
# Downloads a single chapter directly from its URL
```

## Project Structure

```
manga_downloader/
├── providers/          # Site-specific manga providers
│   └── browser_madara.py   # Primary browser-based provider
├── downloader/         # Image download engine
│   ├── image_downloader.py
│   └── session_manager.py
├── exporters/          # CBZ and PDF exporters
│   ├── cbz_exporter.py     # CBZ with ComicInfo.xml
│   └── pdf_exporter.py
├── utils/              # Helpers (logging, filesystem, retry)
├── queue_manager.py    # Download queue
├── follow_manager.py   # Followed manga manager
├── update_checker.py   # Chapter update detection
├── search.py           # Manga search
├── metadata.py         # Metadata collector
├── history.py          # Download history (SQLite)
├── cookie_manager.py   # Cookie persistence
├── config.py           # Configuration
├── models.py           # Data models
├── cli.py              # Command-line interface
└── main.py             # Entry point
```
## Windows Download

Download the latest release from:

https://github.com/Mohasr/Manga-Downloader/releases

### First Run

1. Extract the ZIP.
2. Run MangaDownloader.exe.
3. If Cloudflare verification is required, run setup_cookies.py once.
4. Enjoy.

## ScreenShots

1- The main menu 

<img width="366" height="353" alt="Screenshot 2026-06-24 170629" src="https://github.com/user-attachments/assets/56b446e7-7d42-4285-94d4-dc09d7a1aa53" />

2- Search menu

<img width="485" height="382" alt="Screenshot 2026-06-24 170709" src="https://github.com/user-attachments/assets/7b2bbbc7-96ba-491d-bdb1-f98f98517d85" />

3- Followed Manga menu

<img width="344" height="131" alt="Screenshot 2026-06-24 170758" src="https://github.com/user-attachments/assets/54c54329-9079-458b-8684-8f4db1397310" />


## Troubleshooting

**Downloads return errors after some time:** Run `python setup_cookies.py` to refresh the browser session.

**No manga found:** Verify the URL uses the format `https://<site>/manga/<manga-name>/`.

**Images appear corrupted:** This is handled automatically. Corrupted images are detected and removed — only valid images are exported.

## License

MIT License — see LICENSE file for details.
