# Changelog

## v1.2.2 (2026-06-24)

### Added
- **Rocks Manga** (rocksmanga.com) support — wp-fire theme with 74 image chapters
- **3asq** (3asq.org) support — standard Madara theme with Ajax chapters
- **CDP window minimize** — Chrome now minimizes to taskbar after opening
- **Force-load JS** — data-src images loaded via JavaScript for non-Madara sites
- **Live search filter** — type to filter results within search screen

### Fixed
- **Rocks Manga image download** — was 9/74 images, now 74/74. Removed ?style=list, added force-load JS, increased scroll coverage
- **CDP image filter** — now accepts /wp-content/uploads/ and /WP-manga/ paths
- **Chapter deduplication** — broad selectors no longer pick up manga page link itself
- **Search empty results** — Lek Manga error items no longer appear as blank search results
- **Page navigation timing** — wait_until="load" + retry loop for page.content()

### Changed
- **Chrome start** — uses CDP Browser.setWindowBounds instead of --start-minimized
- **Provider config** — use_style_list flag per site (False for rocksmanga)

---

## v1.2.1

### Fixed
- **History auto-record** — download history records after every completed download
- **Headless removed** — CF blocks headless, always runs headful
- **Menu navigation** — back to number-based input
- **Input prompts** — colon added to all prompts
- **Search integration** — text input auto-triggers search, empty results filtered

---

## v1.2.0

### Added
- **Main Menu System** — Dashboard with 8 options
- **Search Pagination** — N/P navigation, provider filter (F key)
- **Followed Manga Screen** — Add/Remove/Download/Update actions
- **Download History Screen** — View and clear history
- **Download Queue Screen** — Pause/Resume/Cancel items
- **Settings Screen** — Output dir, format, concurrency

---

## v1.1.0

### Added
- **Download Queue** — sequential execution with pause/resume/cancel
- **Followed Manga** — favorites with update checking
- **Update Checker** — detect new chapters
- **Search System** — multi-site search with Arabic support
- **Metadata Collection** — title, author, genres, cover
- **Enhanced CBZ Export** — ComicInfo.xml metadata
- **Download History** — SQLite persistent history

### Fixed
- **Chapter parser** — 374_1 correctly parsed as 374.1
- **Image corruption** — Network.loadingFinished ensures complete downloads
- **Image validation** — magic byte checking
- **Response leak** — session close on error
- **CBZ integrity** — ZipFile.testzip() verification
