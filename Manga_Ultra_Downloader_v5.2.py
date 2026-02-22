"""
Manga Downloader - ULTRA ENHANCED v5.2 (Filters "YOU MAY ALSO LIKE" Recommendations)

NEW in v5.2:
- LAYER 3B: Path-based filtering for recommendation sections
- Enhanced LAYER 3A: Extended filename patterns (may-also-like, popular, trending, card-image, poster)
- Enhanced LAYER 4: Alt/title text analysis for recommendations
- Targets "YOU MAY ALSO LIKE" manga series thumbnails specifically

v5.2 solves: Related manga recommendation images being downloaded with chapter pages
"""

import os
import sys
import asyncio
import zipfile
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from urllib.parse import urljoin, urlparse
import json
import threading
import re
import aiohttp
import aiofiles
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# GUI
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('manga_downloader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class MangaDownloader:
    """Handles downloading manga chapters with enhanced detection and filtering"""

    def __init__(self, output_dir: str = "manga_downloads", use_playwright: bool = True, 
                 progress_callback=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.use_playwright = use_playwright
        self.session = self._create_session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.progress_callback = progress_callback
        self.manga_database = {}
        self.is_stopped = False
        self.resume_state = None

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _log_progress(self, message: str, level: str = "info"):
        try:
            getattr(logger, level)(message)
        except:
            safe_message = message.encode('utf-8', errors='replace').decode('utf-8')
            getattr(logger, level)(safe_message)

        if self.progress_callback:
            try:
                self.progress_callback(message)
            except:
                pass

    def stop_download(self):
        """Stop the download process"""
        self.is_stopped = True
        self._log_progress("\n🛑 STOP REQUESTED - Finishing current chapter...", "warning")

    def resume_download(self):
        """Resume the download process"""
        self.is_stopped = False
        self._log_progress("\n▶️ RESUMING DOWNLOAD...", "info")

    def extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def extract_manga_base_url(self, manga_url: str) -> str:
        """Extract manga base URL (without chapter part)"""
        parsed = urlparse(manga_url)
        path_parts = parsed.path.rstrip('/').split('/')

        # Remove chapter part if present
        if len(path_parts) > 0 and ('chapter' in path_parts[-1].lower() or path_parts[-1].replace('.', '').isdigit()):
            path_parts = path_parts[:-1]

        base_path = '/'.join(path_parts) + '/'
        return f"{parsed.scheme}://{parsed.netloc}{base_path}"

    def is_same_manga(self, chapter_url: str, manga_base_url: str) -> bool:
        """Check if chapter URL belongs to the same manga"""
        return chapter_url.startswith(manga_base_url.rstrip('/'))

    def extract_chapter_number(self, text: str, url: str) -> Optional[str]:
        """
        ENHANCED: Universal chapter number extraction with multiple patterns
        Returns chapter number if found, None otherwise
        """
        text_lower = text.lower()
        url_lower = url.lower()

        patterns = [
            r'chapter[\s-]*([\d]+\.?[\d]*)',
            r'ch\.?[\s-]*([\d]+\.?[\d]*)',
            r'episode[\s-]*([\d]+\.?[\d]*)',
            r'ep\.?[\s-]*([\d]+\.?[\d]*)',
            r'#([\d]+\.?[\d]*)',
            r'(?:^|\s)([\d]+\.?[\d]*)(?:\s|$)',
        ]

        # Try text first
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                return match.group(1)

        # Try URL
        for pattern in patterns:
            match = re.search(pattern, url_lower)
            if match:
                return match.group(1)

        return None

    def is_chapter_link(self, url: str, text: str) -> bool:
        """
        ENHANCED: Detect if a link is a chapter link (not manga link)
        Returns True if it's a chapter, False if it's a manga list
        """
        url_lower = url.lower()
        text_lower = text.lower()

        chapter_indicators = [
            '/chapter-', '/chapter/', '/ch-', '/ch/', '/episode-', '/ep-',
        ]

        # Check URL
        if any(indicator in url_lower for indicator in chapter_indicators):
            return True

        # Check text
        if any(word in text_lower for word in ['chapter', 'ch.', 'episode', 'ep.']):
            if any(word in text_lower for word in ['latest', 'new', 'recent', 'update']):
                return False
            return True

        return False

    def is_valid_sequential_chapter(self, chapter_num: str, existing_chapters: List[str]) -> bool:
        """
        ENHANCED: Validate if chapter is part of a sequential series
        """
        try:
            current_num = float(chapter_num)

            if not existing_chapters:
                return True

            existing_nums = [float(ch) for ch in existing_chapters]
            max_existing = max(existing_nums)
            min_existing = min(existing_nums)

            if current_num > max_existing + 500:
                return False
            if current_num < min_existing - 10:
                return False

            return True
        except:
            return True

    def is_valid_chapter_image(self, img_url: str, img_tag) -> bool:
        """
        NEW v5.2: 5-LAYER IMAGE VALIDATION SYSTEM
        Enhanced to filter "YOU MAY ALSO LIKE" recommendation thumbnails

        Returns True if image is a valid chapter page
        Returns False if image should be filtered out
        """
        url_lower = img_url.lower()

        # === LAYER 1: Format validation ===
        # Reject .gif, .svg (animations, icons, UI elements)
        if url_lower.endswith(('.gif', '.svg')):
            return False

        # === LAYER 2: URL pattern rejection (FIXED in v5.1) ===
        # Specific ad directory checks (avoid matching 'uploads', 'read', 'download')
        if '/ad/' in url_lower or '/ads/' in url_lower:
            return False
        if url_lower.endswith('/ad') or url_lower.endswith('/ads'):
            return False
        if 'advertisement' in url_lower or 'advert' in url_lower:
            return False

        # General rejection patterns (removed 'ad' and 'ads')
        url_reject_patterns = [
            'logo', 'icon', 'banner', 'thumb', 'avatar', 'profile',
            'button', 'badge', 'emoji', 'sprite', 'bg-', 'background'
        ]

        if any(pattern in url_lower for pattern in url_reject_patterns):
            return False

        # === LAYER 3A: Filename pattern rejection (ENHANCED v5.2) ===
        # NEW: Extended patterns to catch recommendation thumbnails
        filename_reject_patterns = [
            'related', 'recommend', 'similar', 'more-like', 'you-may-also',
            'may-also-like', 'popular', 'trending', 'featured', 'latest',
            'card-image', 'cover', 'poster', 'series-thumb', 'manga-thumb',
            'placeholder', 'no-image', 'default'
        ]

        if any(pattern in url_lower for pattern in filename_reject_patterns):
            return False

        # === LAYER 3B: Path-based rejection (NEW v5.2) ===
        # Target "YOU MAY ALSO LIKE" sections by directory structure
        path_reject_patterns = [
            '/recommendations/', '/covers/', '/thumbnails/', '/posters/',
            '/related/', '/similar/', '/suggestions/', '/featured/',
            '/popular/', '/trending/'
        ]

        if any(pattern in url_lower for pattern in path_reject_patterns):
            return False

        # === LAYER 4: HTML attribute analysis (ENHANCED v5.2) ===
        # NEW: Check alt/title text for recommendation indicators
        if img_tag:
            try:
                alt_text = img_tag.get('alt', '').lower()
                title_text = img_tag.get('title', '').lower()

                # Recommendation section indicators
                recommendation_keywords = [
                    'you may also like', 'related manga', 'similar series',
                    'recommended', 'more like this', 'popular manga',
                    'trending', 'featured'
                ]

                combined_text = f"{alt_text} {title_text}"
                if any(keyword in combined_text for keyword in recommendation_keywords):
                    return False

                # Check for CSS classes indicating recommendations
                class_attr = ' '.join(img_tag.get('class', [])).lower()
                if any(word in class_attr for word in ['recommend', 'related', 'similar', 'card']):
                    return False
            except:
                pass

        # === LAYER 5: Dimension validation ===
        # Accept reasonable manga page dimensions
        if img_tag:
            try:
                width = img_tag.get('width')
                height = img_tag.get('height')

                if width and height:
                    w = int(width)
                    h = int(height)

                    # Reject tiny images (likely UI elements)
                    if w < 200 or h < 200:
                        return False

                    # Reject very wide/short images (likely banners)
                    aspect_ratio = w / h if h > 0 else 0
                    if aspect_ratio > 4 or aspect_ratio < 0.25:
                        return False
            except:
                pass
            
        # If passed all layers, accept as valid chapter image
        return True

    async def _get_page_with_playwright(self, url: str, is_chapter: bool = False, click_show_more: bool = True) -> str:
        """Fetch page using Playwright"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                page = await context.new_page()

                try:
                    await page.goto(url, wait_until='networkidle', timeout=60000)
                except:
                    try:
                        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    except:
                        await page.goto(url, timeout=20000)

                if not is_chapter and click_show_more:
                    show_more_selectors = [
                        'button:has-text("Show more")', 'button:has-text("Load more")',
                        'a:has-text("Show more")', 'a:has-text("Load more")',
                        '.show-more', '.load-more', '#show-more', '#load-more'
                    ]

                    for selector in show_more_selectors:
                        try:
                            await page.click(selector, timeout=2000)
                            await page.wait_for_timeout(2000)
                            self._log_progress(f" ✓ Clicked: {selector}")
                            break
                        except:
                            continue

                if is_chapter:
                    await page.wait_for_timeout(3000)

                html = await page.content()
                await browser.close()
                return html

        except Exception as e:
            self._log_progress(f"Playwright error: {str(e)}", "error")
            raise

    async def get_page_content(self, url: str, is_chapter: bool = False, click_show_more: bool = True) -> str:
        """Get page content with fallback"""
        if self.use_playwright:
            try:
                return await self._get_page_with_playwright(url, is_chapter, click_show_more)
            except Exception as e:
                self._log_progress(f"Playwright failed, using requests: {str(e)}", "warning")

        try:
            response = self.session.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self._log_progress(f"Requests also failed: {str(e)}", "error")
            raise

    async def scan_site_for_manga(self, site_url: str, max_pages: int = 5) -> Dict[str, List[Tuple[str, str]]]:
        """ENHANCED: Scan entire site for manga"""
        self._log_progress(f"\n{'='*60}")
        self._log_progress(f"SITE SCAN: {site_url}")
        self._log_progress(f"{'='*60}\n")

        domain = self.extract_domain(site_url)
        manga_links = {}

        try:
            html = await self.get_page_content(site_url, is_chapter=False, click_show_more=True)
            soup = BeautifulSoup(html, 'html.parser')

            all_links = soup.find_all('a', href=True)
            self._log_progress(f"Found {len(all_links)} total links on main page")

            manga_candidates = []
            for link in all_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)

                if self.is_chapter_link(href, text):
                    continue

                if '/manga/' in href.lower() and text and len(text) > 2:
                    full_url = urljoin(site_url, href)

                    if self.extract_domain(full_url) == domain:
                        if not any(m[0] == full_url for m in manga_candidates):
                            manga_candidates.append((full_url, text))

            self._log_progress(f"Found {len(manga_candidates)} manga candidates")

            for idx, (manga_url, manga_title) in enumerate(manga_candidates[:max_pages], 1):
                if self.is_stopped:
                    self._log_progress("Scan stopped by user", "warning")
                    break

                self._log_progress(f"\n[{idx}/{min(len(manga_candidates), max_pages)}] Processing: {manga_title[:50]}")

                try:
                    chapters = await self.extract_chapter_links(manga_url)
                    if chapters:
                        manga_links[manga_title] = chapters
                        self._log_progress(f" ✓ Found {len(chapters)} chapters")
                    else:
                        self._log_progress(f" ✗ No chapters found", "warning")
                except Exception as e:
                    self._log_progress(f" ✗ Error: {str(e)}", "error")

            self.manga_database = manga_links

            self._log_progress(f"\n{'='*60}")
            self._log_progress(f"SITE SCAN COMPLETE")
            self._log_progress(f"{'='*60}")
            self._log_progress(f"Total manga found: {len(manga_links)}")
            total_chapters = sum(len(ch) for ch in manga_links.values())
            self._log_progress(f"Total chapters: {total_chapters}")
            self._log_progress(f"{'='*60}\n")

            return manga_links

        except Exception as e:
            self._log_progress(f"Error scanning site: {str(e)}", "error")
            return {}

    async def extract_chapter_links(self, manga_url: str) -> List[Tuple[str, str]]:
        """ENHANCED: Extract ALL chapter links"""
        self._log_progress(f"Fetching chapters from: {manga_url}")
        manga_base_url = self.extract_manga_base_url(manga_url)

        try:
            html = await self.get_page_content(manga_url, is_chapter=False, click_show_more=True)
            soup = BeautifulSoup(html, 'html.parser')

            chapters = []
            chapter_links = soup.find_all('a', href=True)

            for link in chapter_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)

                chapter_num = self.extract_chapter_number(text, href)

                if chapter_num:
                    full_url = urljoin(manga_url, href)

                    if self.is_same_manga(full_url, manga_base_url):
                        existing_nums = [ch[1] for ch in chapters]
                        if self.is_valid_sequential_chapter(chapter_num, existing_nums):
                            if not any(ch[1] == chapter_num for ch in chapters):
                                chapters.append((full_url, chapter_num))

            try:
                chapters.sort(key=lambda x: float(x[1]))
            except:
                chapters.sort(key=lambda x: x[1])

            return chapters

        except Exception as e:
            self._log_progress(f"Error extracting chapters: {str(e)}", "error")
            return []

    def extract_image_urls(self, html: str, base_url: str) -> List[str]:
        """ENHANCED v5.2: Extract and filter image URLs"""
        soup = BeautifulSoup(html, 'html.parser')
        image_urls = []

        img_selectors = [
            ('img[src]', 'src'),
            ('img[data-src]', 'data-src'),
            ('img[data-lazy-src]', 'data-lazy-src'),
            ('img[data-original]', 'data-original'),
            ('img[data-lazy]', 'data-lazy'),
        ]

        for selector, attr in img_selectors:
            images = soup.select(selector)
            for img in images:
                if img.find_parent(["aside", "nav", "footer"]):
                   continue
                url = img.get(attr, '').strip()

                if url and url not in image_urls:
                    if url.startswith('data:image'):
                        continue

                    full_url = urljoin(base_url, url)

                    if full_url.startswith('http'):
                        # NEW v5.2: Apply 5-layer validation with enhanced filtering
                        if self.is_valid_chapter_image(full_url, img):
                            image_urls.append(full_url)

        return image_urls

    async def download_chapter(self, url: str, chapter_num: str, manga_title: str = "", max_concurrent: int = 5) -> Tuple[int, int]:
        """Download all images from a chapter"""
        if self.is_stopped:
            return 0, 0

        self._log_progress(f"\n📖 Chapter {chapter_num}")

        try:
            html = await self.get_page_content(url, is_chapter=True, click_show_more=False)
            image_urls = self.extract_image_urls(html, url)

            if not image_urls:
                self._log_progress(f" ✗ No images found", "warning")
                return 0, 0

            chapter_dir = self.output_dir / self._sanitize_filename(manga_title) / f"Chapter_{chapter_num}"
            chapter_dir.mkdir(parents=True, exist_ok=True)

            success = 0
            failed = 0

            semaphore = asyncio.Semaphore(max_concurrent)

            async def download_image(img_url: str, idx: int):
                if self.is_stopped:
                    return False

                async with semaphore:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(img_url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                                if response.status == 200:
                                    ext = Path(urlparse(img_url).path).suffix or '.jpg'
                                    filename = f"page_{idx:03d}{ext}"
                                    filepath = chapter_dir / filename

                                    async with aiofiles.open(filepath, 'wb') as f:
                                        await f.write(await response.read())

                                    return True
                    except:
                        pass

                    return False

            tasks = [download_image(img_url, idx) for idx, img_url in enumerate(image_urls, 1)]
            results = await asyncio.gather(*tasks)

            success = sum(1 for r in results if r)
            failed = len(results) - success

            self._log_progress(f" ✓ Downloaded: {success}/{len(image_urls)} images")

            return success, failed

        except Exception as e:
            self._log_progress(f" ✗ Error: {str(e)}", "error")
            return 0, len(image_urls) if 'image_urls' in locals() else 0

    def create_zip(self, chapter_num: str, manga_title: str = "") -> Optional[Path]:
        """Create ZIP from chapter folder"""
        try:
            chapter_dir = self.output_dir / self._sanitize_filename(manga_title) / f"Chapter_{chapter_num}"

            if not chapter_dir.exists():
                return None

            zip_path = chapter_dir.with_suffix('.zip')

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in sorted(chapter_dir.glob('*')):
                    if file.is_file():
                        zipf.write(file, file.name)

            for file in chapter_dir.glob('*'):
                if file.is_file():
                    file.unlink()
            chapter_dir.rmdir()

            return zip_path

        except Exception as e:
            self._log_progress(f"Error creating ZIP: {str(e)}", "error")
            return None

    async def download_and_zip_chapter(self, url: str, chapter_num: str, manga_title: str = "") -> bool:
        """Download chapter and create ZIP"""
        if self.is_stopped:
            return False

        success, failed = await self.download_chapter(url, chapter_num, manga_title)

        if success > 0:
            zip_path = self.create_zip(chapter_num, manga_title)
            if zip_path:
                self._log_progress(f" ✓ Created: {zip_path.name}")
                return True

        return False

    async def download_bulk_chapters(self, chapters: List[Tuple[str, str]], delay_between: float = 1.0, manga_title: str = "") -> dict:
        """ENHANCED: Download multiple chapters with stop/resume"""
        total = len(chapters)
        success_count = 0
        failed_count = 0

        self._log_progress(f"\n{'='*60}")
        self._log_progress(f"BULK DOWNLOAD: {manga_title if manga_title else 'Manga'}")
        self._log_progress(f"Total chapters: {total}")
        self._log_progress(f"{'='*60}\n")

        for idx, (url, chapter_num) in enumerate(chapters, 1):
            if self.is_stopped:
                self._log_progress(f"\n🛑 DOWNLOAD STOPPED at Chapter {chapter_num}")
                self._log_progress(f"Progress: {success_count}/{total} chapters completed")

                self.resume_state = {
                    'chapters': chapters[idx-1:],
                    'manga_title': manga_title,
                    'progress': f"{success_count}/{total}"
                }

                break

            self._log_progress(f"[{idx}/{total}] Chapter {chapter_num}")

            try:
                result = await self.download_and_zip_chapter(url, chapter_num, manga_title)
                if result:
                    success_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                self._log_progress(f" ✗ Failed: {str(e)}", "error")
                failed_count += 1

            if idx < total and not self.is_stopped:
                await asyncio.sleep(delay_between)

        if not self.is_stopped:
            self._log_progress(f"\n{'='*60}")
            self._log_progress(f"DOWNLOAD COMPLETE")
            self._log_progress(f"Success: {success_count}/{total}")
            self._log_progress(f"Failed: {failed_count}/{total}")
            self._log_progress(f"{'='*60}\n")

        return {
            'total': total,
            'success': success_count,
            'failed': failed_count,
            'stopped': self.is_stopped
        }

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for file system"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename[:200]

    async def cleanup(self):
        """Cleanup resources"""
        if self.session:
            self.session.close()

class MangaDownloaderGUI:
    """ENHANCED GUI with Stop/Resume functionality"""

    def __init__(self, root):
        self.root = root
        self.root.title("Manga Downloader v5.2 - Enhanced Filtering (Removes 'YOU MAY ALSO LIKE')")
        self.root.geometry("950x850")

        self.downloader = None
        self.extraction_result = []
        self.manga_database = {}
        self.selected_manga_chapters = {}
        self.is_downloading = False
        self.download_thread = None

        self.setup_ui()

    def setup_ui(self):
        # Main notebook with tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 1: Single Manga
        self.single_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.single_tab, text="Single Manga")
        self.setup_single_tab()

        # Tab 2: Site Scanner
        self.site_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.site_tab, text="Site Scanner")
        self.setup_site_tab()

    def setup_single_tab(self):
        """Setup single manga download tab"""
        main_frame = ttk.Frame(self.single_tab, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Single Manga Downloader", font=("Helvetica", 14, "bold")).pack(pady=10)

        ttk.Label(main_frame, text="Manga URL:").pack(anchor=tk.W)
        url_frame = ttk.Frame(main_frame)
        url_frame.pack(fill=tk.X, pady=5)

        self.url_entry = ttk.Entry(url_frame, width=70)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.url_entry.insert(0, "https://brainrotcomics.com/manga/the-empress-i-only-want-to-date-my-wife/")

        ttk.Button(url_frame, text="Extract", command=self.extract_single_manga).pack(side=tk.LEFT, padx=5)

        ttk.Label(main_frame, text="Chapters:").pack(anchor=tk.W, pady=(15, 5))

        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.chapters_list = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, height=12, selectmode=tk.MULTIPLE)
        self.chapters_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.chapters_list.yview)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Select All", command=self.select_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Deselect All", command=self.deselect_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Invert", command=self.invert_selection).pack(side=tk.LEFT, padx=5)

        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        settings_frame.pack(fill=tk.X, pady=10)

        ttk.Label(settings_frame, text="Output Dir:").grid(row=0, column=0, sticky=tk.W)
        self.output_entry = ttk.Entry(settings_frame, width=50)
        self.output_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5)
        self.output_entry.insert(0, "manga_downloads")
        ttk.Button(settings_frame, text="Browse", command=self.browse_dir).grid(row=0, column=2)

        ttk.Label(settings_frame, text="Delay (sec):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.delay_spin = ttk.Spinbox(settings_frame, from_=0.5, to=10, increment=0.5, width=10)
        self.delay_spin.set(1.5)
        self.delay_spin.grid(row=1, column=1, sticky=tk.W, padx=5)

        download_control_frame = ttk.Frame(main_frame)
        download_control_frame.pack(fill=tk.X, pady=10)

        self.start_btn = ttk.Button(download_control_frame, text="▶️ START DOWNLOAD", command=self.start_download)
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.stop_btn = ttk.Button(download_control_frame, text="🛑 STOP", command=self.stop_download, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.resume_btn = ttk.Button(download_control_frame, text="▶️ RESUME", command=self.resume_download, state=tk.DISABLED)
        self.resume_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.status_label = ttk.Label(main_frame, text="Ready", foreground="green")
        self.status_label.pack(anchor=tk.W, pady=5)

        ttk.Label(main_frame, text="Log:").pack(anchor=tk.W, pady=(15, 5))

        self.log_text = scrolledtext.ScrolledText(main_frame, height=8, width=100, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=5)

    def setup_site_tab(self):
        """Setup site scanner tab"""
        main_frame = ttk.Frame(self.site_tab, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Site-Wide Scanner", font=("Helvetica", 14, "bold")).pack(pady=10)

        ttk.Label(main_frame, text="Site URL:").pack(anchor=tk.W)
        url_frame = ttk.Frame(main_frame)
        url_frame.pack(fill=tk.X, pady=5)

        self.site_url_entry = ttk.Entry(url_frame, width=70)
        self.site_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.site_url_entry.insert(0, "https://brainrotcomics.com/")

        ttk.Button(url_frame, text="Scan Site", command=self.scan_site).pack(side=tk.LEFT, padx=5)

        ttk.Label(main_frame, text="Max Pages:").pack(anchor=tk.W, pady=(10, 5))
        self.max_pages_spin = ttk.Spinbox(main_frame, from_=1, to=50, increment=1, width=10)
        self.max_pages_spin.set(10)
        self.max_pages_spin.pack(anchor=tk.W, pady=5)

        ttk.Label(main_frame, text="Found Manga:").pack(anchor=tk.W, pady=(15, 5))

        manga_frame = ttk.Frame(main_frame)
        manga_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        manga_scroll = ttk.Scrollbar(manga_frame)
        manga_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.manga_listbox = tk.Listbox(manga_frame, yscrollcommand=manga_scroll.set, height=8)
        self.manga_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.manga_listbox.bind('<<ListboxSelect>>', self.on_manga_select)
        manga_scroll.config(command=self.manga_listbox.yview)

        ttk.Label(main_frame, text="Chapters for Selected Manga:").pack(anchor=tk.W, pady=(15, 5))

        chapters_frame = ttk.Frame(main_frame)
        chapters_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        chapters_scroll = ttk.Scrollbar(chapters_frame)
        chapters_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.site_chapters_list = tk.Listbox(chapters_frame, yscrollcommand=chapters_scroll.set, height=10, selectmode=tk.MULTIPLE)
        self.site_chapters_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chapters_scroll.config(command=self.site_chapters_list.yview)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Select All Chapters", command=self.select_all_chapters_site).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Deselect All", command=self.deselect_all_chapters_site).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Select All Manga", command=self.select_all_manga_chapters).pack(side=tk.LEFT, padx=5)

        download_control_frame = ttk.Frame(main_frame)
        download_control_frame.pack(fill=tk.X, pady=10)

        self.site_start_btn = ttk.Button(download_control_frame, text="▶️ START BULK DOWNLOAD", command=self.start_bulk_download)
        self.site_start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.site_stop_btn = ttk.Button(download_control_frame, text="🛑 STOP", command=self.stop_download, state=tk.DISABLED)
        self.site_stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.site_resume_btn = ttk.Button(download_control_frame, text="▶️ RESUME", command=self.resume_download, state=tk.DISABLED)
        self.site_resume_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.site_status_label = ttk.Label(main_frame, text="Ready", foreground="green")
        self.site_status_label.pack(anchor=tk.W, pady=5)

        ttk.Label(main_frame, text="Log:").pack(anchor=tk.W, pady=(15, 5))

        self.site_log_text = scrolledtext.ScrolledText(main_frame, height=8, width=100, state=tk.DISABLED)
        self.site_log_text.pack(fill=tk.BOTH, expand=True, pady=5)

    def log_message(self, message: str):
        """Log message to both tabs"""
        try:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

            self.site_log_text.config(state=tk.NORMAL)
            self.site_log_text.insert(tk.END, message + "\n")
            self.site_log_text.see(tk.END)
            self.site_log_text.config(state=tk.DISABLED)
        except:
            pass

    def update_status(self, message: str, color: str = "green"):
        """Update status label"""
        self.status_label.config(text=message, foreground=color)
        self.site_status_label.config(text=message, foreground=color)

    def browse_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, directory)

    def select_all(self):
        self.chapters_list.select_set(0, tk.END)

    def deselect_all(self):
        self.chapters_list.selection_clear(0, tk.END)

    def invert_selection(self):
        for i in range(self.chapters_list.size()):
            if self.chapters_list.selection_includes(i):
                self.chapters_list.selection_clear(i)
            else:
                self.chapters_list.select_set(i)

    def select_all_chapters_site(self):
        self.site_chapters_list.select_set(0, tk.END)

    def deselect_all_chapters_site(self):
        self.site_chapters_list.selection_clear(0, tk.END)

    def select_all_manga_chapters(self):
        """Select all chapters for all manga"""
        for manga_title in self.manga_database.keys():
            chapters = self.manga_database[manga_title]
            self.selected_manga_chapters[manga_title] = chapters
        self.log_message(f"Selected all chapters for {len(self.manga_database)} manga")

    def extract_single_manga(self):
        """Extract chapters from single manga URL"""
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter a manga URL")
            return

        self.update_status("Extracting chapters...", "blue")
        self.log_message(f"Extracting from: {url}")

        def run_extraction():
            try:
                output_dir = self.output_entry.get().strip()
                self.downloader = MangaDownloader(output_dir, use_playwright=True, progress_callback=self.log_message)

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                chapters = loop.run_until_complete(self.downloader.extract_chapter_links(url))
                self.extraction_result = chapters

                self.root.after(0, lambda: self.display_chapters(chapters))
                loop.close()

            except Exception as e:
                self.root.after(0, lambda: self.update_status(f"Error: {str(e)}", "red"))
                self.root.after(0, lambda: self.log_message(f"Error: {str(e)}"))

        threading.Thread(target=run_extraction, daemon=True).start()

    def display_chapters(self, chapters):
        """Display extracted chapters"""
        self.chapters_list.delete(0, tk.END)

        if not chapters:
            self.update_status("No chapters found", "orange")
            self.log_message("No chapters found")
            return

        for url, chapter_num in chapters:
            self.chapters_list.insert(tk.END, f"Chapter {chapter_num}")

        self.update_status(f"Found {len(chapters)} chapters", "green")
        self.log_message(f"Found {len(chapters)} chapters")

    def start_download(self):
        """Start downloading selected chapters"""
        if self.is_downloading:
            messagebox.showwarning("Warning", "Download already in progress")
            return

        selected_indices = self.chapters_list.curselection()
        if not selected_indices:
            messagebox.showerror("Error", "Please select chapters to download")
            return

        selected_chapters = [self.extraction_result[i] for i in selected_indices]

        self.is_downloading = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.resume_btn.config(state=tk.DISABLED)
        self.update_status("Downloading...", "blue")

        def run_download():
            try:
                output_dir = self.output_entry.get().strip()
                delay = float(self.delay_spin.get())

                if not self.downloader:
                    self.downloader = MangaDownloader(output_dir, use_playwright=True, progress_callback=self.log_message)

                self.downloader.is_stopped = False
                self.downloader.output_dir = Path(output_dir)

                manga_url = self.url_entry.get().strip()
                manga_title = Path(urlparse(manga_url).path).parts[-1] if manga_url else "manga"

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self.downloader.download_bulk_chapters(selected_chapters, delay, manga_title)
                )
                loop.close()

                self.root.after(0, lambda: self.download_complete(result))

            except Exception as e:
                self.root.after(0, lambda: self.update_status(f"Error: {str(e)}", "red"))
                self.root.after(0, lambda: self.log_message(f"Error: {str(e)}"))
                self.root.after(0, self.reset_download_state)

        self.download_thread = threading.Thread(target=run_download, daemon=True)
        self.download_thread.start()

    def stop_download(self):
        """Stop current download"""
        if self.downloader:
            self.downloader.stop_download()
            self.stop_btn.config(state=tk.DISABLED)
            self.update_status("Stopping...", "orange")

    def resume_download(self):
        """Resume stopped download"""
        if not self.downloader or not self.downloader.resume_state:
            messagebox.showerror("Error", "No download to resume")
            return

        self.is_downloading = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.resume_btn.config(state=tk.DISABLED)
        self.update_status("Resuming download...", "blue")
        self.log_message("\n▶️ RESUMING DOWNLOAD...")

        def run_resume():
            try:
                state = self.downloader.resume_state
                self.downloader.resume_download()
                delay = float(self.delay_spin.get())

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self.downloader.download_bulk_chapters(
                        state['chapters'],
                        delay,
                        state['manga_title']
                    )
                )
                loop.close()

                self.root.after(0, lambda: self.download_complete(result))

            except Exception as e:
                self.root.after(0, lambda: self.update_status(f"Error: {str(e)}", "red"))
                self.root.after(0, lambda: self.log_message(f"Error: {str(e)}"))
                self.root.after(0, self.reset_download_state)

        self.download_thread = threading.Thread(target=run_resume, daemon=True)
        self.download_thread.start()

    def download_complete(self, result):
        """Handle download completion"""
        if result.get('stopped'):
            self.update_status("Download stopped", "orange")
            self.log_message(f"\n🛑 Download stopped - Progress: {result['success']}/{result['total']}")
            self.stop_btn.config(state=tk.DISABLED)
            self.resume_btn.config(state=tk.NORMAL)
            self.start_btn.config(state=tk.NORMAL)
        else:
            self.update_status("Download complete", "green")
            self.log_message(f"\n✓ All downloads complete: {result['success']}/{result['total']} successful")
            self.reset_download_state()

        self.is_downloading = False

    def reset_download_state(self):
        """Reset download buttons to initial state"""
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.site_start_btn.config(state=tk.NORMAL)
        self.site_stop_btn.config(state=tk.DISABLED)
        self.site_resume_btn.config(state=tk.DISABLED)
        self.is_downloading = False

    def scan_site(self):
        """Scan entire site for manga"""
        site_url = self.site_url_entry.get().strip()
        if not site_url:
            messagebox.showerror("Error", "Please enter a site URL")
            return

        self.update_status("Scanning site...", "blue")
        self.log_message(f"\nScanning site: {site_url}")

        def run_scan():
            try:
                max_pages = int(self.max_pages_spin.get())
                output_dir = self.output_entry.get().strip() if hasattr(self, 'output_entry') else "manga_downloads"
                self.downloader = MangaDownloader(output_dir, use_playwright=True, progress_callback=self.log_message)

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                manga_dict = loop.run_until_complete(
                    self.downloader.scan_site_for_manga(site_url, max_pages)
                )
                self.manga_database = manga_dict

                self.root.after(0, lambda: self.display_manga_list(manga_dict))
                loop.close()

            except Exception as e:
                self.root.after(0, lambda: self.update_status(f"Error: {str(e)}", "red"))
                self.root.after(0, lambda: self.log_message(f"Error: {str(e)}"))

        threading.Thread(target=run_scan, daemon=True).start()

    def display_manga_list(self, manga_dict):
        """Display found manga in listbox"""
        self.manga_listbox.delete(0, tk.END)

        if not manga_dict:
            self.update_status("No manga found", "orange")
            self.log_message("No manga found on site")
            return

        for manga_title, chapters in manga_dict.items():
            display_text = f"{manga_title} ({len(chapters)} chapters)"
            self.manga_listbox.insert(tk.END, display_text)

        self.update_status(f"Found {len(manga_dict)} manga", "green")
        self.log_message(f"\nScan complete: {len(manga_dict)} manga found")

    def on_manga_select(self, event):
        """Handle manga selection to show chapters"""
        selection = self.manga_listbox.curselection()
        if not selection:
            return

        idx = selection[0]
        manga_title = list(self.manga_database.keys())[idx]
        chapters = self.manga_database[manga_title]

        self.site_chapters_list.delete(0, tk.END)
        for url, chapter_num in chapters:
            self.site_chapters_list.insert(tk.END, f"Chapter {chapter_num}")

        self.current_manga = manga_title
        self.log_message(f"\nSelected: {manga_title} - {len(chapters)} chapters")

    def start_bulk_download(self):
        """Start bulk download for selected manga chapters"""
        if self.is_downloading:
            messagebox.showwarning("Warning", "Download already in progress")
            return

        if not self.manga_database:
            messagebox.showerror("Error", "Please scan site first")
            return

        if not hasattr(self, 'current_manga'):
            messagebox.showerror("Error", "Please select a manga")
            return

        selected_indices = self.site_chapters_list.curselection()
        if not selected_indices:
            messagebox.showerror("Error", "Please select chapters to download")
            return

        manga_title = self.current_manga
        all_chapters = self.manga_database[manga_title]
        selected_chapters = [all_chapters[i] for i in selected_indices]

        self.is_downloading = True
        self.site_start_btn.config(state=tk.DISABLED)
        self.site_stop_btn.config(state=tk.NORMAL)
        self.site_resume_btn.config(state=tk.DISABLED)
        self.update_status(f"Downloading {len(selected_chapters)} chapters...", "blue")

        def run_bulk_download():
            try:
                output_dir = self.output_entry.get().strip() if hasattr(self, 'output_entry') else "manga_downloads"
                delay = float(self.delay_spin.get()) if hasattr(self, 'delay_spin') else 1.5

                if not self.downloader:
                    self.downloader = MangaDownloader(output_dir, use_playwright=True, progress_callback=self.log_message)

                self.downloader.is_stopped = False
                self.downloader.output_dir = Path(output_dir)

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self.downloader.download_bulk_chapters(selected_chapters, delay, manga_title)
                )
                loop.close()

                self.root.after(0, lambda: self.download_complete(result))

            except Exception as e:
                self.root.after(0, lambda: self.update_status(f"Error: {str(e)}", "red"))
                self.root.after(0, lambda: self.log_message(f"Error: {str(e)}"))
                self.root.after(0, self.reset_download_state)

        self.download_thread = threading.Thread(target=run_bulk_download, daemon=True)
        self.download_thread.start()


def main():
    """Main entry point"""
    root = tk.Tk()
    app = MangaDownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()