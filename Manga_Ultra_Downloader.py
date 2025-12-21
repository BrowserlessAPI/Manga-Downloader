"""
Manga Downloader - MEGA ENHANCED v3 (Folder Organization)
Features: Site scanning + Multi-manga extraction + Bulk download all chapters
BUG FIX v3: Creates separate manga folders with chapters inside each manga folder
Now: /manga_bulk_downloads/Manga Title/Chapter1/, /Chapter2/, etc.
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
    """Handles downloading manga chapters + Site-wide scanning with proper folder organization"""
    
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
    
    def extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def extract_manga_base_url(self, manga_url: str) -> str:
        parsed = urlparse(manga_url)
        path = parsed.path.rstrip('/')
        if '/manga/' in path:
            base_part = path.split('/chapter')[0]
            return base_part.rstrip('/') + '/'
        return path.rstrip('/') + '/'
    
    def is_same_manga(self, chapter_url: str, manga_base_url: str) -> bool:
        parsed_chapter = urlparse(chapter_url)
        chapter_path = parsed_chapter.path.lower()
        manga_base_path = manga_base_url.lower().rstrip('/')
        return chapter_path.startswith(manga_base_path)
    
    def is_chapter_link(self, url: str, text: str) -> bool:
        """CRITICAL: Check if a link is a chapter link (not a manga main page)"""
        text_lower = text.lower()
        url_lower = url.lower()
        
        # Check text for chapter markers
        chapter_markers = ['chapter', 'ch.', 'ch-', 'ch ', 'ep.', 'episode']
        if any(marker in text_lower for marker in chapter_markers):
            return True
        
        # Check URL for chapter markers
        if any(marker in url_lower for marker in ['/chapter', '/ch-', '/ep-', '/episode']):
            return True
        
        return False
    
    def sanitize_folder_name(self, name: str) -> str:
        """Remove invalid characters from folder names"""
        invalid_chars = r'[<>:"/\\|?*]'
        sanitized = re.sub(invalid_chars, '', name)
        return sanitized.strip()[:100]  # Limit to 100 chars
    
    async def _get_page_with_playwright(self, url: str, is_chapter: bool = False, click_show_more: bool = True) -> str:
        """Fetch page with Show More button clicking support"""
        if not self.use_playwright:
            raise ValueError("Playwright disabled")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                timeout = 180000 if is_chapter else 60000
                self._log_progress(f"Loading page (timeout: {timeout/1000:.0f}s)...")
                
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
                except:
                    self._log_progress("DOMContentLoaded timeout, using load instead...")
                    await page.goto(url, wait_until='load', timeout=timeout)
                
                await page.wait_for_timeout(2000)
                
                if not is_chapter and click_show_more:
                    self._log_progress("Clicking 'Show more' button to load all chapters...")
                    
                    try:
                        show_more_selectors = [
                            'button:has-text("Show more")',
                            'button:contains("Show more")',
                            '.show-more',
                            '[class*="show-more"]',
                            'button[class*="more"]',
                        ]
                        
                        clicked = False
                        for selector in show_more_selectors:
                            try:
                                element = page.locator(selector).first
                                if await element.is_visible():
                                    self._log_progress(f"Found 'Show more' with selector: {selector}")
                                    await element.click()
                                    clicked = True
                                    await page.wait_for_timeout(3000)
                                    break
                            except:
                                continue
                    except Exception as e:
                        self._log_progress(f"Error clicking Show more: {str(e)}")
                    
                    self._log_progress("Scrolling page to load all chapters...")
                    for i in range(8):
                        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(800)
                        self._log_progress(f"Scroll pass {i+1}/8")
                
                elif is_chapter:
                    self._log_progress("Waiting for chapter images to load...")
                    await page.wait_for_timeout(3000)
                
                content = await page.content()
                await browser.close()
                
                self._log_progress(f"Page loaded: {len(content)} bytes")
                return content
        
        except Exception as e:
            self._log_progress(f"Playwright error: {str(e)}", "error")
            raise
    
    async def get_page_content(self, url: str, is_chapter: bool = False, click_show_more: bool = True) -> str:
        try:
            self._log_progress(f"Fetching: {url}")
            return await self._get_page_with_playwright(url, is_chapter=is_chapter, click_show_more=click_show_more)
        except Exception as e:
            self._log_progress(f"Failed: {str(e)}", "error")
            raise
    
    async def scan_site_for_manga(self, site_url: str, max_pages: int = 5) -> Dict[str, List[Tuple[str, str]]]:
        """Scan entire site for manga posts and their chapters"""
        self._log_progress(f"\n{'='*60}")
        self._log_progress(f"SITE SCAN: {site_url}")
        self._log_progress(f"{'='*60}\n")
        
        domain = self.extract_domain(site_url)
        manga_links = {}
        
        try:
            # Start from main page
            html = await self.get_page_content(site_url, is_chapter=False, click_show_more=True)
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find all manga/series links
            all_links = soup.find_all('a', href=True)
            self._log_progress(f"Found {len(all_links)} total links on main page")
            
            manga_candidates = []
            for link in all_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)
                
                # CRITICAL FIX: Filter out chapter links BEFORE adding to manga list
                if self.is_chapter_link(href, text):
                    continue
                
                # Look for manga links
                if '/manga/' in href.lower() and text and len(text) > 2:
                    full_url = urljoin(site_url, href)
                    
                    # Check if it's from same domain
                    if self.extract_domain(full_url) == domain:
                        # Avoid duplicates
                        if not any(m[0] == full_url for m in manga_candidates):
                            manga_candidates.append((full_url, text))
            
            self._log_progress(f"Found {len(manga_candidates)} manga candidates")
            
            # Extract chapters for each manga
            for idx, (manga_url, manga_title) in enumerate(manga_candidates[:max_pages], 1):
                self._log_progress(f"\n[{idx}/{min(len(manga_candidates), max_pages)}] Processing: {manga_title[:50]}")
                
                try:
                    chapters = await self.extract_chapter_links(manga_url)
                    if chapters:
                        manga_links[manga_title] = chapters
                        self._log_progress(f"  ✓ Found {len(chapters)} chapters")
                    else:
                        self._log_progress(f"  ✗ No chapters found", "warning")
                
                except Exception as e:
                    self._log_progress(f"  ✗ Error: {str(e)}", "error")
            
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
        """Extract ALL chapter links including after Show More button"""
        self._log_progress(f"Fetching chapters from: {manga_url}")
        
        manga_base_url = self.extract_manga_base_url(manga_url)
        
        try:
            html = await self.get_page_content(manga_url, is_chapter=False, click_show_more=True)
            soup = BeautifulSoup(html, 'html.parser')
            
            chapters = []
            chapter_links = soup.find_all('a', href=True)
            
            for link in chapter_links:
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                
                if 'chapter' in text or 'ch' in text or 'chapter' in href.lower():
                    match = re.search(r'chapter\s*(\d+\.?\d*)', text, re.IGNORECASE)
                    if not match:
                        match = re.search(r'ch\.?\s*(\d+\.?\d*)', text, re.IGNORECASE)
                    if not match:
                        match = re.search(r'(?:chapter|ch)[-/](\d+\.?\d*)', href, re.IGNORECASE)
                    
                    if match:
                        chapter_num = match.group(1)
                        full_url = urljoin(manga_url, href)
                        
                        if self.is_same_manga(full_url, manga_base_url):
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
        soup = BeautifulSoup(html, 'html.parser')
        image_urls = []
        img_selectors = [
            ('img[src]', 'src'),
            ('img[data-src]', 'data-src'),
            ('img[data-lazy-src]', 'data-lazy-src'),
        ]
        seen = set()
        
        for selector, attr in img_selectors:
            elements = soup.select(selector)
            for elem in elements:
                url = elem.get(attr, '')
                if not url:
                    continue
                if ',' in url:
                    url = url.split(',')[0].strip().split()[0]
                absolute_url = urljoin(base_url, url)
                if any(x in absolute_url.lower() for x in ['logo', 'icon', 'avatar']):
                    continue
                if absolute_url not in seen:
                    image_urls.append(absolute_url)
                    seen.add(absolute_url)
        
        return image_urls
    
    async def download_image(self, url: str, filepath: Path, session: aiohttp.ClientSession) -> bool:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30), 
                                   headers=self.headers, ssl=False) as resp:
                if resp.status == 200:
                    async with aiofiles.open(filepath, 'wb') as f:
                        await f.write(await resp.read())
                    return True
                return False
        except:
            return False
    
    async def download_chapter(self, url: str, chapter_num: str, manga_title: str = "", max_concurrent: int = 5) -> Tuple[int, int]:
        """Download chapter with optional manga folder organization"""
        self._log_progress(f"Downloading Chapter {chapter_num}")
        
        # Create manga folder first if manga_title provided
        if manga_title:
            manga_folder = self.output_dir / self.sanitize_folder_name(manga_title)
            manga_folder.mkdir(exist_ok=True)
            chapter_folder = manga_folder / f"Chapter{chapter_num}"
        else:
            chapter_folder = self.output_dir / f"Chapter{chapter_num}"
        
        chapter_folder.mkdir(exist_ok=True)
        
        try:
            html = await self.get_page_content(url, is_chapter=True, click_show_more=False)
            image_urls = self.extract_image_urls(html, url)
            
            if not image_urls:
                self._log_progress(f"No images found for Chapter {chapter_num}")
                return 0, 0
            
            self._log_progress(f"Chapter {chapter_num}: Downloading {len(image_urls)} images")
            
            connector = aiohttp.TCPConnector(limit=max_concurrent)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = []
                for idx, img_url in enumerate(image_urls, 1):
                    parsed_url = urlparse(img_url.split('?')[0])
                    ext = Path(parsed_url.path).suffix or '.jpg'
                    filepath = chapter_folder / f"{idx:03d}{ext}"
                    task = self.download_image(img_url, filepath, session)
                    tasks.append(task)
                
                results = await asyncio.gather(*tasks)
                successful = sum(results)
                self._log_progress(f"Chapter {chapter_num}: {successful}/{len(image_urls)} images")
                return successful, len(image_urls)
        
        except Exception as e:
            self._log_progress(f"Error downloading Chapter {chapter_num}: {str(e)}", "error")
            return 0, 0
    
    def create_zip(self, chapter_num: str, manga_title: str = "") -> Optional[Path]:
        """Create ZIP file for chapter"""
        if manga_title:
            manga_folder = self.output_dir / self.sanitize_folder_name(manga_title)
            chapter_folder = manga_folder / f"Chapter{chapter_num}"
        else:
            chapter_folder = self.output_dir / f"Chapter{chapter_num}"
        
        if not chapter_folder.exists():
            return None
        
        images = list(chapter_folder.glob('*'))
        if not images:
            return None
        
        zip_path = chapter_folder.parent / f"Chapter{chapter_num}.zip"
        
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for image_file in sorted(images):
                    if image_file.is_file():
                        zipf.write(image_file, arcname=image_file.name)
            self._log_progress(f"Created ZIP: {zip_path}")
            return zip_path
        except:
            return None
    
    async def download_and_zip_chapter(self, url: str, chapter_num: str, manga_title: str = "") -> bool:
        """Download and zip chapter with manga folder organization"""
        successful, total = await self.download_chapter(url, chapter_num, manga_title)
        if successful > 0:
            zip_path = self.create_zip(chapter_num, manga_title)
            return zip_path is not None
        return False
    
    async def download_bulk_chapters(self, chapters: List[Tuple[str, str]], delay_between: float = 1.0, manga_title: str = "") -> dict:
        """Download multiple chapters with manga folder organization"""
        stats = {'total_chapters': len(chapters), 'successful': 0, 'failed': 0, 'details': []}
        
        for idx, (url, chapter_num) in enumerate(chapters):
            self._log_progress(f"[{idx + 1}/{len(chapters)}] Chapter {chapter_num}")
            
            try:
                success = await self.download_and_zip_chapter(url, chapter_num, manga_title)
                if success:
                    stats['successful'] += 1
                else:
                    stats['failed'] += 1
                stats['details'].append({'chapter': chapter_num, 'success': success})
                
                if idx < len(chapters) - 1:
                    await asyncio.sleep(delay_between)
            except Exception as e:
                self._log_progress(f"Failed Chapter {chapter_num}: {str(e)}", "error")
                stats['failed'] += 1
        
        return stats
    
    async def download_all_manga_bulk(self, manga_dict: Dict[str, List[Tuple[str, str]]], 
                                      delay_between: float = 1.0) -> dict:
        """Download all chapters from multiple manga with separate folders"""
        total_stats = {'total_manga': len(manga_dict), 'total_chapters': 0, 'successful': 0, 'failed': 0}
        
        for manga_title, chapters in manga_dict.items():
            total_stats['total_chapters'] += len(chapters)
            
            self._log_progress(f"\n{'='*60}")
            self._log_progress(f"Downloading: {manga_title}")
            self._log_progress(f"Total Chapters: {len(chapters)}")
            self._log_progress(f"{'='*60}\n")
            
            stats = await self.download_bulk_chapters(chapters, delay_between, manga_title)
            total_stats['successful'] += stats['successful']
            total_stats['failed'] += stats['failed']
        
        self._log_progress(f"\n{'='*60}")
        self._log_progress(f"BULK DOWNLOAD COMPLETE")
        self._log_progress(f"Total Manga: {total_stats['total_manga']}")
        self._log_progress(f"Total Chapters: {total_stats['total_chapters']}")
        self._log_progress(f"Successful: {total_stats['successful']}")
        self._log_progress(f"Failed: {total_stats['failed']}")
        self._log_progress(f"{'='*60}\n")
        
        return total_stats


class MangaDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Manga Downloader - MEGA (Site Scanner)")
        self.root.geometry("950x800")
        self.downloader = None
        self.extraction_result = []
        self.manga_database = {}
        self.selected_manga_chapters = {}
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
        
        self.chapters_list = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, height=12)
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
        
        ttk.Button(main_frame, text="START DOWNLOAD", command=self.start_download).pack(fill=tk.X, pady=15)
        
        self.status_label = ttk.Label(main_frame, text="Ready", foreground="green")
        self.status_label.pack(anchor=tk.W, pady=5)
        
        ttk.Label(main_frame, text="Log:").pack(anchor=tk.W, pady=(15, 5))
        
        self.log_text = scrolledtext.ScrolledText(main_frame, height=8, width=100, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=5)
    
    def setup_site_tab(self):
        """Setup site scanner tab - FIXED: No chapters in manga list"""
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
        
        # Manga list and chapters
        ttk.Label(main_frame, text="Manga List:").pack(anchor=tk.W, pady=(15, 5))
        
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Left: Manga list
        left_frame = ttk.Frame(list_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        ttk.Label(left_frame, text="Manga:").pack(anchor=tk.W)
        scrollbar_left = ttk.Scrollbar(left_frame)
        scrollbar_left.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.manga_list = tk.Listbox(left_frame, yscrollcommand=scrollbar_left.set, height=12)
        self.manga_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.manga_list.bind('<<ListboxSelect>>', self.on_manga_select)
        scrollbar_left.config(command=self.manga_list.yview)
        
        # Right: Chapters for selected manga
        right_frame = ttk.Frame(list_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        ttk.Label(right_frame, text="Chapters:").pack(anchor=tk.W)
        scrollbar_right = ttk.Scrollbar(right_frame)
        scrollbar_right.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.manga_chapters_list = tk.Listbox(right_frame, yscrollcommand=scrollbar_right.set, height=12)
        self.manga_chapters_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_right.config(command=self.manga_chapters_list.yview)
        
        # Selection buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="Select All Manga", command=self.select_all_manga).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Select All Chapters", command=self.select_all_chapters_site).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Deselect All", command=self.deselect_all_site).pack(side=tk.LEFT, padx=5)
        
        # Settings
        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        settings_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(settings_frame, text="Max manga to scan:").grid(row=0, column=0, sticky=tk.W)
        self.max_manga_spin = ttk.Spinbox(settings_frame, from_=1, to=100, increment=1, width=10)
        self.max_manga_spin.set(10)
        self.max_manga_spin.grid(row=0, column=1, sticky=tk.W, padx=5)
        
        ttk.Label(settings_frame, text="Output Dir:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.site_output_entry = ttk.Entry(settings_frame, width=50)
        self.site_output_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5)
        self.site_output_entry.insert(0, "manga_bulk_downloads")
        ttk.Button(settings_frame, text="Browse", command=self.browse_site_dir).grid(row=1, column=2)
        
        ttk.Label(settings_frame, text="Delay (sec):").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.site_delay_spin = ttk.Spinbox(settings_frame, from_=0.5, to=10, increment=0.5, width=10)
        self.site_delay_spin.set(1.5)
        self.site_delay_spin.grid(row=2, column=1, sticky=tk.W, padx=5)
        
        ttk.Button(main_frame, text="BULK DOWNLOAD ALL SELECTED", command=self.bulk_download_all).pack(fill=tk.X, pady=15)
        
        self.site_status = ttk.Label(main_frame, text="Ready", foreground="green")
        self.site_status.pack(anchor=tk.W, pady=5)
        
        ttk.Label(main_frame, text="Log:").pack(anchor=tk.W, pady=(15, 5))
        
        self.site_log_text = scrolledtext.ScrolledText(main_frame, height=8, width=100, state=tk.DISABLED)
        self.site_log_text.pack(fill=tk.BOTH, expand=True, pady=5)
    
    def log(self, msg: str, tab="single"):
        log_widget = self.log_text if tab == "single" else self.site_log_text
        log_widget.config(state=tk.NORMAL)
        try:
            log_widget.insert(tk.END, f"{msg}\n")
        except:
            log_widget.insert(tk.END, f"{msg.encode('utf-8', errors='ignore').decode()}\n")
        log_widget.see(tk.END)
        log_widget.config(state=tk.DISABLED)
        self.root.update()
    
    def extract_single_manga(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Error", "Enter URL")
            return
        
        self.log(f"Extracting from: {url}\n")
        self.status_label.config(text="Extracting...", foreground="blue")
        
        def thread_func():
            try:
                self.downloader = MangaDownloader(
                    output_dir=self.output_entry.get(),
                    progress_callback=lambda msg: self.log(msg, "single")
                )
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                chapters = loop.run_until_complete(self.downloader.extract_chapter_links(url))
                loop.close()
                
                self.extraction_result = chapters
                self.chapters_list.delete(0, tk.END)
                for _, ch_num in chapters:
                    self.chapters_list.insert(tk.END, f"Chapter {ch_num}")
                
                self.log(f"[SUCCESS] Found {len(chapters)} chapters!")
                self.status_label.config(text=f"Found {len(chapters)} chapters", foreground="green")
            except Exception as e:
                self.log(f"[ERROR] {str(e)}")
                self.status_label.config(text="Error", foreground="red")
        
        threading.Thread(target=thread_func, daemon=True).start()
    
    def scan_site(self):
        site_url = self.site_url_entry.get().strip()
        if not site_url:
            messagebox.showerror("Error", "Enter site URL")
            return
        
        max_pages = int(self.max_manga_spin.get())
        self.log(f"Scanning {max_pages} manga from: {site_url}\n", "site")
        self.site_status.config(text="Scanning...", foreground="blue")
        
        def thread_func():
            try:
                self.downloader = MangaDownloader(
                    output_dir=self.site_output_entry.get(),
                    progress_callback=lambda msg: self.log(msg, "site")
                )
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                manga_db = loop.run_until_complete(
                    self.downloader.scan_site_for_manga(site_url, max_pages)
                )
                loop.close()
                
                self.manga_database = manga_db
                
                # Display only manga titles (NO CHAPTERS)
                self.manga_list.delete(0, tk.END)
                for manga_title in manga_db.keys():
                    self.manga_list.insert(tk.END, manga_title)
                
                self.log(f"\n[SUCCESS] Found {len(manga_db)} manga!")
                self.site_status.config(text=f"Found {len(manga_db)} manga", foreground="green")
            except Exception as e:
                self.log(f"[ERROR] {str(e)}", "site")
                self.site_status.config(text="Error", foreground="red")
        
        threading.Thread(target=thread_func, daemon=True).start()
    
    def on_manga_select(self, event):
        selection = self.manga_list.curselection()
        if not selection:
            return
        
        manga_title = self.manga_list.get(selection[0])
        chapters = self.manga_database.get(manga_title, [])
        
        self.manga_chapters_list.delete(0, tk.END)
        for _, ch_num in chapters:
            self.manga_chapters_list.insert(tk.END, f"Chapter {ch_num}")
    
    def select_all(self):
        self.chapters_list.select_set(0, tk.END)
    
    def deselect_all(self):
        self.chapters_list.select_clear(0, tk.END)
    
    def invert_selection(self):
        selected = set(self.chapters_list.curselection())
        all_idx = set(range(self.chapters_list.size()))
        self.chapters_list.select_clear(0, tk.END)
        for idx in all_idx - selected:
            self.chapters_list.select_set(idx)
    
    def select_all_manga(self):
        self.manga_list.select_set(0, tk.END)
    
    def select_all_chapters_site(self):
        self.manga_chapters_list.select_set(0, tk.END)
    
    def deselect_all_site(self):
        self.manga_list.select_clear(0, tk.END)
        self.manga_chapters_list.select_clear(0, tk.END)
    
    def browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, d)
    
    def browse_site_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.site_output_entry.delete(0, tk.END)
            self.site_output_entry.insert(0, d)
    
    def start_download(self):
        if not self.extraction_result:
            messagebox.showerror("Error", "Extract chapters first")
            return
        
        selected = self.chapters_list.curselection()
        if not selected:
            messagebox.showerror("Error", "Select chapters")
            return
        
        chapters = [self.extraction_result[i] for i in selected]
        self.log(f"\nDownloading {len(chapters)} chapters...\n")
        self.status_label.config(text="Downloading...", foreground="blue")
        
        def thread_func():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                stats = loop.run_until_complete(
                    self.downloader.download_bulk_chapters(chapters, float(self.delay_spin.get()))
                )
                loop.close()
                
                self.log(f"\n[SUCCESS] {stats['successful']}/{stats['total_chapters']} chapters downloaded")
                self.status_label.config(text=f"Complete! {stats['successful']}/{stats['total_chapters']}", foreground="green")
                messagebox.showinfo("Success", f"Downloaded {stats['successful']} chapters")
            except Exception as e:
                self.log(f"[ERROR] {str(e)}")
                self.status_label.config(text="Error", foreground="red")
        
        threading.Thread(target=thread_func, daemon=True).start()
    
    def bulk_download_all(self):
        selected_manga_idx = self.manga_list.curselection()
        if not selected_manga_idx:
            messagebox.showerror("Error", "Select manga")
            return
        
        selected_manga_titles = [self.manga_list.get(i) for i in selected_manga_idx]
        selected_manga = {title: self.manga_database[title] for title in selected_manga_titles}
        
        total_chapters = sum(len(ch) for ch in selected_manga.values())
        self.log(f"\nBULK downloading {len(selected_manga)} manga with {total_chapters} chapters...\n", "site")
        self.site_status.config(text="Downloading...", foreground="blue")
        
        def thread_func():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                stats = loop.run_until_complete(
                    self.downloader.download_all_manga_bulk(selected_manga, float(self.site_delay_spin.get()))
                )
                loop.close()
                
                self.log(f"\n[SUCCESS] {stats['successful']}/{stats['total_chapters']} total chapters downloaded", "site")
                self.site_status.config(text=f"Complete! {stats['successful']}/{stats['total_chapters']}", foreground="green")
                messagebox.showinfo("Success", f"Downloaded {stats['successful']} chapters from {stats['total_manga']} manga")
            except Exception as e:
                self.log(f"[ERROR] {str(e)}", "site")
                self.site_status.config(text="Error", foreground="red")
        
        threading.Thread(target=thread_func, daemon=True).start()


def main():
    root = tk.Tk()
    gui = MangaDownloaderGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()