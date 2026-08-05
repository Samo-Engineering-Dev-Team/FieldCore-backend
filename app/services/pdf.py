import urllib.request
import urllib.error
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import socket
from urllib.parse import urlparse, unquote
from io import BytesIO
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
    KeepTogether,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.graphics.shapes import Circle, Drawing, String
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from xml.sax.saxutils import escape
from PIL import Image as PILImage, ImageOps

from loguru import logger as LOG
from app.core.settings import app_settings
from app.models import Report
from app.models.report_data import CABINET_CHECK_LABELS, HOSTED_SITE_SECTIONS, SITE_CHECK_LABELS
from app.models.sheq_submission import (
    DAILY_RISK_MATRIX,
    DAILY_RISK_MATRIX_GROUP_TITLES,
    MASTER_SAFETY_PHOTO_SLOTS,
    MASTER_SAFETY_SECTIONS,
    MASTER_SAFETY_SECTION_TITLES,
    VEHICLE_PRE_TRIP_LABELS,
)
from app.utils.enums import ReportType, SheqChecklistType
from app.utils.funcs import format_iso_week, parse_diesel_runtime_minutes, utcnow

if TYPE_CHECKING:
    from app.models import IncidentReport
    from app.models.report_data import DieselSiteHistory

# Imported lazily inside generate_incident_report_pdf to avoid circular import at module load
# from app.models import IncidentReport

# ── Incident report palette (module-level so all incident methods share them) ──
_INC_PRIMARY = "#7f1d1d"  # deep red — accent text and dividers
_INC_LIGHT_RED = "#fee2e2"  # light red — accent bands, heading highlight
_INC_LIGHT_BG = "#fef2f2"  # very pale red — sidebar strip
_INC_CHARCOAL = "#1a1a1a"  # near-black — massive cover title
_INC_WARM_GRAY = "#4a5568"  # warm gray — body text
_INC_LIGHT_GRAY = "#718096"  # light gray — running header, labels, captions
_INC_DIVIDER = "#e2e8f0"  # very light gray — thin separator lines
_INC_DARK_LABEL = "#2d3748"  # dark gray — metadata values

_FIELDCORE_BRAND = "FIELD CORE"
_FIELDCORE_REPORT_LABEL = "Field Report - FIELD CORE"
_FIELDCORE_CONFIDENTIAL = "CONFIDENTIAL - FOR FIELD CORE INTERNAL USE ONLY"
_FIELDCORE_MARK_ASSET = "fieldcore-logo-mark.png"
_FIELDCORE_LOCKUP_ASSET = "fieldcore-logo-lockup.png"

# ── Image fetch tuning (report photo evidence) ────────────────────────────────
_IMAGE_FETCH_TIMEOUT = 10  # seconds per attempt
_IMAGE_FETCH_RETRIES = 2  # attempts before giving up on a photo
_IMAGE_MAX_BYTES = 25 * 1024 * 1024  # 25 MB hard cap per image
_IMAGE_FETCH_WORKERS = 6  # concurrent photo downloads per grid
_IMAGE_MAX_DIM = 1600  # longest side (px) an embedded photo is downscaled to
_IMAGE_JPEG_QUALITY = 85  # re-encode quality for downscaled photos


class PDFService:
    """Service for generating PDF documents from reports."""

    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
        self.assets_path = Path(__file__).parent.parent / "assets"
        self.supabase_url = (app_settings.SUPABASE_URL or "").rstrip("/")
        self.supabase_service_key = app_settings.SUPABASE_SERVICE_KEY or ""
        self.supabase_bucket = app_settings.SUPABASE_STORAGE_BUCKET or "attachments"
        self._extra_image_hosts = {
            host.strip().lower()
            for host in (app_settings.PDF_IMAGE_ALLOWED_HOSTS or "").split(",")
            if host.strip()
        }
        # Per-PDF cache of downscaled image bytes, keyed by storage path (query
        # stripped) so signed-URL variants of the same file collapse to one
        # download+decode. A fresh PDFService is built per request, so this never
        # leaks across reports.
        self._image_cache: dict[str, bytes | None] = {}
        backend_root = Path(__file__).resolve().parents[2]
        workspace_root = backend_root.parent
        self.cover_search_paths = [
            self.assets_path / "Report" / "coverpages",
            self.assets_path / "Report Cover Pages",
            backend_root / "assets" / "Report" / "coverpages",
            backend_root / "assets" / "Report Cover Pages",
            workspace_root
            / "seacom-app-frontend"
            / "src"
            / "assets"
            / "Report Cover Pages",
        ]
        self.cover_file_map = {
            "base": "Base Cover.jpg",
            "diesel": "Diesel Generator Cover Page.jpg",
            "repeater": "Telecoms.jpg",
            "routine-drive": "RHS.jpg",
            "incident": "Incident.jpg",
            "executive": "Executive.jpg",
            "regional": "Regional.jpg",
            "technician": "Technicians.jpg",
            "client": "Seacom Client Report.jpg",
            "telecoms": "Telecoms.jpg",
            "rhs": "RHS.jpg",
            # No dedicated DC/POP cover art exists yet — reuse Telecoms and
            # swap the filenames when artwork is supplied.
            "datacenter": "Telecoms.jpg",
            "pop": "Telecoms.jpg",
        }
        self._first_page_bg_image: Path | None = None
        self._first_page_bg_primary: str = "#0b2265"
        self._first_page_bg_accent: str = "#1a365d"

    def _setup_custom_styles(self):
        """Setup custom paragraph styles for professional PDF design."""
        # Header style with centered alignment
        self.styles.add(
            ParagraphStyle(
                name="CompanyHeader",
                parent=self.styles["Normal"],
                fontSize=24,
                textColor=colors.HexColor("#0b2265"),
                spaceAfter=4,
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
            )
        )

        # Report title with centered alignment
        self.styles.add(
            ParagraphStyle(
                name="ReportTitle",
                parent=self.styles["Heading1"],
                fontSize=18,
                spaceAfter=8,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#1a365d"),
                fontName="Helvetica-Bold",
                spaceBefore=12,
            )
        )

        # Section header with centered alignment and rounded effect via styling
        self.styles.add(
            ParagraphStyle(
                name="SectionHeader",
                parent=self.styles["Heading2"],
                fontSize=12,
                spaceBefore=14,
                spaceAfter=10,
                textColor=colors.HexColor("#ffffff"),
                fontName="Helvetica-Bold",
                alignment=TA_CENTER,
                backColor=colors.HexColor("#1a365d"),
            )
        )

        # Field label
        self.styles.add(
            ParagraphStyle(
                name="FieldLabel",
                parent=self.styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#4a5568"),
                spaceAfter=2,
                fontName="Helvetica-Bold",
            )
        )

        # Field value
        self.styles.add(
            ParagraphStyle(
                name="FieldValue",
                parent=self.styles["Normal"],
                fontSize=10,
                spaceAfter=6,
                textColor=colors.HexColor("#2d3748"),
                fontName="Helvetica",
            )
        )

        # Footer style
        self.styles.add(
            ParagraphStyle(
                name="Footer",
                parent=self.styles["Normal"],
                fontSize=8,
                textColor=colors.HexColor("#718096"),
                alignment=TA_CENTER,
                spaceBefore=20,
            )
        )

    def _resolve_cover_image_path(self, cover_key: str | None) -> Path | None:
        """Resolve a cover image path using configured search paths with base fallback."""
        if cover_key is None:
            cover_key = "base"

        candidates = [cover_key]
        if cover_key != "base":
            candidates.append("base")

        for key in candidates:
            filename = self.cover_file_map.get(key)
            if not filename:
                continue
            for base_path in self.cover_search_paths:
                candidate = base_path / filename
                if candidate.exists():
                    return candidate
        return None

    def _cover_palette(self, cover_key: str | None) -> tuple[str, str]:
        """Return (primary, accent) colors by report style key."""
        palettes = {
            "base": ("#0b2265", "#1a365d"),
            "diesel": ("#6b3f00", "#9a6700"),
            "repeater": ("#0e7490", "#155e75"),
            "routine-drive": ("#5b21b6", "#6d28d9"),
            "incident": ("#7f1d1d", "#991b1b"),
            "executive": ("#0b2265", "#1a365d"),
            "regional": ("#1d4ed8", "#1e40af"),
            "technician": ("#166534", "#15803d"),
            "client": ("#0f172a", "#0b2265"),
            "telecoms": ("#155e75", "#0e7490"),
            "rhs": ("#4c1d95", "#5b21b6"),
            "datacenter": ("#0f766e", "#115e59"),
            "pop": ("#9a3412", "#7c2d12"),
        }
        return palettes.get(cover_key or "base", palettes["base"])

    def _load_brand_logo(
        self,
        filename: str,
        *,
        max_width_mm: float,
        max_height_mm: float,
    ) -> Image | None:
        """Load a logo and fit it inside requested bounds while preserving aspect ratio."""
        path = self.assets_path / filename
        if not path.exists():
            return None

        try:
            reader = ImageReader(str(path))
            src_width, src_height = reader.getSize()
            if not src_width or not src_height:
                return None

            scale = min(max_width_mm / src_width, max_height_mm / src_height)
            draw_width_mm = src_width * scale
            draw_height_mm = src_height * scale
            return Image(
                str(path),
                width=draw_width_mm * mm,
                height=draw_height_mm * mm,
            )
        except Exception:
            return None

    def _load_fieldcore_mark(
        self, *, max_width_mm: float, max_height_mm: float
    ) -> Image | None:
        return self._load_brand_logo(
            _FIELDCORE_MARK_ASSET,
            max_width_mm=max_width_mm,
            max_height_mm=max_height_mm,
        )

    def _load_fieldcore_lockup(
        self, *, max_width_mm: float, max_height_mm: float
    ) -> Image | None:
        return self._load_brand_logo(
            _FIELDCORE_LOCKUP_ASSET,
            max_width_mm=max_width_mm,
            max_height_mm=max_height_mm,
        )

    def _configure_first_page_background(
        self,
        cover_key: str | None,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """Configure first-page background image and color overlay palette."""
        self._first_page_bg_image = self._resolve_cover_image_path(cover_key)
        self._first_page_bg_primary = primary_hex
        self._first_page_bg_accent = accent_hex

    def _clear_first_page_background(self) -> None:
        """Reset first-page background configuration."""
        self._first_page_bg_image = None

    def _draw_first_page_background(
        self, canv: canvas.Canvas, doc: SimpleDocTemplate
    ) -> None:
        """Draw cover image + color overlay as a true page background."""
        bg_path = self._first_page_bg_image
        if bg_path is None:
            return

        page_w, page_h = doc.pagesize
        canv.saveState()
        try:
            # Scale to COVER the page (like CSS background-size:cover):
            # use the larger scale factor so the image fills both dimensions,
            # then center it (cropping any excess on the shorter axis).
            img_reader = ImageReader(str(bg_path))
            img_w, img_h = img_reader.getSize()
            scale = max(page_w / img_w, page_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            x = (page_w - draw_w) / 2
            y = (page_h - draw_h) / 2
            canv.drawImage(
                str(bg_path),
                x,
                y,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=False,
            )

            # Primary dark overlay for text legibility.
            if hasattr(canv, "setFillAlpha"):
                canv.setFillAlpha(0.50)
            canv.setFillColor(colors.HexColor(self._first_page_bg_primary))
            canv.rect(0, 0, page_w, page_h, stroke=0, fill=1)

            # Accent tint near the top for visual depth.
            if hasattr(canv, "setFillAlpha"):
                canv.setFillAlpha(0.22)
            canv.setFillColor(colors.HexColor(self._first_page_bg_accent))
            canv.rect(0, page_h * 0.45, page_w, page_h * 0.55, stroke=0, fill=1)
        finally:
            if hasattr(canv, "setFillAlpha"):
                canv.setFillAlpha(1)
            canv.restoreState()

    # ── Cover page builder ───────────────────────────────────────────────────

    def _build_cover_page(
        self,
        title: str,
        subtitle: str,
        details: list[list[str]],
        cover_key: str | None = None,
    ) -> list:
        """
        Build a professional full-cover first page. Returns a list of flowables
        ending with PageBreak() so main content starts on page 2.

        Args:
            title:    Large headline (e.g. "Incident Report")
            subtitle: Smaller descriptor below title (e.g. "Severity: CRITICAL - FIELD CORE")
            details:  List of [label, value] rows for the info table
        """
        elements = []
        primary_color, accent_color = self._cover_palette(cover_key)

        mark_logo = self._load_fieldcore_mark(max_width_mm=22, max_height_mm=20)
        lockup_logo = self._load_fieldcore_lockup(max_width_mm=52, max_height_mm=18)

        # ── Blue header band (logos + brand name) ────────────────────────────
        brand_style = ParagraphStyle(
            "CoverBrand",
            parent=self.styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#e2e8f0"),
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        )
        header_data = [
            [
                mark_logo or Paragraph("<b>FC</b>", self.styles["CompanyHeader"]),
                Paragraph(_FIELDCORE_BRAND, brand_style),
                lockup_logo
                or Paragraph("<b>FIELD CORE</b>", self.styles["CompanyHeader"]),
            ]
        ]
        header_table = Table(
            header_data, colWidths=[60 * mm, 50 * mm, 60 * mm], rowHeights=[32 * mm]
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(primary_color)),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.append(header_table)
        elements.append(Spacer(1, 22 * mm))

        # ── Large report title ───────────────────────────────────────────────
        cover_title_style = ParagraphStyle(
            "CoverTitle",
            parent=self.styles["Normal"],
            fontSize=28,
            textColor=colors.HexColor("#ffffff"),
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
            spaceAfter=6,
        )
        elements.append(Paragraph(title, cover_title_style))

        # ── Subtitle ─────────────────────────────────────────────────────────
        cover_sub_style = ParagraphStyle(
            "CoverSubtitle",
            parent=self.styles["Normal"],
            fontSize=13,
            textColor=colors.HexColor("#e2e8f0"),
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        )
        elements.append(Paragraph(subtitle, cover_sub_style))
        elements.append(Spacer(1, 8 * mm))

        # ── Thin navy divider ─────────────────────────────────────────────────
        elements.append(self._create_divider(color_hex=accent_color))
        elements.append(Spacer(1, 8 * mm))

        # ── Details table ────────────────────────────────────────────────────
        if details:
            det_table = Table(details, colWidths=[55 * mm, 115 * mm])
            det_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 10),
                        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#2d3748")),
                        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#4a5568")),
                        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                        ("ALIGN", (1, 0), (1, -1), "LEFT"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 0),
                            (-1, -1),
                            [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                        ),
                    ]
                )
            )
            elements.append(det_table)

        elements.append(Spacer(1, 18 * mm))

        # ── Confidentiality footer ────────────────────────────────────────────
        conf_style = ParagraphStyle(
            "CoverConf",
            parent=self.styles["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#e2e8f0"),
            alignment=TA_CENTER,
            fontName="Helvetica-Oblique",
        )
        elements.append(
            Paragraph(
                _FIELDCORE_CONFIDENTIAL,
                conf_style,
            )
        )
        elements.append(Spacer(1, 3 * mm))
        elements.append(
            Paragraph(
                f"Generated {datetime.now().strftime('%d %B %Y %H:%M')} UTC",
                conf_style,
            )
        )

        # ── Start main content on page 2 ─────────────────────────────────────
        elements.append(PageBreak())
        return elements

    # ── Field reports ────────────────────────────────────────────────────────

    def _build_brand_logo_row(
        self,
        *,
        mark_width_mm: float,
        mark_height_mm: float,
        lockup_width_mm: float,
        lockup_height_mm: float,
        gap_mm: float = 4,
        fallback_color: str = "#1f2a44",
    ) -> Table:
        """Build a shared Field Core logo row for report layouts.

        Field report pages previously rendered the shield mark and full lockup
        together, which looked like two company logos. Keep a single lockup.
        """
        fallback_s = ParagraphStyle(
            "BrandLogoFallback",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(fallback_color),
            alignment=TA_CENTER,
        )

        lockup_logo = self._load_fieldcore_lockup(
            max_width_mm=mark_width_mm + gap_mm + lockup_width_mm,
            max_height_mm=lockup_height_mm,
        )

        logos = Table(
            [
                [
                    lockup_logo or Paragraph("<b>FIELD CORE</b>", fallback_s),
                ]
            ],
            colWidths=[(mark_width_mm + gap_mm + lockup_width_mm) * mm],
        )
        logos.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return logos

    def _build_field_cover_page(
        self,
        *,
        report_type_label: str,
        title: str,
        site: str,
        subtitle: str,
        descriptor: str,
        detail_items: list[tuple[str, str]],
        generated_at: str,
        accent_hex: str,
    ) -> list:
        """Build the newer light field-report cover used by the latest exports."""
        accent = colors.HexColor(accent_hex)
        border = colors.HexColor("#d9e2ec")
        body_text = colors.HexColor("#5b6d88")
        title_color = colors.HexColor("#1b2540")

        kicker_s = ParagraphStyle(
            "FieldCoverKicker",
            parent=self.styles["Normal"],
            fontSize=9.5,
            fontName="Helvetica-Bold",
            textColor=accent,
            leading=11,
        )
        brand_s = ParagraphStyle(
            "FieldCoverBrand",
            parent=self.styles["Normal"],
            fontSize=16,
            fontName="Helvetica-Bold",
            textColor=accent,
            leading=18,
        )
        type_s = ParagraphStyle(
            "FieldCoverType",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=accent,
            leading=13,
        )
        title_s = ParagraphStyle(
            "FieldCoverTitle",
            parent=self.styles["Normal"],
            fontSize=31,
            fontName="Helvetica-Bold",
            textColor=title_color,
            leading=33,
        )
        site_s = ParagraphStyle(
            "FieldCoverSite",
            parent=self.styles["Normal"],
            fontSize=16,
            fontName="Helvetica-Bold",
            textColor=accent,
            leading=19,
        )
        subtitle_s = ParagraphStyle(
            "FieldCoverSubtitle",
            parent=self.styles["Normal"],
            fontSize=12.5,
            fontName="Helvetica",
            textColor=body_text,
            leading=15,
        )
        descriptor_s = ParagraphStyle(
            "FieldCoverDescriptor",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica",
            textColor=body_text,
            leading=16,
        )
        footer_s = ParagraphStyle(
            "FieldCoverFooter",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor("#70819b"),
            leading=10,
        )

        header = Table(
            [
                [
                    [
                        Paragraph("FIELD OPERATIONS REPORT", kicker_s),
                        Spacer(1, 1.5 * mm),
                        Paragraph(_FIELDCORE_BRAND, brand_s),
                    ],
                    self._build_brand_logo_row(
                        mark_width_mm=34,
                        mark_height_mm=15,
                        lockup_width_mm=40,
                        lockup_height_mm=16,
                        fallback_color="#1b2540",
                    ),
                ]
            ],
            colWidths=[97 * mm, 73 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, border),
                    ("LINEBELOW", (0, 0), (-1, -1), 2.2, accent),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )

        footer = Table(
            [
                [
                    Paragraph(
                        _FIELDCORE_CONFIDENTIAL,
                        footer_s,
                    ),
                    Paragraph(f"Generated {escape(generated_at)}", footer_s),
                ]
            ],
            colWidths=[120 * mm, 50 * mm],
        )
        footer.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 0.6, border),
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )

        elements: list = [
            header,
            Spacer(1, 18 * mm),
            Paragraph(escape(report_type_label.upper()), type_s),
            Spacer(1, 2 * mm),
            Paragraph(escape(title), title_s),
            Paragraph(escape(site or "N/A"), site_s),
            Spacer(1, 2 * mm),
            Paragraph(escape(subtitle), subtitle_s),
            Spacer(1, 4 * mm),
            Paragraph(escape(descriptor), descriptor_s),
            Spacer(1, 10 * mm),
            self._create_divider(color_hex=accent_hex),
            Spacer(1, 8 * mm),
        ]
        elements.extend(self._build_metadata_cards(detail_items))
        elements.extend(
            [
                Spacer(1, 18 * mm),
                footer,
                PageBreak(),
            ]
        )
        return elements

    def _build_field_page_header(
        self,
        *,
        title: str,
        subtitle: str,
        accent_hex: str,
    ) -> list:
        """Build the light header used on field-report content pages."""
        header = Table(
            [
                [
                    [
                        Paragraph(
                            escape(title),
                            ParagraphStyle(
                                "FieldPageHeaderTitle",
                                parent=self.styles["Normal"],
                                fontSize=18,
                                fontName="Helvetica-Bold",
                                textColor=colors.HexColor("#1b2540"),
                                leading=21,
                            ),
                        ),
                        Spacer(1, 1.5 * mm),
                        Paragraph(
                            escape(subtitle),
                            ParagraphStyle(
                                "FieldPageHeaderSubtitle",
                                parent=self.styles["Normal"],
                                fontSize=8.8,
                                fontName="Helvetica",
                                textColor=colors.HexColor("#64748b"),
                                leading=12,
                            ),
                        ),
                    ],
                    self._build_brand_logo_row(
                        mark_width_mm=26,
                        mark_height_mm=11,
                        lockup_width_mm=33,
                        lockup_height_mm=13,
                        fallback_color="#1b2540",
                    ),
                ]
            ],
            colWidths=[102 * mm, 68 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e2ec")),
                    ("LINEBELOW", (0, 0), (-1, -1), 2.2, colors.HexColor(accent_hex)),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )
        return [header, Spacer(1, 4 * mm)]

    def _build_field_overview_cards(
        self,
        items: list[tuple[str, str]],
        *,
        accent_hex: str,
    ) -> list:
        """Build the four-up overview cards on field report page two."""
        if not items:
            return []

        label_s = ParagraphStyle(
            "FieldOverviewLabel",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#6b7b94"),
            leading=9,
        )
        value_s = ParagraphStyle(
            "FieldOverviewValue",
            parent=self.styles["Normal"],
            fontSize=15,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1b2540"),
            leading=18,
        )

        data = [
            [Paragraph(escape(label.upper()), label_s) for label, _ in items],
            [Paragraph(escape(value or "N/A"), value_s) for _, value in items],
        ]
        widths = [(170 * mm) / len(items)] * len(items)
        table = Table(data, colWidths=widths)
        table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 1.8, colors.HexColor(accent_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e2ec")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                    ("TOPPADDING", (0, 1), (-1, 1), 4),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
                ]
            )
        )
        return [table, Spacer(1, 5 * mm)]

    def _build_field_metric_cards(
        self,
        items: list[tuple[str, str]],
        *,
        accent_hex: str,
    ) -> Table:
        """Build the compact KPI cards used in the diesel summary block."""
        label_s = ParagraphStyle(
            "FieldMetricLabel",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#6b7b94"),
            leading=9,
        )
        value_s = ParagraphStyle(
            "FieldMetricValue",
            parent=self.styles["Normal"],
            fontSize=14.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1b2540"),
            leading=17,
        )

        widths = [(170 * mm) / len(items)] * len(items)
        table = Table(
            [
                [Paragraph(escape(label.upper()), label_s) for label, _ in items],
                [Paragraph(escape(value or "N/A"), value_s) for _, value in items],
            ],
            colWidths=widths,
        )
        table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 1.8, colors.HexColor(accent_hex)),
                    ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e2ec")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                    ("TOPPADDING", (0, 1), (-1, 1), 4),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
                ]
            )
        )
        return table

    def generate_report_pdf_legacy(self, report: Report) -> BytesIO:
        """
        Generate a professional PDF document for a completed report with logos and rounded design elements.

        Args:
            report: The Report model instance to generate PDF for

        Returns:
            BytesIO buffer containing the PDF document
        """
        buffer = BytesIO()

        try:
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=20 * mm,
                leftMargin=20 * mm,
                topMargin=20 * mm,
                bottomMargin=20 * mm,
                title=f"Report_{report.report_type.value}_{report.id}",
            )

            story = []

            # ── Cover page ────────────────────────────────────────────────────
            report_type_display = self._format_report_type(report.report_type)
            cover_details: list[list[str]] = [
                ["Report Type", report_type_display],
                ["Status", report.status.value.upper()],
                ["Service Provider", report.service_provider or "N/A"],
            ]
            try:
                if report.technician and report.technician.user:
                    u = report.technician.user
                    cover_details.append(["Technician", f"{u.name} {u.surname}"])
                    cover_details.append(["Phone", report.technician.phone or "N/A"])
            except Exception:
                pass
            try:
                if report.task:
                    if report.task.seacom_ref:
                        cover_details.append(["Reference", report.task.seacom_ref])
                    if report.task.site:
                        cover_details.append(["Site", report.task.site.name])
                        cover_details.append(
                            [
                                "Region",
                                report.task.site.region.value.replace("-", " ").title(),
                            ]
                        )
            except Exception:
                pass
            cover_details.append(
                ["Generated", self._format_datetime(report.created_at)]
            )
            report_cover_key = (
                report.report_type.value
                if getattr(report, "report_type", None)
                else "base"
            )
            primary_hex, accent_hex = self._cover_palette(report_cover_key)
            story.extend(
                self._build_cover_page(
                    title=f"{report_type_display} Report",
                    subtitle=_FIELDCORE_REPORT_LABEL,
                    details=cover_details,
                    cover_key=report_cover_key,
                )
            )

            # ── Page 2: banner header ─────────────────────────────────────────
            story.extend(
                self._build_page_header(
                    title=f"{report_type_display} Report",
                    subtitle=f"{_FIELDCORE_REPORT_LABEL}  |  {self._format_datetime(report.created_at)}",
                    primary_hex=primary_hex,
                    accent_hex=accent_hex,
                )
            )

            # ── Metadata cards ────────────────────────────────────────────────
            meta_items: list[tuple[str, str]] = [
                ("Report Type", report_type_display),
                ("Status", report.status.value.upper()),
                ("Service Provider", report.service_provider or "N/A"),
                ("Created", self._format_datetime(report.created_at)),
            ]
            try:
                if report.technician and report.technician.user:
                    u = report.technician.user
                    meta_items.append(("Technician", f"{u.name} {u.surname}"))
                    meta_items.append(("Phone", report.technician.phone or "N/A"))
            except Exception:
                pass
            try:
                if report.task:
                    if report.task.seacom_ref:
                        meta_items.append(("Reference", report.task.seacom_ref))
                    if report.task.site:
                        meta_items.append(("Site", report.task.site.name))
                        meta_items.append(
                            (
                                "Region",
                                report.task.site.region.value.replace("-", " ").title(),
                            )
                        )
            except Exception:
                pass
            story.extend(self._build_metadata_cards(meta_items, primary_hex))

            # Report Data Section
            if report.data:
                if report.report_type == ReportType.REPEATER:
                    self._render_repeater_body(report, story, primary_hex, accent_hex)
                elif report.report_type == ReportType.DIESEL:
                    self._render_diesel_body(report, story, primary_hex, accent_hex)
                elif report.report_type == ReportType.ROUTINE_DRIVE:
                    self._render_route_patrol_body(
                        report, story, primary_hex, accent_hex
                    )
                else:
                    story.extend(
                        self._repeater_section_header(
                            "Report Details", primary_hex, accent_hex
                        )
                    )
                    story.extend(self._render_report_data(report.data))

            # Attachments Section
            # ROUTINE_DRIVE and REPEATER render photos in their own body
            # section; DIESEL renders every uploaded photo in its "9. Report
            # Pictures" section (attachments.files are merged there).
            # Rendering attachments again here duplicated every photo, so
            # skip all three report types.
            if report.attachments and report.report_type not in (
                ReportType.ROUTINE_DRIVE,
                ReportType.DIESEL,
                ReportType.REPEATER,
            ):
                story.append(Spacer(1, 16))
                story.extend(
                    self._repeater_section_header(
                        "Attachments", primary_hex, accent_hex
                    )
                )

                attachment_data = [["Field Name", "Value"]]
                for key, value in report.attachments.items():
                    attachment_data.append([key, self._format_attachment_value(value)])

                if len(attachment_data) > 1:
                    att_table = Table(attachment_data, colWidths=[140, 330])
                    att_table.setStyle(
                        TableStyle(
                            [
                                (
                                    "BACKGROUND",
                                    (0, 0),
                                    (-1, 0),
                                    colors.HexColor("#1a365d"),
                                ),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                                ("FONTNAME", (1, 1), (-1, -1), "Helvetica"),
                                ("FONTSIZE", (0, 0), (-1, -1), 9),
                                (
                                    "TEXTCOLOR",
                                    (0, 0),
                                    (-1, 0),
                                    colors.HexColor("#ffffff"),
                                ),
                                (
                                    "TEXTCOLOR",
                                    (1, 1),
                                    (-1, -1),
                                    colors.HexColor("#4a5568"),
                                ),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                (
                                    "GRID",
                                    (0, 0),
                                    (-1, -1),
                                    1,
                                    colors.HexColor("#cbd5e0"),
                                ),
                                (
                                    "ROWBACKGROUNDS",
                                    (0, 1),
                                    (-1, -1),
                                    [
                                        colors.HexColor("#ffffff"),
                                        colors.HexColor("#f7fafc"),
                                    ],
                                ),
                            ]
                        )
                    )
                    story.append(att_table)

            # Footer
            story.append(Spacer(1, 24))
            story.append(self._create_divider())
            story.append(Spacer(1, 8))
            story.append(
                Paragraph(
                    f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC | "
                    f"Report ID: {str(report.id)[:8]}",
                    self.styles["Footer"],
                )
            )

            # Build PDF
            self._configure_first_page_background(
                report_cover_key, primary_hex, accent_hex
            )
            try:
                doc.build(story, onFirstPage=self._draw_first_page_background)
            finally:
                self._clear_first_page_background()

        except Exception:
            raise

        buffer.seek(0)
        return buffer

    # ── Incident reports ─────────────────────────────────────────────────────

    def generate_report_pdf(self, report: Report) -> BytesIO:
        """Generate a field report PDF using the latest light-weight branded layout."""
        buffer = BytesIO()

        try:
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=20 * mm,
                leftMargin=20 * mm,
                topMargin=20 * mm,
                bottomMargin=20 * mm,
                title=f"Report_{report.report_type.value}_{report.id}",
            )

            story: list = []
            report_type_display = self._format_report_type(report.report_type)
            status_display = report.status.value.upper()
            service_provider_display = report.service_provider or "N/A"
            technician_name = "N/A"
            phone_display = "N/A"
            reference_display = "N/A"
            site_display = "N/A"
            region_display = "N/A"

            try:
                if report.technician and report.technician.user:
                    user = report.technician.user
                    technician_name = f"{user.name} {user.surname}"
                    phone_display = report.technician.phone or "N/A"
            except Exception:
                pass

            try:
                if report.task:
                    if report.task.seacom_ref:
                        reference_display = report.task.seacom_ref
                    if report.task.site:
                        site_display = report.task.site.name
                        region_display = report.task.site.region.value.replace(
                            "-", " "
                        ).title()
            except Exception:
                pass

            created_display = self._format_datetime(report.created_at)
            report_cover_key = (
                report.report_type.value
                if getattr(report, "report_type", None)
                else "base"
            )
            primary_hex, accent_hex = self._cover_palette(report_cover_key)

            story.extend(
                self._build_field_cover_page(
                    report_type_label=report_type_display,
                    title=f"{report_type_display} Report",
                    site=site_display,
                    subtitle=_FIELDCORE_REPORT_LABEL,
                    descriptor=(
                        f"Prepared for {service_provider_display}. Structured field document "
                        "for operational review, archive, and audit traceability."
                    ),
                    detail_items=[
                        ("Report Type", report_type_display),
                        ("Status", status_display),
                        ("Service Provider", service_provider_display),
                        ("Technician", technician_name),
                        ("Phone", phone_display),
                        ("Reference", reference_display),
                        ("Site", site_display),
                        ("Region", region_display),
                        ("Generated", created_display),
                    ],
                    generated_at=created_display,
                    accent_hex=accent_hex,
                )
            )

            story.extend(
                self._build_field_page_header(
                    title=f"{report_type_display} Report",
                    subtitle=f"{_FIELDCORE_REPORT_LABEL} | {created_display}",
                    accent_hex=accent_hex,
                )
            )
            story.extend(
                self._build_field_overview_cards(
                    [
                        ("Status", status_display),
                        ("Service Provider", service_provider_display),
                        ("Technician", technician_name),
                        ("Site", site_display),
                    ],
                    accent_hex=accent_hex,
                )
            )
            story.append(
                self._build_field_kv_table(
                    [
                        ("Report Type", report_type_display),
                        ("Created", created_display),
                        ("Phone", phone_display),
                        ("Reference", reference_display),
                        ("Region", region_display),
                    ]
                )
            )
            story.append(Spacer(1, 6 * mm))

            if report.data:
                if report.report_type == ReportType.REPEATER:
                    self._render_repeater_body(report, story, primary_hex, accent_hex)
                elif report.report_type == ReportType.DIESEL:
                    self._render_diesel_body(report, story, primary_hex, accent_hex)
                elif report.report_type == ReportType.ROUTINE_DRIVE:
                    self._render_route_patrol_body(
                        report, story, primary_hex, accent_hex
                    )
                elif report.report_type in (ReportType.DATACENTER, ReportType.POP):
                    self._render_hosted_site_body(report, story, primary_hex, accent_hex)
                else:
                    story.extend(
                        self._repeater_section_header(
                            "Report Details", primary_hex, accent_hex
                        )
                    )
                    story.extend(self._render_report_data(report.data))

            # ROUTINE_DRIVE renders photos in its body; DIESEL renders every
            # uploaded photo in its "9. Report Pictures" section (attachments.files
            # are merged there); DATACENTER/POP read photos from data.cabinets[]/
            # .extra_sections[], never attachments.files. Rendering attachments
            # again here would duplicate every photo, so skip all four types.
            if report.attachments and report.report_type not in (
                ReportType.ROUTINE_DRIVE,
                ReportType.DATACENTER,
                ReportType.POP,
                ReportType.DIESEL,
                ReportType.REPEATER,
            ):
                story.append(Spacer(1, 16))
                story.extend(
                    self._repeater_section_header(
                        "Attachments", primary_hex, accent_hex
                    )
                )

                attachment_data = [["Field Name", "Value"]]
                for key, value in report.attachments.items():
                    attachment_data.append([key, self._format_attachment_value(value)])

                if len(attachment_data) > 1:
                    att_table = Table(attachment_data, colWidths=[140, 330])
                    att_table.setStyle(
                        TableStyle(
                            [
                                (
                                    "BACKGROUND",
                                    (0, 0),
                                    (-1, 0),
                                    colors.HexColor("#1a365d"),
                                ),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                                ("FONTNAME", (1, 1), (-1, -1), "Helvetica"),
                                ("FONTSIZE", (0, 0), (-1, -1), 9),
                                (
                                    "TEXTCOLOR",
                                    (0, 0),
                                    (-1, 0),
                                    colors.HexColor("#ffffff"),
                                ),
                                (
                                    "TEXTCOLOR",
                                    (1, 1),
                                    (-1, -1),
                                    colors.HexColor("#4a5568"),
                                ),
                                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                (
                                    "GRID",
                                    (0, 0),
                                    (-1, -1),
                                    1,
                                    colors.HexColor("#cbd5e0"),
                                ),
                                (
                                    "ROWBACKGROUNDS",
                                    (0, 1),
                                    (-1, -1),
                                    [
                                        colors.HexColor("#ffffff"),
                                        colors.HexColor("#f7fafc"),
                                    ],
                                ),
                            ]
                        )
                    )
                    story.append(att_table)

            story.append(Spacer(1, 24))
            story.append(self._create_divider())
            story.append(Spacer(1, 8))
            story.append(
                Paragraph(
                    f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC | "
                    f"Report ID: {str(report.id)[:8]}",
                    self.styles["Footer"],
                )
            )

            doc.build(story)

        except Exception:
            raise

        buffer.seek(0)
        return buffer

    def _extract_supabase_file_path(self, url_or_path: str) -> str | None:
        """Extract storage file path from a Supabase URL or return raw path."""
        candidate = (url_or_path or "").strip()
        if not candidate:
            return None

        # Already a raw file path (e.g. reports/<id>/site-pictures/<file>.jpg)
        if "://" not in candidate:
            return candidate.lstrip("/")

        try:
            parsed = urlparse(candidate)
        except Exception:
            return None

        if parsed.scheme not in {"http", "https"}:
            return None

        if self.supabase_url:
            configured_host = urlparse(self.supabase_url).netloc
            if configured_host and parsed.netloc != configured_host:
                return None

        path = unquote(parsed.path or "")
        public_prefix = f"/storage/v1/object/public/{self.supabase_bucket}/"
        sign_prefix = f"/storage/v1/object/sign/{self.supabase_bucket}/"
        auth_prefix = f"/storage/v1/object/authenticated/{self.supabase_bucket}/"

        for prefix in (public_prefix, sign_prefix, auth_prefix):
            if path.startswith(prefix):
                return path[len(prefix) :]

        return None

    def _fetch_supabase_image_bytes(self, file_path: str) -> BytesIO | None:
        """Download an image from Supabase authenticated endpoint with service key."""
        if not (self.supabase_url and self.supabase_service_key and file_path):
            return None

        auth_url = f"{self.supabase_url}/storage/v1/object/authenticated/{self.supabase_bucket}/{file_path.lstrip('/')}"
        if not self._is_safe_remote_url(auth_url):
            return None
        return self._http_get_image(
            auth_url,
            {
                "User-Agent": "Mozilla/5.0",
                "Authorization": f"Bearer {self.supabase_service_key}",
                "apikey": self.supabase_service_key,
            },
        )

    def _validate_image_bytes(
        self, data: bytes, expected_len: int | None
    ) -> BytesIO | None:
        """Validate, orient, and downscale a photo before it reaches ReportLab.

        Two jobs in one PIL pass:

        1. Reject truncated/corrupt payloads (a short transfer vs Content-Length,
           or bytes PIL cannot decode) so a half-rendered image is never embedded.
        2. Downscale to at most ``_IMAGE_MAX_DIM`` on the longest side and re-encode
           as JPEG. ReportLab embeds the *source* pixels at full resolution — a 12 MP
           phone photo shown in a 55 mm cell still costs ~35 MB of decoded RAM and
           bloats the PDF. Downscaling here bounds both memory and file size; the
           JPEG ``draft`` hint lets the decoder scale down *while* decoding so a
           large source is never fully expanded in memory. EXIF orientation is
           baked in so rotated phone photos render upright.

        Returns a small JPEG buffer, or None on failure (caller retries / shows a
        placeholder).
        """
        if not data:
            return None
        if expected_len is not None and len(data) < expected_len:
            LOG.debug(
                "pdf_image_truncated got={} expected={}", len(data), expected_len
            )
            return None
        try:
            with PILImage.open(BytesIO(data)) as im:
                # draft() only affects JPEG, but that's the phone-photo case — it
                # asks the decoder for a pre-scaled image, avoiding a full decode.
                im.draft("RGB", (_IMAGE_MAX_DIM, _IMAGE_MAX_DIM))
                im = ImageOps.exif_transpose(im)  # honour camera rotation
                if im.mode != "RGB":
                    im = im.convert("RGB")
                im.thumbnail(
                    (_IMAGE_MAX_DIM, _IMAGE_MAX_DIM), PILImage.Resampling.LANCZOS
                )
                out = BytesIO()
                im.save(out, format="JPEG", quality=_IMAGE_JPEG_QUALITY, optimize=True)
            out.seek(0)
            return out
        except Exception as exc:
            LOG.debug("pdf_image_decode_failed err={}", repr(exc))
            return None

    def _http_get_image(self, url: str, headers: dict) -> BytesIO | None:
        """Fetch an image with retries, a size cap, and integrity validation."""
        last_err: str | None = None
        for attempt in range(1, _IMAGE_FETCH_RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_IMAGE_FETCH_TIMEOUT) as resp:
                    cl = resp.headers.get("Content-Length")
                    expected = int(cl) if cl and cl.isdigit() else None
                    if expected is not None and expected > _IMAGE_MAX_BYTES:
                        LOG.debug(
                            "pdf_image_too_large url={} size={}", url[:120], expected
                        )
                        return None
                    # Read one byte past the cap so we can detect an overflow.
                    data = resp.read(_IMAGE_MAX_BYTES + 1)
                if len(data) > _IMAGE_MAX_BYTES:
                    LOG.debug("pdf_image_exceeds_cap url={}", url[:120])
                    return None
                buf = self._validate_image_bytes(data, expected)
                if buf is not None:
                    return buf
                last_err = "validation_failed"
            except urllib.error.HTTPError as exc:
                # 4xx is permanent (missing/forbidden) — retrying just wastes time.
                last_err = f"HTTP {exc.code}"
                if 400 <= exc.code < 500:
                    LOG.debug(
                        "pdf_image_http_permanent url={} status={}", url[:120], exc.code
                    )
                    return None
            except Exception as exc:
                last_err = repr(exc)
            LOG.debug(
                "pdf_image_fetch_attempt_failed attempt={}/{} url={} err={}",
                attempt,
                _IMAGE_FETCH_RETRIES,
                url[:120],
                last_err,
            )
        return None

    def _fit_photo_image(
        self, buf: BytesIO, box_w: float, box_h: float
    ) -> Image | None:
        """Build a ReportLab Image scaled to fit box_w x box_h, preserving aspect.

        box_w/box_h are in points (mm values are already multiplied by `mm`).
        Portrait or off-ratio photos are letterboxed within the cell rather than
        stretched into a fixed landscape frame — the whole photo stays visible
        and undistorted.
        """
        try:
            buf.seek(0)
            reader = ImageReader(buf)
            iw, ih = reader.getSize()
            if not iw or not ih:
                return None
            scale = min(box_w / iw, box_h / ih)
            buf.seek(0)
            img = Image(buf, width=iw * scale, height=ih * scale)
            img.hAlign = "CENTER"
            return img
        except Exception:
            return None

    def _is_safe_remote_url(self, url: str) -> bool:
        """Guard the direct image fetch against SSRF (M5).

        Only http(s) is allowed; when Supabase is configured the host must match
        it; and every resolved IP must be a public address (blocks loopback,
        private, link-local, reserved and multicast targets).
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        if parsed.scheme not in {"http", "https"}:
            return False

        host = parsed.hostname
        if not host:
            return False

        if self.supabase_url:
            allowed_host = urlparse(self.supabase_url).hostname
            allowed = {allowed_host.lower()} if allowed_host else set()
            allowed |= self._extra_image_hosts
            if allowed and host.lower() not in allowed:
                return False

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addr_infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except Exception:
            return False

        for info in addr_infos:
            ip_str = info[4][0]
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                return False
            if (
                not addr.is_global
                or addr.is_loopback
                or addr.is_private
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
            ):
                return False

        return True

    def _fetch_image_bytes(self, url: str) -> BytesIO | None:
        """Download an image and support Supabase private storage fallbacks."""
        if not url:
            return None

        # 1) Direct URL fetch first (works for signed/public links and standard
        #    URLs) — only if the URL passes the SSRF allowlist/IP check.
        if self._is_safe_remote_url(url):
            buf = self._http_get_image(url, {"User-Agent": "Mozilla/5.0"})
            if buf is not None:
                return buf

        # 2) Supabase private bucket fallback using service key.
        file_path = self._extract_supabase_file_path(url)
        if file_path:
            fallback = self._fetch_supabase_image_bytes(file_path)
            if fallback:
                return fallback

        LOG.debug("pdf_image_fetch_failed source={}", url[:200])
        return None

    def _image_cache_key(self, url: str) -> str:
        """Stable per-file key: prefer the Supabase storage path, else the URL
        without its query string, so signed-URL variants of one file share a key.
        """
        file_path = self._extract_supabase_file_path(url)
        if file_path:
            return f"sb:{file_path}"
        try:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        except Exception:
            return url

    def _fetch_one_for_cache(self, key: str, url: str) -> bytes | None:
        """Fetch+downscale a single distinct file, logging size and elapsed."""
        started = time.monotonic()
        buf = self._fetch_image_bytes(url)
        elapsed = time.monotonic() - started
        data = buf.getvalue() if buf is not None else None
        if data is not None:
            LOG.info(
                "pdf_photo_ok {:.0f}KB {:.1f}s key={}", len(data) / 1024, elapsed, key
            )
        else:
            LOG.warning("pdf_photo_failed {:.1f}s url={}", elapsed, url[:80])
        return data

    def _fetch_images_parallel(self, urls: list[str]) -> list[BytesIO | None]:
        """Fetch photos concurrently, de-duplicated and cached, preserving order.

        The same underlying file often appears many times (signed URLs differ only
        by their token), and a report can render several photo grids. We collapse
        every occurrence to one download+decode via a per-PDF cache, so RAM and
        time scale with the number of *distinct* files, not raw occurrences.
        Each caller still gets its own BytesIO (ReportLab consumes the buffer, so
        occurrences must not share one). Progress is logged at INFO.
        """
        if not urls:
            return []
        total = len(urls)
        keys = [self._image_cache_key(u) for u in urls]

        # Distinct files not already cached, first URL seen per key.
        to_fetch: dict[str, str] = {}
        for url, key in zip(urls, keys):
            if key not in self._image_cache and key not in to_fetch:
                to_fetch[key] = url

        cached_hits = sum(1 for k in keys if k in self._image_cache)
        LOG.info(
            "pdf_photos_fetch_start occurrences={} distinct_new={} already_cached={} dup_in_batch={}",
            total,
            len(to_fetch),
            cached_hits,
            total - cached_hits - len(to_fetch),
        )
        started = time.monotonic()

        if to_fetch:
            workers = min(_IMAGE_FETCH_WORKERS, len(to_fetch))
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_to_key = {
                    pool.submit(self._fetch_one_for_cache, key, url): key
                    for key, url in to_fetch.items()
                }
                for future in as_completed(future_to_key):
                    key = future_to_key[future]
                    self._image_cache[key] = future.result()
                    done += 1
                    LOG.info(
                        "pdf_photos_progress {}/{} distinct complete",
                        done,
                        len(to_fetch),
                    )

        # Hand back a fresh buffer per occurrence (never a shared BytesIO).
        results: list[BytesIO | None] = []
        for key in keys:
            data = self._image_cache.get(key)
            results.append(BytesIO(data) if data else None)

        elapsed = time.monotonic() - started
        ok = sum(1 for r in results if r is not None)
        LOG.info(
            "pdf_photos_fetch_done ok={} failed={} occurrences={} distinct_new={} elapsed={:.1f}s",
            ok,
            total - ok,
            total,
            len(to_fetch),
            elapsed,
        )
        return results

    def _build_narrative_section(
        self,
        number: int,
        label: str,
        body: str | None,
        primary_hex: str = "#7f1d1d",
        accent_hex: str = "#991b1b",
    ) -> list:
        """Build an incident narrative section using the client-overview card styling."""
        elements = []

        title_style = ParagraphStyle(
            f"IncSecTitle{number}",
            parent=self.styles["Normal"],
            fontSize=12,
            fontName="Times-Bold",
            textColor=colors.HexColor(primary_hex),
            leading=15,
        )
        body_style = ParagraphStyle(
            f"IncSecBody{number}",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
            leading=16,
        )

        section_title = Paragraph(f"{number}. {label}", title_style)
        section_divider = Table([[""]], colWidths=[170 * mm])
        section_divider.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, -1), 1.4, colors.HexColor(accent_hex)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(section_title)
        elements.append(section_divider)
        elements.append(Spacer(1, 2 * mm))

        safe_body = (body or "").strip() or "<i>Not provided.</i>"
        body_para = Paragraph(safe_body, body_style)
        body_table = Table([[body_para]], colWidths=[170 * mm])
        body_table.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (0, 0), 10),
                    ("RIGHTPADDING", (0, 0), (0, 0), 10),
                    ("TOPPADDING", (0, 0), (0, 0), 9),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 9),
                    ("BACKGROUND", (0, 0), (0, 0), colors.white),
                    ("BOX", (0, 0), (0, 0), 0.7, colors.HexColor("#e4ebf9")),
                ]
            )
        )
        elements.append(body_table)
        elements.append(Spacer(1, 5 * mm))
        return elements

    # ── Legacy incident cover kept only as historical reference ───────────────

    def _build_incident_cover_page_legacy(
        self,
        seacom_ref: str,
        site: str,
        technician: str,
        severity: str,
        report_date: str,
        report_date_obj: "datetime | None" = None,
        generated_at: datetime | None = None,
    ) -> list:
        """
        Build a dark-background-compatible cover page matching the
        Field Core incident style: logos + pill badge top bar, large white
        title, frosted info boxes at the bottom.
        """
        elements = []

        # ── Local styles (all white text — canvas dark background shows through) ─
        wh = "#ffffff"
        dim_wh = "#dbe7f5"
        teal_lbl = "#63b3ed"  # label text in info boxes
        box_fill = "#dce8f5"  # frosted info box fill (light on dark bg)
        box2_fill = "#1e3a5f"  # darker confidentiality box
        chip_fill = "#24486f"
        ref_fill = "#f3f7fb"
        ref_text = "#102940"
        badge_bdr = "#93c5fd"  # pill badge outline

        fb_s = ParagraphStyle(
            "IncCovFb_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
        )
        fb_r_s = ParagraphStyle(
            "IncCovFbR_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
            alignment=TA_RIGHT,
        )
        badge_s = ParagraphStyle(
            "IncCovBadge_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
            alignment=TA_CENTER,
        )
        brand_s = ParagraphStyle(
            "IncCovBrand_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor(wh),
            alignment=TA_LEFT,
        )
        kicker_s = ParagraphStyle(
            "IncCovKicker_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#9ec5ea"),
            alignment=TA_LEFT,
        )
        title1_s = ParagraphStyle(
            "IncCovTitle1_local",
            parent=self.styles["Normal"],
            fontSize=20,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
            leading=24,
            alignment=TA_LEFT,
        )
        title2_s = ParagraphStyle(
            "IncCovTitle2_local",
            parent=self.styles["Normal"],
            fontSize=42,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
            leading=46,
            alignment=TA_LEFT,
        )
        subtitle_s = ParagraphStyle(
            "IncCovSubtitle_local",
            parent=self.styles["Normal"],
            fontSize=14,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
            alignment=TA_LEFT,
        )
        descriptor_s = ParagraphStyle(
            "IncCovDescriptor_local",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            leading=15,
            textColor=colors.HexColor(dim_wh),
            alignment=TA_LEFT,
        )
        lbl_s = ParagraphStyle(
            "IncCovInfoLbl_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(teal_lbl),
        )
        val_s = ParagraphStyle(
            "IncCovInfoVal_local",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
        )
        chip_s = ParagraphStyle(
            "IncCovChip_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(wh),
            alignment=TA_CENTER,
        )
        ref_label_s = ParagraphStyle(
            "IncCovRefLabel_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#526477"),
        )
        ref_value_s = ParagraphStyle(
            "IncCovRefValue_local",
            parent=self.styles["Normal"],
            fontSize=18,
            fontName="Helvetica-Bold",
            leading=22,
            textColor=colors.HexColor(ref_text),
        )
        conf_s = ParagraphStyle(
            "IncCovConf2_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor("#cbd5e0"),
            alignment=TA_LEFT,
        )

        # ── 1. Top breathing room ─────────────────────────────────────────────
        elements.append(Spacer(1, 6 * mm))

        # ── 2. Top bar: Field Core mark | "INCIDENT REPORT" pill | Field Core lockup ─────
        mark_logo = self._load_fieldcore_mark(max_width_mm=40, max_height_mm=15)
        lockup_logo = self._load_fieldcore_lockup(max_width_mm=45, max_height_mm=16)

        # Pill badge (nested table with white border)
        pill_inner = Table(
            [[Paragraph("INCIDENT  REPORT", badge_s)]],
            colWidths=[70 * mm],
        )
        pill_inner.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(badge_bdr)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )

        top_bar = Table(
            [
                [
                    mark_logo or Paragraph("<b>FC</b>", fb_s),
                    pill_inner,
                    lockup_logo or Paragraph("<b>FIELD CORE</b>", fb_r_s),
                ]
            ],
            colWidths=[50 * mm, 70 * mm, 50 * mm],
        )
        top_bar.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(top_bar)

        # ── 3. Vertical space before title ───────────────────────────────────
        elements.append(Spacer(1, 18 * mm))

        # ── 4. Brand line ─────────────────────────────────────────────────────
        elements.append(Paragraph("FIELD OPERATIONS DOSSIER", kicker_s))
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(_FIELDCORE_BRAND, brand_s))
        elements.append(Spacer(1, 5 * mm))

        # ── 5. Large title ────────────────────────────────────────────────────
        elements.append(Paragraph("Post-Resolution", title1_s))
        elements.append(Paragraph("Incident Report", title2_s))
        elements.append(Spacer(1, 4 * mm))

        # ── 6. Subtitle: severity | site ─────────────────────────────────────
        elements.append(Paragraph(f"{severity}  |  {site}", subtitle_s))
        elements.append(Spacer(1, 3 * mm))
        elements.append(
            Paragraph(
                "Operational close-out summary covering incident context, engineering response, "
                "service restoration, and formal technician sign-off.",
                descriptor_s,
            )
        )
        elements.append(Spacer(1, 7 * mm))

        # ── 7. Frosted info box ────────────────────────────────────────────────
        generated_ts = (generated_at or utcnow()).strftime("%d %B %Y  %H:%M UTC")
        severity_key = (severity or "UNSPECIFIED").strip().upper()
        severity_fill = {
            "CRITICAL": "#b42318",
            "MAJOR": "#c2410c",
            "MEDIUM": "#1d4ed8",
            "MINOR": "#0f766e",
        }.get(severity_key, chip_fill)
        severity_chip = Table(
            [[Paragraph(f"SEVERITY  {severity_key}", chip_s)]],
            colWidths=[42 * mm],
        )
        severity_chip.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(severity_fill)),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(severity_fill)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        site_chip = Table(
            [[Paragraph(f"SITE  {site}", chip_s)]],
            colWidths=[58 * mm],
        )
        site_chip.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(chip_fill)),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(badge_bdr)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        hero_meta = Table([[severity_chip, site_chip]], colWidths=[46 * mm, 62 * mm])
        hero_meta.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(hero_meta)
        elements.append(Spacer(1, 11 * mm))

        ref_band = Table(
            [
                [
                    Paragraph("INCIDENT REFERENCE", ref_label_s),
                    Paragraph(seacom_ref, ref_value_s),
                ]
            ],
            colWidths=[44 * mm, 122 * mm],
        )
        ref_band.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(ref_fill)),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#c7d8ea")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )
        elements.append(ref_band)
        elements.append(Spacer(1, 9 * mm))
        info_data = [
            [
                [Paragraph("SITE", lbl_s), Paragraph(site, val_s)],
                [Paragraph("TECHNICIAN", lbl_s), Paragraph(technician, val_s)],
            ],
            [
                [Paragraph("SEVERITY", lbl_s), Paragraph(severity, val_s)],
                [Paragraph("REPORT DATE", lbl_s), Paragraph(report_date, val_s)],
            ],
            [
                [Paragraph("GENERATED", lbl_s), Paragraph(generated_ts, val_s)],
                [
                    Paragraph("DOCUMENT CLASS", lbl_s),
                    Paragraph("Internal Controlled Copy", val_s),
                ],
            ],
        ]
        info_box = Table(info_data, colWidths=[85 * mm, 85 * mm])
        info_box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(box_fill)),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(badge_bdr)),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8d4f0")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(info_box)
        elements.append(Spacer(1, 3 * mm))

        # ── 8. Confidentiality footer box ─────────────────────────────────────
        conf_box = Table(
            [
                [
                    Paragraph(
                        f"Ref: {seacom_ref}  \u2014  Confidential, Field Core internal use.",
                        conf_s,
                    )
                ]
            ],
            colWidths=[170 * mm],
        )
        conf_box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(box2_fill)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements.append(conf_box)

        elements.append(PageBreak())
        return elements

    def _incident_markup(
        self,
        value: str | None,
        *,
        fallback: str = "Not provided.",
    ) -> str:
        """Escape incident text for ReportLab while preserving user line breaks."""
        cleaned = (value or "").strip()
        if not cleaned:
            return f"<i>{escape(fallback)}</i>"
        return (
            escape(cleaned)
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "<br/>")
        )

    def _build_incident_cover_icon(self) -> Drawing:
        """Build simple incident icon inspired by clean form-style reference art."""
        brand = colors.HexColor("#0f7c73")
        accent = colors.HexColor("#f4c542")

        drawing = Drawing(40 * mm, 28 * mm)
        drawing.add(
            Circle(23 * mm, 11 * mm, 11 * mm, fillColor=brand, strokeColor=brand)
        )
        drawing.add(
            Circle(10 * mm, 20 * mm, 7 * mm, fillColor=brand, strokeColor=brand)
        )
        drawing.add(
            String(
                23 * mm,
                9.5 * mm,
                "IR",
                fontName="Helvetica-Bold",
                fontSize=13,
                fillColor=colors.white,
                textAnchor="middle",
            )
        )
        drawing.add(
            String(
                10 * mm,
                16.5 * mm,
                "!",
                fontName="Helvetica-Bold",
                fontSize=15,
                fillColor=accent,
                textAnchor="middle",
            )
        )
        return drawing

    def _build_incident_panel(
        self,
        title: str,
        content: Any,
        *,
        width: float = 170 * mm,
        fill_hex: str = "#d8efee",
        border_hex: str = "#63736f",
        title_hex: str = "#0f7c73",
    ) -> Table:
        """Wrap content in a soft report-form panel."""
        title_s = ParagraphStyle(
            "IncPanelTitle_local",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(title_hex),
        )
        content_items = content if isinstance(content, list) else [content]
        panel = Table(
            [
                [
                    [
                        Paragraph(escape(title), title_s),
                        Spacer(1, 2.5 * mm),
                        *content_items,
                    ]
                ]
            ],
            colWidths=[width],
        )
        panel.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill_hex)),
                    ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor(border_hex)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return panel

    def _build_incident_grid_table(
        self,
        rows: list[list[Any]],
        col_widths: list[float],
        *,
        fill_hex: str = "#d8efee",
        border_hex: str = "#63736f",
    ) -> Table:
        """Build thin-lined table matching reference form layout."""
        table = Table(rows, colWidths=col_widths)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill_hex)),
                    ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor(border_hex)),
                    ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.HexColor(border_hex)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return table

    def _draw_incident_body_chrome(
        self,
        canv: canvas.Canvas,
        doc: SimpleDocTemplate,
        *,
        seacom_ref: str,
        severity: str,
        generated_at: datetime,
    ) -> None:
        """Draw running header and footer for incident-report body pages."""
        page_w, page_h = doc.pagesize
        left = doc.leftMargin
        right = page_w - doc.rightMargin

        canv.saveState()
        try:
            header_y = page_h - 18 * mm
            header_h = 10 * mm
            canv.setFillColor(colors.HexColor("#0f2747"))
            canv.roundRect(
                left, header_y, doc.width, header_h, 3 * mm, stroke=0, fill=1
            )

            canv.setFillColor(colors.white)
            canv.setFont("Helvetica-Bold", 8)
            canv.drawString(left + 5 * mm, header_y + 6.2 * mm, "INCIDENT REPORT")

            canv.setFont("Helvetica", 7.5)
            canv.drawString(left + 44 * mm, header_y + 6.2 * mm, f"Ref {seacom_ref}")

            canv.setFont("Helvetica-Bold", 7.5)
            canv.drawRightString(
                right - 5 * mm, header_y + 6.2 * mm, f"Severity {severity}"
            )

            footer_y = 11 * mm
            canv.setStrokeColor(colors.HexColor("#cbd5e0"))
            canv.setLineWidth(0.6)
            canv.line(left, footer_y + 5 * mm, right, footer_y + 5 * mm)

            canv.setFillColor(colors.HexColor("#64748b"))
            canv.setFont("Helvetica", 7)
            footer_text = (
                f"CONFIDENTIAL | Generated {generated_at.strftime('%d %B %Y %H:%M UTC')} | "
                f"Ref {seacom_ref} | Page {canv.getPageNumber()}"
            )
            canv.drawCentredString(page_w / 2, footer_y + 1.2 * mm, footer_text)
        finally:
            canv.restoreState()

    # ── Incident report: dark navy running header bar ─────────────────────────

    def _build_incident_running_header(self, date_str: str) -> list:
        """Build a full-width dark navy header bar matching the Operations Report style."""
        nav = "#1a365d"  # dark navy
        wh = "#ffffff"

        hdr_l = ParagraphStyle(
            "IncRunHdrL_local",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica",
            textColor=colors.HexColor(wh),
            alignment=TA_LEFT,
        )
        hdr_r = ParagraphStyle(
            "IncRunHdrR_local",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica",
            textColor=colors.HexColor(wh),
            alignment=TA_RIGHT,
        )
        hdr_tbl = Table(
            [
                [
                    Paragraph(f"INCIDENT REPORT  |  {date_str}", hdr_l),
                    Paragraph("CONFIDENTIAL", hdr_r),
                ]
            ],
            colWidths=[120 * mm, 50 * mm],
        )
        hdr_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(nav)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        return [hdr_tbl, Spacer(1, 8 * mm)]

    # ── Incident report: 4-card KPI row ───────────────────────────────────────

    def _build_incident_overview_section(
        self,
        site: str,
        seacom_ref: str,
        technician: str,
        severity: str,
        incident_status: str,
        report_date: str,
        incident_summary: str | None = None,
    ) -> list:
        """Build a cleaner overview section with readable metadata cards."""
        elements: list = []
        nav = "#0f2747"
        teal = "#2563eb"
        gray = "#64748b"
        bord = "#dbe4f0"
        fill = "#f8fbff"

        sev_upper = severity.upper()
        if "CRITICAL" in sev_upper:
            sev_color = "#c53030"
        elif "HIGH" in sev_upper or "MAJOR" in sev_upper:
            sev_color = "#d97706"
        elif "MINOR" in sev_upper or "LOW" in sev_upper:
            sev_color = "#15803d"
        else:
            sev_color = nav

        eyebrow_s = ParagraphStyle(
            "IncOvEyebrow_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(teal),
            spaceAfter=2,
        )
        title_s = ParagraphStyle(
            "IncOvTitle_local",
            parent=self.styles["Normal"],
            fontSize=18,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(nav),
            leading=21,
            spaceAfter=6,
        )
        lbl_s = ParagraphStyle(
            "IncOvLbl_local",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(gray),
        )
        val_nav_s = ParagraphStyle(
            "IncOvValNav_local",
            parent=self.styles["Normal"],
            fontSize=12,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(nav),
            leading=15,
        )
        val_sev_s = ParagraphStyle(
            "IncOvValSev_local",
            parent=self.styles["Normal"],
            fontSize=12,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(sev_color),
            leading=15,
        )
        summary_lbl_s = ParagraphStyle(
            "IncOvSummaryLbl_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(teal),
            spaceAfter=3,
        )
        summary_body_s = ParagraphStyle(
            "IncOvSummaryBody_local",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor("#334155"),
            leading=15,
        )

        elements.append(Paragraph("INCIDENT SUMMARY", eyebrow_s))
        elements.append(Paragraph("Incident Overview", title_s))

        def _overview_card(
            label: str, value: str, *, severity_card: bool = False
        ) -> Table:
            value_style = val_sev_s if severity_card else val_nav_s
            accent = sev_color if severity_card else teal
            card = Table(
                [
                    [Paragraph(label, lbl_s)],
                    [
                        Paragraph(
                            self._incident_markup(value, fallback="N/A"), value_style
                        )
                    ],
                ],
                colWidths=[82 * mm],
            )
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill)),
                        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(bord)),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(accent)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            return card

        cards = [
            _overview_card("INCIDENT REF", seacom_ref),
            _overview_card("SITE", site),
            _overview_card("TECHNICIAN", technician),
            _overview_card("SEVERITY", severity, severity_card=True),
            _overview_card("INCIDENT STATUS", incident_status),
            _overview_card("REPORT DATE", report_date),
        ]

        grid = Table(
            [[cards[0], cards[1]], [cards[2], cards[3]], [cards[4], cards[5]]],
            colWidths=[83 * mm, 83 * mm],
        )
        grid.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(grid)

        if (incident_summary or "").strip():
            summary_box = Table(
                [
                    [
                        Paragraph("INCIDENT DESCRIPTION", summary_lbl_s),
                        Paragraph(
                            self._incident_markup(incident_summary), summary_body_s
                        ),
                    ]
                ],
                colWidths=[170 * mm],
            )
            summary_box.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill)),
                        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(bord)),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(teal)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                )
            )
            elements.append(summary_box)
            elements.append(Spacer(1, 3 * mm))

        return elements

    # ── Incident report: bordered narrative section box ───────────────────────

    def _build_incident_narrative_section(
        self,
        number: int,
        label: str,
        body: str | None,
    ) -> list:
        """
        Build a numbered narrative section with cleaner hierarchy and flowing body text.
        """
        nav = "#0f2747"
        teal = "#2563eb"
        hdr_bg = "#eff6ff"
        body_c = "#334155"
        bord = "#dbe4f0"

        num_s = ParagraphStyle(
            f"IncNarNum{number}_local",
            parent=self.styles["Normal"],
            fontSize=12,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(teal),
            alignment=TA_CENTER,
        )
        head_s = ParagraphStyle(
            f"IncNarHead{number}_local",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(nav),
        )
        body_s = ParagraphStyle(
            f"IncNarBody{number}_local",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor(body_c),
            leading=14,
            backColor=colors.white,
            borderColor=colors.HexColor(bord),
            borderWidth=0.5,
            borderPadding=6,
        )

        safe_body = self._incident_markup(body)

        hdr_row = Table(
            [[Paragraph(f"{number:02d}", num_s), Paragraph(escape(label), head_s)]],
            colWidths=[16 * mm, 154 * mm],
        )
        hdr_row.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(hdr_bg)),
                    ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor(teal)),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(bord)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ]
            )
        )
        body_para = Paragraph(safe_body, body_s)

        return [hdr_row, Spacer(1, 1 * mm), body_para, Spacer(1, 2 * mm)]

    def generate_incident_report_pdf_legacy(
        self, report: "IncidentReport", incident: Any | None = None
    ) -> BytesIO:  # type: ignore[name-defined]  # noqa: F821
        """Legacy incident PDF layout kept for reference only."""
        from app.models.incident_report import IncidentReport  # noqa: F401

        # ── Derived metadata ──────────────────────────────────────────────────
        generated_at = utcnow()
        incident_severity = getattr(
            incident, "severity", getattr(report, "severity", None)
        )
        severity_value = getattr(incident_severity, "value", incident_severity)
        inc_severity = (
            str(severity_value).replace("-", " ").upper() if severity_value else "N/A"
        )
        seacom_ref = (
            getattr(incident, "seacom_ref", None)
            or getattr(incident, "ref_no", None)
            or getattr(report, "seacom_ref", None)
            or str(report.incident_id)[:8].upper()
        )
        incident_status = (
            str(
                getattr(
                    getattr(incident, "status", None),
                    "value",
                    getattr(incident, "status", ""),
                )
                or "N/A"
            )
            .replace("-", " ")
            .title()
        )
        incident_summary = getattr(incident, "description", None)
        report_date_str = (
            report.report_date.strftime("%d %B %Y").upper()
            if report.report_date
            else "N/A"
        )
        report_date_display = (
            report.report_date.strftime("%d %b %Y") if report.report_date else "N/A"
        )

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=26 * mm,
            bottomMargin=18 * mm,
            title=f"Incident_Report_{report.id}",
        )

        story: list = []

        # ── Page 1: cover page ────────────────────────────────────────────────
        story.extend(
            self._build_incident_cover_page(
                seacom_ref=seacom_ref,
                site=report.site_name or "N/A",
                technician=report.technician_name or "N/A",
                severity=inc_severity,
                report_date=report_date_str,
                report_date_obj=report.report_date,
                generated_at=generated_at,
            )
        )

        # ── Page 2+: dark navy running header bar ─────────────────────────────
        story.extend(
            self._build_incident_overview_section(
                site=report.site_name or "N/A",
                seacom_ref=seacom_ref,
                technician=report.technician_name or "N/A",
                severity=inc_severity,
                incident_status=incident_status,
                report_date=report_date_display,
                incident_summary=incident_summary,
            )
        )
        story.append(Spacer(1, 1 * mm))

        # ── Section heading: INCIDENT SUMMARY ────────────────────────────────
        # ── 4-card KPI row ────────────────────────────────────────────────────
        # ── Narrative sections ────────────────────────────────────────────────
        narrative_sections = [
            (1, "Introduction", report.introduction),
            (2, "Problem Statement", report.problem_statement),
            (3, "Findings on Site", report.findings),
            (4, "Actions Taken", report.actions_taken),
            (5, "Root Cause Analysis", report.root_cause_analysis),
            (6, "Conclusion", report.conclusion),
        ]
        for number, label, body in narrative_sections:
            story.extend(self._build_incident_narrative_section(number, label, body))

        # ── Site photos ───────────────────────────────────────────────────────
        photos_raw: list = []
        try:
            attachments = report.attachments or {}
            photos_raw = attachments.get("photos", []) or []
        except Exception:
            pass

        # Keep failed fetches too — they render as a visible placeholder so a
        # missing photo never silently disappears and the count stays honest.
        photo_items = [
            (
                photo.get("original_name") or "Photo",
                photo.get("url") or photo.get("public_url"),
            )
            for photo in photos_raw
        ]
        photo_items = [(name, url) for name, url in photo_items if url]
        photo_bufs = self._fetch_images_parallel([url for _n, url in photo_items])
        photo_buffers: list[tuple[str, BytesIO | None]] = [
            (name, buf) for (name, _u), buf in zip(photo_items, photo_bufs)
        ]

        if photo_buffers:
            # Photo section heading — same bordered-box style as narrative sections
            count_label = len(photo_buffers)
            _ph_num_s = ParagraphStyle(
                "IncPhotoNum_local",
                parent=self.styles["Normal"],
                fontSize=13,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#2b6cb0"),
                alignment=TA_CENTER,
            )
            _ph_lbl_s = ParagraphStyle(
                "IncPhotoHead_local",
                parent=self.styles["Normal"],
                fontSize=11,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#1a365d"),
            )
            _ph_hdr = Table(
                [
                    [
                        Paragraph("07", _ph_num_s),
                        Paragraph(
                            f"SITE PHOTOS  \u2014  {count_label} IMAGE{'S' if count_label != 1 else ''}",
                            _ph_lbl_s,
                        ),
                    ]
                ],
                colWidths=[16 * mm, 154 * mm],
            )
            _ph_hdr.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ebf4ff")),
                        ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.HexColor("#2b6cb0")),
                        ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor("#2b6cb0")),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ]
                )
            )
            story.append(_ph_hdr)
            story.append(Spacer(1, 5 * mm))

            caption_s = ParagraphStyle(
                "IncPhotoCaption_local",
                parent=self.styles["Normal"],
                fontSize=7,
                fontName="Helvetica",
                textColor=colors.HexColor(_INC_LIGHT_GRAY),
                alignment=TA_CENTER,
            )
            COLS = 3
            PHOTO_W = (170 * mm - (COLS - 1) * 4 * mm) / COLS
            PHOTO_H = PHOTO_W * 0.68

            rows = [
                photo_buffers[i : i + COLS] for i in range(0, len(photo_buffers), COLS)
            ]
            for row_items in rows:
                img_row: list = []
                cap_row: list = []
                for name, buf in row_items:
                    img = self._fit_photo_image(buf, PHOTO_W, PHOTO_H) if buf else None
                    if img is not None:
                        img_row.append(img)
                        cap_row.append(Paragraph(escape(name[:35]), caption_s))
                    else:
                        img_row.append(Paragraph("<i>(unavailable)</i>", caption_s))
                        cap_row.append(Paragraph(escape(name[:35]), caption_s))

                while len(img_row) < COLS:
                    img_row.append(Spacer(PHOTO_W, PHOTO_H))
                    cap_row.append(Paragraph("", caption_s))

                col_widths = [PHOTO_W + 2 * mm] * COLS
                img_table = Table([img_row], colWidths=col_widths)
                img_table.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                            (
                                "BOX",
                                (0, 0),
                                (-1, -1),
                                0.4,
                                colors.HexColor(_INC_DIVIDER),
                            ),
                            (
                                "INNERGRID",
                                (0, 0),
                                (-1, -1),
                                0.4,
                                colors.HexColor(_INC_DIVIDER),
                            ),
                        ]
                    )
                )
                cap_table = Table([cap_row], colWidths=col_widths)
                cap_table.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]
                    )
                )
                # Keep each image row with its captions across page breaks.
                story.append(KeepTogether([img_table, cap_table]))

            story.append(Spacer(1, 4 * mm))

        # ── Signature block: PREPARED BY / APPROVED BY ───────────────────────
        story.append(Spacer(1, 3 * mm))

        sig_hdr_s = ParagraphStyle(
            "IncSigHdr_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2563eb"),
        )
        sig_name_s = ParagraphStyle(
            "IncSigName_local",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#0f2747"),
            spaceAfter=2,
        )
        sig_line_s = ParagraphStyle(
            "IncSigLine_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#475569"),
        )
        sig_intro_s = ParagraphStyle(
            "IncSigIntro_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#64748b"),
            leading=13,
        )
        sig_table = Table(
            [
                [
                    [
                        Paragraph("PREPARED BY", sig_hdr_s),
                        Spacer(1, 3),
                        Paragraph(report.technician_name or "N/A", sig_name_s),
                        Paragraph(f"Prepared on {report_date_display}", sig_intro_s),
                        Spacer(1, 4),
                        Paragraph(
                            "Signature:  _________________________________", sig_line_s
                        ),
                        Spacer(1, 3),
                        Paragraph(f"Date:  {report_date_display}", sig_line_s),
                    ],
                    [
                        Paragraph("APPROVED BY", sig_hdr_s),
                        Spacer(1, 3),
                        Paragraph("___________________________", sig_name_s),
                        Paragraph("Operations / NOC Authorisation", sig_intro_s),
                        Spacer(1, 4),
                        Paragraph(
                            "Signature:  _________________________________", sig_line_s
                        ),
                        Spacer(1, 3),
                        Paragraph("Date:  _____  /  _____  /  __________", sig_line_s),
                    ],
                ]
            ],
            colWidths=[85 * mm, 85 * mm],
        )
        sig_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbff")),
                    ("BOX", (0, 0), (0, 0), 0.6, colors.HexColor("#dbe4f0")),
                    ("BOX", (1, 0), (1, 0), 0.6, colors.HexColor("#dbe4f0")),
                    ("LINEBEFORE", (0, 0), (0, 0), 3, colors.HexColor("#2563eb")),
                    ("LINEBEFORE", (1, 0), (1, 0), 3, colors.HexColor("#2563eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(KeepTogether([sig_table]))

        # ── Footer ────────────────────────────────────────────────────────────
        self._configure_first_page_background("incident", _INC_PRIMARY, "#991b1b")
        try:
            doc.build(
                story,
                onFirstPage=self._draw_first_page_background,
                onLaterPages=lambda canv, doc: self._draw_incident_body_chrome(
                    canv,
                    doc,
                    seacom_ref=seacom_ref,
                    severity=inc_severity,
                    generated_at=generated_at,
                ),
            )
        finally:
            self._clear_first_page_background()
        buffer.seek(0)
        return buffer

    # ── Executive summary PDF (management) ───────────────────────────────────

    def _build_incident_cover_page(
        self,
        seacom_ref: str,
        site: str,
        technician: str,
        severity: str,
        report_date: str,
        report_date_obj: "datetime | None" = None,
        generated_at: datetime | None = None,
        incident_status: str = "N/A",
        incident_summary: str | None = None,
        response_note: str | None = None,
    ) -> list:
        """Build clean reference-inspired incident cover page."""
        elements: list = []
        brand = "#0f7c73"
        ink = "#182524"
        muted = "#566765"

        top_l_s = ParagraphStyle(
            "IncCovTopLRef_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor(ink),
        )
        top_r_s = ParagraphStyle(
            "IncCovTopRRef_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(ink),
            alignment=TA_RIGHT,
        )
        title_s = ParagraphStyle(
            "IncCovTitleRef2_local",
            parent=self.styles["Normal"],
            fontSize=28,
            fontName="Helvetica-Bold",
            leading=31,
            textColor=colors.HexColor(brand),
        )
        subtitle_s = ParagraphStyle(
            "IncCovSubtitleRef2_local",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            leading=14,
            textColor=colors.HexColor(muted),
        )
        label_s = ParagraphStyle(
            "IncCovLabelRef2_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(ink),
        )
        value_s = ParagraphStyle(
            "IncCovValueRef2_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            leading=13,
            textColor=colors.HexColor(ink),
        )
        footer_label_s = ParagraphStyle(
            "IncCovFooterLabelRef_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(ink),
        )
        footer_value_s = ParagraphStyle(
            "IncCovFooterValueRef_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor(ink),
        )

        generated_dt = generated_at or utcnow()
        generated_ts = generated_dt.strftime("%d %B %Y  %H:%M UTC")
        time_value = (
            report_date_obj.strftime("%H:%M UTC")
            if report_date_obj is not None
            else generated_dt.strftime("%H:%M UTC")
        )

        lockup_logo = self._load_fieldcore_lockup(max_width_mm=43, max_height_mm=15)

        header = Table(
            [
                [
                    Paragraph("Field Core incident services", top_l_s),
                    lockup_logo or Paragraph("FIELD CORE", top_r_s),
                ]
            ],
            colWidths=[109 * mm, 61 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(header)
        elements.append(Spacer(1, 10 * mm))

        hero = Table(
            [
                [
                    self._build_incident_cover_icon(),
                    [
                        Paragraph("Incident", title_s),
                        Paragraph("Report Form", title_s),
                        Spacer(1, 2 * mm),
                        Paragraph(
                            "Post-resolution field record capturing incident context, "
                            "restoration outcome, and submission details.",
                            subtitle_s,
                        ),
                    ],
                ]
            ],
            colWidths=[46 * mm, 124 * mm],
        )
        hero.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(hero)
        elements.append(Spacer(1, 8 * mm))

        meta_rows = [
            [Paragraph("Date of Service", label_s), Paragraph(report_date, value_s)],
            [Paragraph("Time", label_s), Paragraph(time_value, value_s)],
            [
                Paragraph("Staff Name", label_s),
                Paragraph(self._incident_markup(technician, fallback="N/A"), value_s),
            ],
            [
                Paragraph("Incident Reference", label_s),
                Paragraph(self._incident_markup(seacom_ref, fallback="N/A"), value_s),
            ],
            [
                Paragraph("Severity", label_s),
                Paragraph(self._incident_markup(severity, fallback="N/A"), value_s),
            ],
            [
                Paragraph("Site", label_s),
                Paragraph(self._incident_markup(site, fallback="N/A"), value_s),
            ],
        ]
        elements.append(
            self._build_incident_panel(
                "Document Control:",
                self._build_incident_grid_table(meta_rows, [48 * mm, 122 * mm]),
            )
        )
        elements.append(Spacer(1, 5 * mm))

        detail_rows = [
            [
                Paragraph("Nature of Incident", label_s),
                Paragraph(
                    self._incident_markup(
                        incident_summary, fallback="Operational incident record."
                    ),
                    value_s,
                ),
            ],
            [
                Paragraph("Current Resolution Status", label_s),
                Paragraph(
                    self._incident_markup(incident_status, fallback="N/A"), value_s
                ),
            ],
            [
                Paragraph("Detailed Feedback", label_s),
                Paragraph(
                    self._incident_markup(
                        incident_summary, fallback="No narrative summary provided."
                    ),
                    value_s,
                ),
            ],
            [
                Paragraph("Further Action Needed", label_s),
                Paragraph(
                    self._incident_markup(
                        response_note,
                        fallback="Close-out recorded in main report body.",
                    ),
                    value_s,
                ),
            ],
        ]
        elements.append(
            self._build_incident_panel(
                "Details of Incident Report:",
                self._build_incident_grid_table(detail_rows, [48 * mm, 122 * mm]),
            )
        )
        elements.append(Spacer(1, 5 * mm))

        submission_rows = [
            [
                Paragraph("Reported by", label_s),
                Paragraph(self._incident_markup(technician, fallback="N/A"), value_s),
                Paragraph("Date Submitted", label_s),
                Paragraph(report_date, value_s),
            ],
            [
                Paragraph("Document Class", label_s),
                Paragraph("Internal Controlled Copy", value_s),
                Paragraph("Generated", label_s),
                Paragraph(generated_ts, value_s),
            ],
        ]
        elements.append(
            self._build_incident_panel(
                "Submission Record:",
                self._build_incident_grid_table(
                    submission_rows,
                    [28 * mm, 57 * mm, 28 * mm, 57 * mm],
                ),
            )
        )
        elements.append(Spacer(1, 8 * mm))

        footer = Table(
            [
                [
                    Paragraph("Reported by:", footer_label_s),
                    Paragraph(
                        self._incident_markup(technician, fallback="N/A"),
                        footer_value_s,
                    ),
                    Paragraph("Date Submitted:", footer_label_s),
                    Paragraph(report_date, footer_value_s),
                ]
            ],
            colWidths=[21 * mm, 61 * mm, 28 * mm, 60 * mm],
        )
        footer.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(footer)
        elements.append(Spacer(1, 1.5 * mm))

        footer2 = Table(
            [
                [
                    Paragraph("Designation:", footer_label_s),
                    Paragraph("Field Technician", footer_value_s),
                    Paragraph("Reference:", footer_label_s),
                    Paragraph(
                        self._incident_markup(seacom_ref, fallback="N/A"),
                        footer_value_s,
                    ),
                ]
            ],
            colWidths=[21 * mm, 61 * mm, 21 * mm, 67 * mm],
        )
        footer2.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        elements.append(footer2)
        elements.append(PageBreak())
        return elements

    def _draw_incident_body_chrome(
        self,
        canv: canvas.Canvas,
        doc: SimpleDocTemplate,
        *,
        seacom_ref: str,
        severity: str,
        generated_at: datetime,
    ) -> None:
        """Draw light reference-style running header and footer."""
        page_w, page_h = doc.pagesize
        left = doc.leftMargin
        right = page_w - doc.rightMargin
        brand = colors.HexColor("#0f7c73")
        ink = colors.HexColor("#182524")
        muted = colors.HexColor("#647874")

        canv.saveState()
        try:
            header_y = page_h - 12 * mm
            canv.setFillColor(ink)
            canv.setFont("Helvetica", 8)
            canv.drawString(left, header_y, "Samo Engineering // Incident Report")
            canv.setFont("Helvetica-Bold", 8)
            canv.drawRightString(right, header_y, f"Ref {seacom_ref}")

            canv.setStrokeColor(brand)
            canv.setLineWidth(1)
            canv.line(left, header_y - 2.5 * mm, right, header_y - 2.5 * mm)

            footer_y = 10 * mm
            canv.setStrokeColor(colors.HexColor("#c8dddb"))
            canv.setLineWidth(0.6)
            canv.line(left, footer_y + 5 * mm, right, footer_y + 5 * mm)
            canv.setFillColor(muted)
            canv.setFont("Helvetica", 7)
            footer_text = (
                f"Generated {generated_at.strftime('%d %B %Y %H:%M UTC')} | "
                f"Severity {severity} | Ref {seacom_ref} | Page {canv.getPageNumber()}"
            )
            canv.drawCentredString(page_w / 2, footer_y + 1.1 * mm, footer_text)
        finally:
            canv.restoreState()

    def _build_incident_overview_section(
        self,
        site: str,
        seacom_ref: str,
        technician: str,
        severity: str,
        incident_status: str,
        report_date: str,
        incident_summary: str | None = None,
    ) -> list:
        """Build light form-style overview section."""
        elements: list = []
        brand = "#0f7c73"
        ink = "#182524"

        heading_s = ParagraphStyle(
            "IncOverviewHeadingRef_local",
            parent=self.styles["Normal"],
            fontSize=16,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(brand),
            spaceAfter=4,
        )
        label_s = ParagraphStyle(
            "IncOverviewLabelRef_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(ink),
        )
        value_s = ParagraphStyle(
            "IncOverviewValueRef_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            leading=13,
            textColor=colors.HexColor(ink),
        )

        elements.append(Paragraph("Incident Overview", heading_s))
        meta_rows = [
            [
                Paragraph("Incident Reference", label_s),
                Paragraph(self._incident_markup(seacom_ref, fallback="N/A"), value_s),
                Paragraph("Site", label_s),
                Paragraph(self._incident_markup(site, fallback="N/A"), value_s),
            ],
            [
                Paragraph("Technician", label_s),
                Paragraph(self._incident_markup(technician, fallback="N/A"), value_s),
                Paragraph("Severity", label_s),
                Paragraph(self._incident_markup(severity, fallback="N/A"), value_s),
            ],
            [
                Paragraph("Incident Status", label_s),
                Paragraph(
                    self._incident_markup(incident_status, fallback="N/A"), value_s
                ),
                Paragraph("Report Date", label_s),
                Paragraph(self._incident_markup(report_date, fallback="N/A"), value_s),
            ],
        ]
        elements.append(
            self._build_incident_panel(
                "Details of Incident Report:",
                self._build_incident_grid_table(
                    meta_rows,
                    [34 * mm, 51 * mm, 34 * mm, 51 * mm],
                ),
            )
        )

        if (incident_summary or "").strip():
            summary_rows = [
                [
                    Paragraph("Incident Description", label_s),
                    Paragraph(self._incident_markup(incident_summary), value_s),
                ]
            ]
            elements.append(Spacer(1, 4 * mm))
            elements.append(
                self._build_incident_panel(
                    "Details of Service Report:",
                    self._build_incident_grid_table(summary_rows, [48 * mm, 122 * mm]),
                )
            )

        elements.append(Spacer(1, 4 * mm))
        return elements

    def _build_incident_resolution_panel(
        self,
        rows: list[tuple[str, str | None]],
    ) -> list:
        """Build main incident narrative as single reference-style panel."""
        ink = "#182524"
        label_s = ParagraphStyle(
            "IncResolutionLabelRef_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(ink),
        )
        value_s = ParagraphStyle(
            "IncResolutionValueRef_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            leading=13,
            textColor=colors.HexColor(ink),
        )

        table_rows = [
            [
                Paragraph(escape(label), label_s),
                Paragraph(self._incident_markup(body), value_s),
            ]
            for label, body in rows
        ]
        panel = self._build_incident_panel(
            "Details of Resolution Report:",
            self._build_incident_grid_table(table_rows, [48 * mm, 122 * mm]),
        )
        return [panel, Spacer(1, 4 * mm)]

    def _build_incident_submission_footer(
        self,
        *,
        technician: str,
        report_date: str,
        seacom_ref: str,
        designation: str = "Field Technician",
    ) -> KeepTogether:
        """Build simple footer metadata row like reference form."""
        ink = "#182524"
        label_s = ParagraphStyle(
            "IncSubmitFooterLabel_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(ink),
        )
        value_s = ParagraphStyle(
            "IncSubmitFooterValue_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor(ink),
        )

        row1 = Table(
            [
                [
                    Paragraph("Reported by:", label_s),
                    Paragraph(
                        self._incident_markup(technician, fallback="N/A"), value_s
                    ),
                    Paragraph("Date Submitted:", label_s),
                    Paragraph(
                        self._incident_markup(report_date, fallback="N/A"), value_s
                    ),
                ]
            ],
            colWidths=[21 * mm, 61 * mm, 28 * mm, 60 * mm],
        )
        row2 = Table(
            [
                [
                    Paragraph("Designation:", label_s),
                    Paragraph(designation, value_s),
                    Paragraph("Reference:", label_s),
                    Paragraph(
                        self._incident_markup(seacom_ref, fallback="N/A"), value_s
                    ),
                ]
            ],
            colWidths=[21 * mm, 61 * mm, 21 * mm, 67 * mm],
        )
        for tbl in (row1, row2):
            tbl.setStyle(
                TableStyle(
                    [
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
        return KeepTogether([row1, Spacer(1, 1.5 * mm), row2])

    def generate_incident_report_pdf(
        self, report: "IncidentReport", incident: Any | None = None
    ) -> BytesIO:  # type: ignore[name-defined]  # noqa: F821
        """Generate reference-inspired incident PDF layout."""
        from app.models.incident_report import IncidentReport  # noqa: F401

        generated_at = utcnow()
        incident_severity = getattr(
            incident, "severity", getattr(report, "severity", None)
        )
        severity_value = getattr(incident_severity, "value", incident_severity)
        inc_severity = (
            str(severity_value).replace("-", " ").upper() if severity_value else "N/A"
        )
        seacom_ref = (
            getattr(incident, "seacom_ref", None)
            or getattr(incident, "ref_no", None)
            or getattr(report, "seacom_ref", None)
            or str(report.incident_id)[:8].upper()
        )
        incident_status = (
            str(
                getattr(
                    getattr(incident, "status", None),
                    "value",
                    getattr(incident, "status", ""),
                )
                or "N/A"
            )
            .replace("-", " ")
            .title()
        )
        incident_summary = getattr(incident, "description", None)
        response_note = (
            report.conclusion or report.actions_taken or report.root_cause_analysis
        )
        report_date_str = (
            report.report_date.strftime("%d %B %Y").upper()
            if report.report_date
            else "N/A"
        )
        report_date_display = (
            report.report_date.strftime("%d %B %Y") if report.report_date else "N/A"
        )

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=16 * mm,
            title=f"Incident_Report_{report.id}",
        )

        story: list = []
        story.extend(
            self._build_incident_cover_page(
                seacom_ref=seacom_ref,
                site=report.site_name or "N/A",
                technician=report.technician_name or "N/A",
                severity=inc_severity,
                report_date=report_date_str,
                report_date_obj=report.report_date,
                generated_at=generated_at,
                incident_status=incident_status,
                incident_summary=incident_summary,
                response_note=response_note,
            )
        )
        story.extend(
            self._build_incident_overview_section(
                site=report.site_name or "N/A",
                seacom_ref=seacom_ref,
                technician=report.technician_name or "N/A",
                severity=inc_severity,
                incident_status=incident_status,
                report_date=report_date_display,
                incident_summary=incident_summary,
            )
        )
        story.extend(
            self._build_incident_resolution_panel(
                [
                    ("Introduction", report.introduction),
                    ("Problem Statement", report.problem_statement),
                    ("Findings on Site", report.findings),
                    ("Actions Taken", report.actions_taken),
                    ("Root Cause Analysis", report.root_cause_analysis),
                    ("Conclusion", report.conclusion),
                ]
            )
        )

        photos_raw: list = []
        try:
            attachments = report.attachments or {}
            photos_raw = attachments.get("photos", []) or []
        except Exception:
            pass

        # Keep failed fetches so they render as a placeholder, not a silent gap.
        photo_items = [
            (
                photo.get("original_name") or "Photo",
                photo.get("url") or photo.get("public_url"),
            )
            for photo in photos_raw
        ]
        photo_items = [(name, url) for name, url in photo_items if url]
        photo_bufs = self._fetch_images_parallel([url for _n, url in photo_items])
        photo_buffers: list[tuple[str, BytesIO | None]] = [
            (name, buf) for (name, _u), buf in zip(photo_items, photo_bufs)
        ]

        if photo_buffers:
            photo_label_s = ParagraphStyle(
                "IncPhotoLabelRef_local",
                parent=self.styles["Normal"],
                fontSize=8,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#182524"),
            )
            photo_caption_s = ParagraphStyle(
                "IncPhotoCaptionRef_local",
                parent=self.styles["Normal"],
                fontSize=7,
                fontName="Helvetica",
                textColor=colors.HexColor("#566765"),
                alignment=TA_CENTER,
            )

            count_label = len(photo_buffers)
            photo_header_rows = [
                [
                    Paragraph("Photos Attached", photo_label_s),
                    Paragraph(
                        f"{count_label} image{'s' if count_label != 1 else ''}",
                        photo_label_s,
                    ),
                ]
            ]
            story.append(
                self._build_incident_panel(
                    "Site Photos:",
                    self._build_incident_grid_table(
                        photo_header_rows, [120 * mm, 50 * mm]
                    ),
                )
            )
            story.append(Spacer(1, 4 * mm))

            cols = 3
            photo_w = (170 * mm - (cols - 1) * 4 * mm) / cols
            photo_h = photo_w * 0.68
            rows = [
                photo_buffers[i : i + cols] for i in range(0, len(photo_buffers), cols)
            ]
            for row_items in rows:
                img_row: list = []
                cap_row: list = []
                for name, buf in row_items:
                    img = self._fit_photo_image(buf, photo_w, photo_h) if buf else None
                    if img is not None:
                        img_row.append(img)
                        cap_row.append(Paragraph(escape(name[:35]), photo_caption_s))
                    else:
                        img_row.append(
                            Paragraph("<i>(unavailable)</i>", photo_caption_s)
                        )
                        cap_row.append(Paragraph(escape(name[:35]), photo_caption_s))

                while len(img_row) < cols:
                    img_row.append(Spacer(photo_w, photo_h))
                    cap_row.append(Paragraph("", photo_caption_s))

                col_widths = [photo_w + 2 * mm] * cols
                img_table = Table([img_row], colWidths=col_widths)
                img_table.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#63736f")),
                            (
                                "INNERGRID",
                                (0, 0),
                                (-1, -1),
                                0.6,
                                colors.HexColor("#63736f"),
                            ),
                        ]
                    )
                )
                cap_table = Table([cap_row], colWidths=col_widths)
                cap_table.setStyle(
                    TableStyle(
                        [
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]
                    )
                )
                # Keep each image row with its captions across page breaks.
                story.append(KeepTogether([img_table, cap_table]))

            story.append(Spacer(1, 3 * mm))

        story.append(
            self._build_incident_submission_footer(
                technician=report.technician_name or "N/A",
                report_date=report_date_display,
                seacom_ref=seacom_ref,
            )
        )

        doc.build(
            story,
            onLaterPages=lambda canv, doc: self._draw_incident_body_chrome(
                canv,
                doc,
                seacom_ref=seacom_ref,
                severity=inc_severity,
                generated_at=generated_at,
            ),
        )
        buffer.seek(0)
        return buffer

    def generate_executive_summary_pdf(
        self,
        month_label: str,
        sla_compliance: float,
        total_incidents: int,
        total_tasks: int,
        monthly_incidents: list[dict],  # [{month: str, count: int}]
        technician_performance: list[dict],  # [{name: str, incidents: int, tasks: int}]
        regional_performance: list[dict],  # [{region: str, compliance: float}]
    ) -> BytesIO:
        """
        Generate an executive management summary PDF with embedded charts.

        Args:
            month_label:             Display label, e.g. "February 2026"
            sla_compliance:          Overall SLA compliance percentage
            total_incidents:         Total incidents in period
            total_tasks:             Total tasks in period
            monthly_incidents:       Last 6 months incident counts for bar chart
            technician_performance:  Per-technician workload data for bar chart
            regional_performance:    Per-region SLA compliance for summary table

        Returns:
            BytesIO buffer containing the PDF document
        """
        buffer = BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=f"Executive_Summary_{month_label}",
        )

        story = []

        # ── Cover page ────────────────────────────────────────────────────────
        cover_details: list[list[str]] = [
            ["Period", month_label],
            ["SLA Compliance", f"{sla_compliance:.1f}%"],
            ["Total Incidents", str(total_incidents)],
            ["Total Tasks", str(total_tasks)],
            ["Generated", datetime.now().strftime("%d %B %Y %H:%M UTC")],
        ]
        story.extend(
            self._build_cover_page(
                title="Executive Management Report",
                subtitle=f"{month_label} - {_FIELDCORE_BRAND}",
                details=cover_details,
                cover_key="executive",
            )
        )

        # ── Page 2: banner header ─────────────────────────────────────────
        exec_primary, exec_accent = self._cover_palette("executive")
        story.extend(
            self._build_page_header(
                title="Executive Management Report",
                subtitle=f"{month_label}  |  {_FIELDCORE_BRAND}",
                primary_hex=exec_primary,
                accent_hex=exec_accent,
            )
        )

        # ── KPI summary row ───────────────────────────────────────────────────
        story.append(
            Paragraph("Key Performance Indicators", self.styles["SectionHeader"])
        )
        kpi_data = [
            ["Metric", "Value"],
            ["SLA Compliance", f"{sla_compliance:.1f}%"],
            ["Total Incidents", str(total_incidents)],
            ["Total Tasks", str(total_tasks)],
        ]
        kpi_table = Table(kpi_data, colWidths=[235, 235])
        kpi_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#ffffff")),
                    ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#2d3748")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                    ),
                ]
            )
        )
        story.append(kpi_table)
        story.append(Spacer(1, 16))

        # ── Monthly incident trend bar chart ──────────────────────────────────
        if monthly_incidents:
            story.append(
                Paragraph(
                    "Monthly Incident Trend (Last 6 Months)",
                    self.styles["SectionHeader"],
                )
            )
            story.append(Spacer(1, 8))

            chart_width = 400
            chart_height = 160
            d = Drawing(chart_width, chart_height + 40)
            bc = VerticalBarChart()
            bc.x = 40
            bc.y = 30
            bc.height = chart_height
            bc.width = chart_width - 60
            bc.data = [[entry.get("count", 0) for entry in monthly_incidents]]
            bc.categoryAxis.categoryNames = [
                entry.get("month", "") for entry in monthly_incidents
            ]
            bc.categoryAxis.labels.angle = 0
            bc.categoryAxis.labels.fontSize = 8
            bc.valueAxis.labels.fontSize = 8
            bc.bars[0].fillColor = colors.HexColor("#1a365d")
            bc.bars[0].strokeColor = colors.HexColor("#0b2265")
            bc.valueAxis.valueMin = 0
            d.add(bc)
            story.append(d)
            story.append(Spacer(1, 16))

        # ── Technician workload bar chart ─────────────────────────────────────
        if technician_performance:
            story.append(
                Paragraph(
                    "Technician Activity (Incidents + Tasks)",
                    self.styles["SectionHeader"],
                )
            )
            story.append(Spacer(1, 8))

            names = [e.get("name", "Unknown")[:18] for e in technician_performance[:8]]
            totals = [
                e.get("incidents", 0) + e.get("tasks", 0)
                for e in technician_performance[:8]
            ]

            chart_w = 400
            chart_h = 140
            d2 = Drawing(chart_w, chart_h + 40)
            hbc = HorizontalBarChart()
            hbc.x = 90
            hbc.y = 10
            hbc.height = chart_h
            hbc.width = chart_w - 110
            hbc.data = [totals]
            hbc.categoryAxis.categoryNames = names
            hbc.categoryAxis.labels.fontSize = 7
            hbc.valueAxis.labels.fontSize = 7
            hbc.bars[0].fillColor = colors.HexColor("#2b6cb0")
            hbc.bars[0].strokeColor = colors.HexColor("#1a365d")
            hbc.valueAxis.valueMin = 0
            d2.add(hbc)
            story.append(d2)
            story.append(Spacer(1, 16))

        # ── Regional SLA compliance table ─────────────────────────────────────
        if regional_performance:
            story.append(
                Paragraph("Regional SLA Compliance", self.styles["SectionHeader"])
            )
            reg_data = [["Region", "Compliance %"]]
            for row in regional_performance:
                reg_data.append(
                    [
                        (row.get("region") or "N/A").replace("_", " ").title(),
                        f"{float(row.get('compliance') or 0):.1f}%",
                    ]
                )
            reg_table = Table(reg_data, colWidths=[300, 170])
            reg_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#ffffff")),
                        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#2d3748")),
                        ("ALIGN", (1, 0), (1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                        ),
                    ]
                )
            )
            story.append(reg_table)
            story.append(Spacer(1, 16))

        # ── Footer ─────────────────────────────────────────────────────────────
        story.append(self._create_divider())
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC | "
                f"Executive Summary - {month_label}",
                self.styles["Footer"],
            )
        )

        self._configure_first_page_background("executive", exec_primary, exec_accent)
        try:
            doc.build(story, onFirstPage=self._draw_first_page_background)
        finally:
            self._clear_first_page_background()
        buffer.seek(0)
        return buffer

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _build_page_header(
        self,
        title: str,
        subtitle: str,
        primary_hex: str = "#0b2265",
        accent_hex: str = "#1a365d",
    ) -> list:
        """
        Build a full-width dark banner for the top of every content page.
        Mirrors the .content-header style from the client HTML report:
        [ Field Core mark | title + subtitle | Field Core lockup ]
        """
        mark_logo = self._load_fieldcore_mark(max_width_mm=46, max_height_mm=16)
        lockup_logo = self._load_fieldcore_lockup(max_width_mm=46, max_height_mm=16)

        title_s = ParagraphStyle(
            "BnrTitle",
            parent=self.styles["Normal"],
            fontSize=17,
            fontName="Helvetica-Bold",
            textColor=colors.white,
            leading=22,
            spaceAfter=2,
        )
        sub_s = ParagraphStyle(
            "BnrSub",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor("#a0aec0"),
        )
        fallback_s = ParagraphStyle(
            "BnrFb",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.white,
            alignment=TA_CENTER,
        )

        banner = Table(
            [
                [
                    mark_logo or Paragraph("<b>FC</b>", fallback_s),
                    [
                        Paragraph(title, title_s),
                        Spacer(1, 2),
                        Paragraph(subtitle, sub_s),
                    ],
                    lockup_logo or Paragraph("<b>FIELD CORE</b>", fallback_s),
                ]
            ],
            colWidths=[46 * mm, 78 * mm, 46 * mm],
        )
        banner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(primary_hex)),
                    ("ALIGN", (0, 0), (0, 0), "CENTER"),
                    ("ALIGN", (1, 0), (1, 0), "LEFT"),
                    ("ALIGN", (2, 0), (2, 0), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 11),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
                    ("LINEBELOW", (0, 0), (-1, 0), 4, colors.HexColor(accent_hex)),
                ]
            )
        )
        return [banner, Spacer(1, 7 * mm)]

    def _build_metadata_cards(
        self,
        items: list[tuple[str, str]],
        primary_hex: str = "#0b2265",
    ) -> list:
        """
        Build a 2-column card-style metadata grid.
        Mirrors .metadata-grid / .metadata-card from the client HTML report.
        Items are paired left-right; each pair occupies a label row + value row.
        """
        label_s = ParagraphStyle(
            "CrdLbl",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#5f6f8d"),
        )
        val_s = ParagraphStyle(
            "CrdVal",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1a202c"),
            leading=14,
        )

        table_data: list = []
        style_cmds: list = [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7e1f2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]

        row_i = 0
        for i in range(0, len(items), 2):
            left_lbl, left_val = items[i]
            right_lbl, right_val = items[i + 1] if i + 1 < len(items) else ("", "")

            # Label row
            table_data.append(
                [
                    Paragraph(left_lbl.upper(), label_s),
                    Paragraph(right_lbl.upper() if right_lbl else "", label_s),
                ]
            )
            style_cmds += [
                ("BACKGROUND", (0, row_i), (-1, row_i), colors.HexColor("#eef2f8")),
                ("TOPPADDING", (0, row_i), (-1, row_i), 8),
                ("BOTTOMPADDING", (0, row_i), (-1, row_i), 2),
            ]
            row_i += 1

            # Value row
            table_data.append(
                [
                    Paragraph(left_val or "N/A", val_s),
                    Paragraph(right_val or "", val_s),
                ]
            )
            style_cmds += [
                ("BACKGROUND", (0, row_i), (-1, row_i), colors.white),
                ("TOPPADDING", (0, row_i), (-1, row_i), 2),
                ("BOTTOMPADDING", (0, row_i), (-1, row_i), 10),
            ]
            row_i += 1

        if not table_data:
            return []

        tbl = Table(table_data, colWidths=[85 * mm, 85 * mm])
        tbl.setStyle(TableStyle(style_cmds))
        return [tbl, Spacer(1, 6 * mm)]

    def _create_divider(self, color_hex: str = "#1a365d"):
        """Create a divider line as a table."""
        divider = Table(
            [
                [""],
            ],
            colWidths=[470],
        )
        divider.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, 0), 2, colors.HexColor(color_hex)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return divider

    def _format_report_type(self, report_type: ReportType) -> str:
        """Format report type enum to display string."""
        if report_type == ReportType.DATACENTER:
            return "Datacenter Inspection"
        if report_type == ReportType.POP:
            return "POP Inspection"
        return report_type.value.replace("-", " ").title()

    def _format_datetime(self, dt: datetime | None) -> str:
        """Format datetime to display string."""
        if dt is None:
            return "N/A"
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _format_iso_week(self, dt: datetime | None) -> str:
        """Format a date as the ISO week label used on diesel reports, e.g. "WEEK 30"."""
        return format_iso_week(dt)

    def _render_report_data(self, data: dict[str, Any], level: int = 0) -> list:
        """
        Recursively render report data dictionary into PDF elements.

        Args:
            data: The data dictionary to render
            level: Nesting level for indentation

        Returns:
            List of PDF elements
        """
        elements = []
        indent = "    " * level

        for key, value in data.items():
            formatted_key = key.replace("_", " ").title()

            if isinstance(value, dict):
                elements.append(
                    Paragraph(
                        f"{indent}<b>{formatted_key}:</b>", self.styles["FieldValue"]
                    )
                )
                elements.extend(self._render_report_data(value, level + 1))
            elif isinstance(value, list):
                elements.append(
                    Paragraph(
                        f"{indent}<b>{formatted_key}:</b>", self.styles["FieldValue"]
                    )
                )
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        elements.append(
                            Paragraph(
                                f"{indent}    Item {i + 1}:", self.styles["FieldValue"]
                            )
                        )
                        elements.extend(self._render_report_data(item, level + 2))
                    else:
                        elements.append(
                            Paragraph(
                                f"{indent}    - {item}", self.styles["FieldValue"]
                            )
                        )
            elif isinstance(value, bool):
                display_value = "Yes" if value else "No"
                elements.append(
                    Paragraph(
                        f"{indent}<b>{formatted_key}:</b> {display_value}",
                        self.styles["FieldValue"],
                    )
                )
            else:
                display_value = str(value) if value is not None else "N/A"
                elements.append(
                    Paragraph(
                        f"{indent}<b>{formatted_key}:</b> {display_value}",
                        self.styles["FieldValue"],
                    )
                )

        return elements

    # ── Repeater report rendering ─────────────────────────────────────────────

    def _repeater_section_header(
        self, title: str, primary_hex: str, accent_hex: str
    ) -> list:
        """Render the light field-report section header used by repeater/diesel pages."""
        badge_style = ParagraphStyle(
            "RptSecBadge",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1b2540"),
        )
        header = Table([[Paragraph(title, badge_style)]], colWidths=[170 * mm])
        header.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e2ec")),
                    ("LINEBEFORE", (0, 0), (0, 0), 3, colors.HexColor(accent_hex)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        return [header, Spacer(1, 3 * mm)]

    def _render_checklist_table(
        self,
        section_data: dict[str, Any],
        label_map: dict[str, str],
        primary_hex: str = "#0e7490",
        polarity_map: dict[str, Any] | None = None,
    ) -> list:
        """Render a dict of check items as a color-coded checklist table.

        Two value shapes are accepted per key: the legacy `{passed: bool}` /
        bare-bool shape (unchanged), and the hosted-site tri-state
        `{status: "yes"|"no"|"n/a", issue}` shape. Tri-state colour is driven
        by `polarity_map[key].bad_when` rather than coerced to boolean, so
        `n/a` renders neutral (no green/red judgement) instead of a false
        pass or fail.
        """
        GREEN = "#166534"
        RED = "#991b1b"

        hdr_s = ParagraphStyle(
            "CkH",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.white,
        )
        lbl_s = ParagraphStyle(
            "CkL",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
        )
        res_s = ParagraphStyle(
            "CkR",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            alignment=TA_CENTER,
            textColor=colors.white,
        )
        iss_s = ParagraphStyle(
            "CkI",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Oblique",
            textColor=colors.HexColor("#4a5568"),
        )

        table_data: list = [
            [
                Paragraph("Check Item", hdr_s),
                Paragraph("Result", hdr_s),
                Paragraph("Issue / Notes", hdr_s),
            ]
        ]
        # None = neutral (n/a) — no green/red judgement, just striped background.
        pass_flags: list[bool | None] = []

        for key, value in section_data.items():
            label = label_map.get(key, key.replace("_", " ").title())
            if isinstance(value, dict) and "status" in value:
                status = str(value.get("status") or "").strip().lower()
                issue = (value.get("issue") or "").strip()
                bad_when = getattr((polarity_map or {}).get(key), "bad_when", "no")
                passed = None if (not status or status == "n/a") else status != bad_when
                result_text = status.upper() if status else "N/A"
            elif isinstance(value, dict):
                passed = bool(value.get("passed", True))
                issue = (value.get("issueDescription") or "").strip()
                result_text = "PASS" if passed else "FAIL"
            elif isinstance(value, bool):
                passed = value
                issue = ""
                result_text = "PASS" if passed else "FAIL"
            else:
                continue
            pass_flags.append(passed)
            table_data.append(
                [
                    Paragraph(label, lbl_s),
                    Paragraph(result_text, res_s),
                    Paragraph(issue or "N/A", iss_s),
                ]
            )

        if len(table_data) <= 1:
            return []

        style_cmds: list = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary_hex)),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
            ),
        ]
        for i, passed in enumerate(pass_flags, start=1):
            if passed is None:
                continue
            bg = GREEN if passed else RED
            style_cmds.append(("BACKGROUND", (1, i), (1, i), colors.HexColor(bg)))

        tbl = Table(table_data, colWidths=[95 * mm, 25 * mm, 50 * mm])
        tbl.setStyle(TableStyle(style_cmds))
        return [tbl]

    def _render_generator_table(
        self,
        gen_data: dict[str, Any],
        label: str,
        primary_hex: str = "#0e7490",
        accent_hex: str = "#155e75",
    ) -> list:
        """Render generator inspection data as a structured two-column table."""
        GREEN = "#166534"
        RED = "#991b1b"

        LABEL_MAP: dict[str, str] = {
            "serialNumber": "Serial Number",
            "paintWorkFree": "Paint Work Free of Damage",
            "generatorLocksFunctional": "Generator Locks Functional",
            "radiatorWaterLevel": "Radiator Water Level OK",
            "fanBeltTensionGood": "Fan Belt Tension Good",
            "oilLevelFull": "Oil Level Full",
            "fuelLevelFull": "Fuel Level Full",
            "emersionHeaterFunctional": "Immersion Heater Functional",
            "corrosionOnBatteryTerminals": "Corrosion on Battery Terminals",
            "looseWireTerminations": "Loose Wire Terminations",
            "batteryVoltageInternal": "Battery Voltage (Internal)",
            "deepSeaControllerOn": "Deep Sea Controller On",
            "fromStandby": "From Standby",
            "batteryVoltageAlternator": "Battery Voltage (Alternator)",
            "vibrationsObserved": "Vibrations Observed",
            "oilPressureAfterTest": "Oil Pressure After Test",
            "coolantLeaksAfterStop": "Coolant Leaks After Stop",
            "fuelLeaksAfterStop": "Fuel Leaks After Stop",
            "oilLeaksAfterStop": "Oil Leaks After Stop",
            "standbyHourMeterAfterTest": "Standby Hour Meter After Test",
            "numberOfStartsToDate": "Number of Starts to Date",
            "nextServiceDate": "Next Service Date",
            "nextServiceHourMeter": "Next Service Hour Meter",
            "litresOfFuelRequired": "Litres of Fuel Required",
            "batteryChargerOnFloat": "Battery Charger on Float",
            "generatorPlcTime": "Generator PLC Time",
            "plcTimeInSync": "PLC Time in Sync",
        }
        # Keys where True means BAD (inverted colour logic)
        INVERTED = {
            "corrosionOnBatteryTerminals",
            "looseWireTerminations",
            "vibrationsObserved",
            "coolantLeaksAfterStop",
            "fuelLeaksAfterStop",
            "oilLeaksAfterStop",
        }

        lbl_s = ParagraphStyle(
            "GnL",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2d3748"),
        )
        val_s = ParagraphStyle(
            "GnV",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#4a5568"),
        )
        bool_s = ParagraphStyle(
            "GnB",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            alignment=TA_CENTER,
            textColor=colors.white,
        )

        table_data: list = []
        bool_row_colors: dict[int, str] = {}

        for key, value in gen_data.items():
            if key in ("oilLevelImages", "fuelLevelImages"):
                continue
            field_label = LABEL_MAP.get(key, key.replace("_", " ").title())
            if isinstance(value, bool):
                inverted = key in INVERTED
                is_good = (not value) if inverted else value
                color = GREEN if is_good else RED
                text = "Yes" if value else "No"
                bool_row_colors[len(table_data)] = color
                table_data.append(
                    [Paragraph(field_label, lbl_s), Paragraph(text, bool_s)]
                )
            elif isinstance(value, (int, float)):
                unit = ""
                if "voltage" in key.lower():
                    unit = " V"
                elif "pressure" in key.lower():
                    unit = " psi"
                elif "litres" in key.lower():
                    unit = " L"
                elif "meter" in key.lower() or "hours" in key.lower():
                    unit = " hrs"
                table_data.append(
                    [Paragraph(field_label, lbl_s), Paragraph(f"{value}{unit}", val_s)]
                )
            elif isinstance(value, str) and value:
                table_data.append(
                    [Paragraph(field_label, lbl_s), Paragraph(value, val_s)]
                )

        if not table_data:
            return []

        style_cmds: list = [
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
            (
                "ROWBACKGROUNDS",
                (0, 0),
                (-1, -1),
                [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
            ),
        ]
        for row_i, color in bool_row_colors.items():
            style_cmds.append(
                ("BACKGROUND", (1, row_i), (1, row_i), colors.HexColor(color))
            )

        tbl = Table(table_data, colWidths=[105 * mm, 65 * mm])
        tbl.setStyle(TableStyle(style_cmds))
        return [tbl]

    def _render_environmental_systems(
        self, env: dict[str, Any], primary_hex: str
    ) -> list:
        """Render environmental systems (AC, fire, electric fence, alarms) as sub-tables."""
        elements: list = []

        sub_lbl = ParagraphStyle(
            "EnvSL",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(primary_hex),
            spaceBefore=8,
            spaceAfter=3,
        )
        _kv_lbl = ParagraphStyle(
            "EnvKL",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2d3748"),
        )
        _kv_val = ParagraphStyle(
            "EnvKV",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#4a5568"),
        )

        def _kv(rows: list[list[str]]) -> Table:
            t = Table(rows, colWidths=[100 * mm, 70 * mm])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 0),
                            (-1, -1),
                            [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                        ),
                    ]
                )
            )
            return t

        def yn(v: Any) -> str:
            return "Yes" if v else "No"

        ac = env.get("airConditioning") or {}
        if ac:
            elements.append(Paragraph("Air Conditioning", sub_lbl))
            elements.append(
                _kv(
                    [
                        ["Temperature", f"{ac.get('temperature', 'N/A')} deg C"],
                        ["Cycle Setting", str(ac.get("cycleSetting") or "N/A")],
                        ["Aircon Panel OK", yn(ac.get("airconPanelOk"))],
                    ]
                )
            )

        fire = env.get("fireSystem") or {}
        if fire:
            elements.append(Paragraph("Fire System", sub_lbl))
            elements.append(
                _kv(
                    [
                        ["Fire Panel OK", yn(fire.get("firePanelOk"))],
                        [
                            "Fire Extinguisher Pressure OK",
                            yn(fire.get("fireExtinguisherPressure")),
                        ],
                    ]
                )
            )

        fence = env.get("electricFence") or {}
        if fence:
            elements.append(Paragraph("Electric Fence", sub_lbl))
            elements.append(
                _kv(
                    [
                        [
                            "Energizer Functioning",
                            yn(fence.get("energizerFunctioning")),
                        ],
                        [
                            "Fence Free from Debris",
                            yn(fence.get("fenceFreeFromDebris")),
                        ],
                        ["No Disturbed Wiring", yn(fence.get("noDisturbedWiring"))],
                        [
                            "Wire Tension Acceptable",
                            yn(fence.get("wireTensionAcceptable")),
                        ],
                        ["Alarm Test Confirmed", yn(fence.get("alarmTestConfirmed"))],
                    ]
                )
            )

        alarms = env.get("alarmsAndSensors") or {}
        if alarms:
            elements.append(Paragraph("Alarms &amp; Sensors", sub_lbl))
            elements.append(
                _kv(
                    [
                        [
                            "Door Alarms Tested (Front)",
                            yn(alarms.get("doorAlarmsTestedFront")),
                        ],
                        [
                            "Door Alarms Tested (Rear)",
                            yn(alarms.get("doorAlarmsTestedRear")),
                        ],
                        [
                            "Flood Sensors Tested (Front)",
                            yn(alarms.get("floodSensorsTestedFront")),
                        ],
                        [
                            "Flood Sensors Tested (Rear)",
                            yn(alarms.get("floodSensorsTestedRear")),
                        ],
                    ]
                )
            )

        return elements

    def _get_media_source(self, item: Any) -> str:
        """Return the best available media source from a string or attachment dict."""
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            for key in ("signed_url", "url", "public_url", "file_path", "path"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _get_media_name(self, item: Any, default: str = "Photo") -> str:
        """Return a human-friendly media name for captions."""
        if isinstance(item, dict):
            for key in ("original_name", "name", "filename", "label"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return default

    def _is_renderable_image_item(self, item: Any) -> bool:
        """Check whether an attachment item looks like an image."""
        source = self._get_media_source(item)
        if not source:
            return False

        if isinstance(item, dict):
            content_type = (
                str(item.get("content_type") or item.get("mime_type") or "")
                .strip()
                .lower()
            )
            if content_type.startswith("image/"):
                return True

        lowered = source.lower().split("?", 1)[0].split("#", 1)[0]
        return lowered.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")
        )

    def _render_photo_grid(self, photos: list, story: list, cols: int = 3) -> None:
        """Render a list of photo URLs or dicts as a grid of images."""
        caption_style = ParagraphStyle(
            "PGCap",
            parent=self.styles["Normal"],
            fontSize=7,
            fontName="Helvetica",
            textColor=colors.HexColor("#718096"),
            alignment=TA_CENTER,
        )

        PHOTO_W = (170 * mm - (cols - 1) * 4 * mm) / cols
        PHOTO_H = PHOTO_W * 0.68

        items = []
        for photo in photos:
            url = self._get_media_source(photo)
            if not url:
                continue
            items.append((self._get_media_name(photo), url))
        bufs = self._fetch_images_parallel([u for _n, u in items])
        photo_buffers: list[tuple[str, BytesIO | None]] = [
            (name, buf) for (name, _u), buf in zip(items, bufs)
        ]

        col_widths = [PHOTO_W + 2 * mm] * cols
        for chunk in [
            photo_buffers[i : i + cols] for i in range(0, len(photo_buffers), cols)
        ]:
            img_row: list = []
            cap_row: list = []
            for name, buf in chunk:
                img = self._fit_photo_image(buf, PHOTO_W, PHOTO_H) if buf else None
                if img is not None:
                    img_row.append(img)
                else:
                    img_row.append(Paragraph("<i>(unavailable)</i>", caption_style))
                cap_row.append(Paragraph((name or "")[:35], caption_style))

            while len(img_row) < cols:
                img_row.append(Spacer(PHOTO_W, PHOTO_H))
                cap_row.append(Paragraph("", caption_style))

            img_tbl = Table([img_row], colWidths=col_widths)
            img_tbl.setStyle(
                TableStyle(
                    [
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                        (
                            "INNERGRID",
                            (0, 0),
                            (-1, -1),
                            0.5,
                            colors.HexColor("#e2e8f0"),
                        ),
                    ]
                )
            )
            cap_tbl = Table([cap_row], colWidths=col_widths)
            cap_tbl.setStyle(
                TableStyle(
                    [
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            # Keep each image row with its captions so they never split pages.
            story.append(KeepTogether([img_tbl, cap_tbl]))

    def _build_field_kv_table(self, rows: list[tuple[str, str]]) -> Table:
        """Build a clean two-column key/value table."""
        label_style = ParagraphStyle(
            "DieselKvLabel",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2d3748"),
        )
        value_style = ParagraphStyle(
            "DieselKvValue",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#4a5568"),
        )

        table_rows = [
            [
                Paragraph(escape(label), label_style),
                Paragraph(escape(value), value_style),
            ]
            for label, value in rows
        ]
        table = Table(table_rows, colWidths=[62 * mm, 108 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 0),
                        (-1, -1),
                        [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                    ),
                ]
            )
        )
        return table

    def _build_field_data_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        col_widths: list[float],
        primary_hex: str,
    ) -> Table:
        """Build a multi-column data table with header row."""
        header_style = ParagraphStyle(
            "DieselDataHeader",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.white,
            alignment=TA_CENTER,
        )
        cell_style = ParagraphStyle(
            "DieselDataCell",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
            leading=11,
        )

        table_rows: list[list[Any]] = [
            [Paragraph(escape(header), header_style) for header in headers]
        ]
        for row in rows:
            table_rows.append(
                [Paragraph(escape(str(value)), cell_style) for value in row]
            )

        table = Table(table_rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary_hex)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                    ),
                ]
            )
        )
        return table

    def _parse_diesel_runtime_minutes(self, value: Any) -> int | None:
        """Parse runtime stored as hours, numeric string, or H/M notation."""
        return parse_diesel_runtime_minutes(value)

    def _format_diesel_runtime(self, value: Any) -> str:
        """Format diesel runtime using the same compact H/M style as technician print."""
        total_minutes = self._parse_diesel_runtime_minutes(value)
        return self._format_runtime_minutes(total_minutes)

    def _format_runtime_minutes(self, total_minutes: int | None) -> str:
        """Format duration minutes in compact H/M notation."""
        if total_minutes is None:
            return "N/A"

        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 0 and minutes > 0:
            return f"{hours}H{minutes:02d}M"
        if hours > 0:
            return f"{hours}H"
        return f"{minutes}M"

    def _format_diesel_liters(self, value: Any, *, fixed: bool = False) -> str:
        """Format liter values consistently for diesel tables."""
        try:
            liters = float(value)
        except (TypeError, ValueError):
            return "N/A"

        if fixed or not liters.is_integer():
            return f"{liters:.2f} L"
        return f"{int(liters)} L"

    def _format_diesel_liters_plain(self, value: Any) -> str:
        """Format liter values without a unit suffix for compact diesel tables."""
        try:
            liters = float(value)
        except (TypeError, ValueError):
            return "N/A"
        return f"{liters:.2f}"

    def _format_diesel_amount(self, value: Any) -> str:
        """Format a Rand amount for diesel fill-up tables/cards, e.g. R 5,000.00."""
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return "N/A"
        return f"R {amount:,.2f}"

    def _text_value(self, value: Any, default: str = "N/A") -> str:
        """Return a display-safe string for report values."""
        if value is None:
            return default
        if isinstance(value, bool):
            return "Yes" if value else "No"
        text = str(value).strip()
        return text or default

    def _format_attachment_value(self, value: Any) -> str:
        """Summarize an attachment field for the generic Attachments table.

        Lists/dicts (e.g. a photo array) must never hit `str(value)` directly —
        that prints the Python repr (single-quoted, unescaped) instead of a
        readable summary.
        """
        if isinstance(value, (list, tuple)):
            return f"{len(value)} item(s)"
        if isinstance(value, dict):
            return f"{len(value)} field(s)"
        return self._text_value(value)[:60]

    def _route_photo_groups(self, data: dict[str, Any]) -> dict[str, list[Any]]:
        photos = data.get("photos") if isinstance(data.get("photos"), dict) else {}
        groups: dict[str, list[Any]] = {}

        def add(label: str, value: Any) -> None:
            if isinstance(value, list) and value:
                groups[label] = value

        add("Trip Start Photos", photos.get("trip_start_photos"))
        add("Trip End Photos", photos.get("trip_end_photos"))
        add("All Route Photos", photos.get("all_photos"))

        for item in photos.get("bridge_culvert_checks") or []:
            if isinstance(item, dict):
                title = self._text_value(item.get("location"), "Bridge / Culvert")
                add(f"Bridge / Culvert - {title}", item.get("photos"))

        for item in photos.get("activity_checks") or []:
            if isinstance(item, dict):
                title = self._text_value(item.get("location"), "Activity Check")
                add(f"Activity Check - {title}", item.get("photos"))

        for item in photos.get("manhole_inspections") or []:
            if isinstance(item, dict):
                title = self._text_value(item.get("manhole_id"), "Manhole")
                add(f"Manhole - {title}", item.get("photos"))

        return groups

    def _render_route_patrol_body(
        self,
        report: Report,
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """Render Routine Drive / route patrol data as a report, not raw JSON."""
        data = report.data if isinstance(report.data, dict) else {}
        photos = data.get("photos") if isinstance(data.get("photos"), dict) else {}

        body_style = ParagraphStyle(
            "RouteBody",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#344054"),
            leading=12,
        )
        label_style = ParagraphStyle(
            "RouteLabel",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#667085"),
            leading=10,
        )
        value_style = ParagraphStyle(
            "RouteValue",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#101828"),
            leading=12,
        )
        table_header_style = ParagraphStyle(
            "RouteTableHeader",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.white,
            leading=10,
        )
        table_cell_style = ParagraphStyle(
            "RouteTableCell",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor("#344054"),
            leading=10,
        )

        bridge_checks = [
            item
            for item in photos.get("bridge_culvert_checks") or []
            if isinstance(item, dict)
        ]
        activity_checks = [
            item
            for item in photos.get("activity_checks") or []
            if isinstance(item, dict)
        ]
        manhole_checks = [
            item
            for item in photos.get("manhole_inspections") or []
            if isinstance(item, dict)
        ]
        photo_groups = self._route_photo_groups(data)
        unique_photo_sources = {
            source
            for group in photo_groups.values()
            for photo in group
            if (source := self._get_media_source(photo))
        }
        final_notes = self._text_value(
            photos.get("final_notes") or data.get("final_notes")
        )
        anomaly_details = self._text_value(data.get("anomaly_details"), "")

        raw_anomalies_found = data.get("anomalies_found")
        if isinstance(raw_anomalies_found, str) and raw_anomalies_found.strip().lower() in (
            "true",
            "false",
        ):
            anomalies_found_text = (
                "Yes" if raw_anomalies_found.strip().lower() == "true" else "No"
            )
        else:
            anomalies_found_text = self._text_value(raw_anomalies_found)

        story.extend(
            self._repeater_section_header("Patrol Summary", primary_hex, accent_hex)
        )
        story.extend(
            self._build_field_overview_cards(
                [
                    ("Photos", str(len(unique_photo_sources))),
                    ("Manholes", str(len(manhole_checks))),
                    ("Bridge / Culvert", str(len(bridge_checks))),
                    ("Activity Checks", str(len(activity_checks))),
                ],
                accent_hex=accent_hex,
            )
        )
        story.append(
            self._build_field_kv_table(
                [
                    ("Route Segment", self._text_value(data.get("route_segment"))),
                    ("Patrol Date", self._text_value(data.get("patrol_date"))),
                    (
                        "NOC Ticket",
                        self._text_value(
                            photos.get("noc_ticket")
                            or getattr(report, "seacom_ref", None)
                        ),
                    ),
                    ("Technician", self._text_value(photos.get("technician_name"))),
                    ("Weather", self._text_value(data.get("weather_conditions"))),
                    ("Anomalies Found", anomalies_found_text),
                    ("Final Notes", final_notes),
                ]
            )
        )
        story.append(Spacer(1, 5 * mm))

        if anomaly_details:
            story.extend(
                self._repeater_section_header(
                    "Anomaly Summary", primary_hex, accent_hex
                )
            )
            story.append(Paragraph(escape(anomaly_details), body_style))
            story.append(Spacer(1, 5 * mm))

        def render_count_section(
            title: str,
            rows: list[dict[str, Any]],
            headers: list[str],
            row_builder,
            empty_message: str,
            widths: list[float],
        ) -> None:
            story.extend(self._repeater_section_header(title, primary_hex, accent_hex))
            if not rows:
                story.append(Paragraph(escape(empty_message), body_style))
                story.append(Spacer(1, 5 * mm))
                return

            table_data = [
                [Paragraph(escape(header), table_header_style) for header in headers]
            ]
            for row in rows:
                table_data.append(
                    [
                        Paragraph(escape(self._text_value(value)), table_cell_style)
                        for value in row_builder(row)
                    ]
                )

            table = Table(
                table_data,
                colWidths=[width * mm for width in widths],
                repeatRows=1,
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary_hex)),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d5dd")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.HexColor("#ffffff"), colors.HexColor("#f8fafc")],
                        ),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 6 * mm))

        # Dedup set shared across every inline photo block below plus the
        # catch-all "Photo Evidence" section — a check's photos are rendered
        # right after its own row/block, so they must not also print again
        # in the catch-all section at the end of the report.
        rendered_photo_urls: set[str] = set()

        def render_inline_photos(caption: str | None, photos: Any) -> None:
            """Render one item's own photos, deduped against every other
            inline block and the catch-all section, directly under it.
            `caption` is skipped when the item already printed its own
            title (e.g. the numbered manhole heading)."""
            unique_photos: list[Any] = []
            for photo in photos or []:
                source = self._get_media_source(photo)
                if not source or source in rendered_photo_urls:
                    continue
                rendered_photo_urls.add(source)
                unique_photos.append(photo)
            if not unique_photos:
                return
            if caption:
                story.append(Paragraph(escape(caption), value_style))
            self._render_photo_grid(unique_photos, story, cols=3)
            story.append(Spacer(1, 3 * mm))

        render_count_section(
            "Bridge / Culvert Checks",
            bridge_checks,
            ["Location", "Coordinates", "Ground Movement", "Flood Damage", "Risk", "Mitigation"],
            lambda row: [
                row.get("location"),
                row.get("coordinates"),
                row.get("ground_movement"),
                row.get("flood_damage"),
                row.get("risk_to_network"),
                row.get("mitigation"),
            ],
            "No bridge or culvert checks were recorded.",
            [30, 26, 24, 22, 22, 46],
        )
        for index, check in enumerate(bridge_checks, start=1):
            title = self._text_value(check.get("location"), f"Bridge / Culvert {index}")
            render_inline_photos(f"Bridge / Culvert {index} - {title}", check.get("photos"))

        render_count_section(
            "Third-Party Activity Checks",
            activity_checks,
            ["Location", "Coordinates", "Risk", "Mitigation"],
            lambda row: [
                row.get("location"),
                row.get("coordinates"),
                row.get("risk_to_network"),
                row.get("mitigation"),
            ],
            "No third-party activity checks were recorded.",
            [44, 42, 28, 56],
        )
        for index, check in enumerate(activity_checks, start=1):
            title = self._text_value(check.get("location"), f"Activity Check {index}")
            render_inline_photos(f"Activity Check {index} - {title}", check.get("photos"))

        # Manhole Inspections — every manhole captures 12 checklist fields plus
        # coordinates/remarks; a single wide table can't fit them (the old
        # version dropped 6 fields and joined the rest into an unlabeled
        # "No | No | No" string). Render one full label/value block per
        # manhole instead, so nothing captured on mobile/web is lost here.
        story.extend(
            self._repeater_section_header("Manhole Inspections", primary_hex, accent_hex)
        )
        if manhole_checks:
            manhole_title_style = ParagraphStyle(
                "RouteManholeTitle",
                parent=self.styles["Normal"],
                fontSize=10,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor(primary_hex),
                spaceBefore=2,
                spaceAfter=3,
            )
            for index, manhole in enumerate(manhole_checks, start=1):
                title = self._text_value(manhole.get("manhole_id"), f"Manhole {index}")
                story.append(Paragraph(escape(f"{index}. {title}"), manhole_title_style))
                story.append(
                    self._build_field_kv_table(
                        [
                            (
                                "Coordinates",
                                self._text_value(
                                    manhole.get("coordinates_recorded")
                                    or manhole.get("coordinates_on_file")
                                ),
                            ),
                            ("Lid Locked", self._text_value(manhole.get("lid_locked"))),
                            ("Can Be Unlocked", self._text_value(manhole.get("can_be_unlocked"))),
                            ("Ducts Sealed", self._text_value(manhole.get("ducts_sealed"))),
                            ("Clean / No Debris", self._text_value(manhole.get("clean_no_debris"))),
                            ("Marker In Place", self._text_value(manhole.get("marker_in_place"))),
                            ("Slack Management", self._text_value(manhole.get("slack_management"))),
                            ("Lid Disturbed", self._text_value(manhole.get("lid_disturbed"))),
                            ("Manhole Exposed", self._text_value(manhole.get("manhole_exposed"))),
                            (
                                "Disturbance / Erosion",
                                self._text_value(manhole.get("disturbance_erosion")),
                            ),
                            (
                                "Water Ingress / Rodents",
                                self._text_value(manhole.get("water_ingress_rodents")),
                            ),
                            ("Chemical Threats", self._text_value(manhole.get("chemical_threats"))),
                            ("Corrosion / Splice", self._text_value(manhole.get("corrosion_splice"))),
                            ("Remarks", self._text_value(manhole.get("remarks"))),
                        ]
                    )
                )

                render_inline_photos(None, manhole.get("photos"))
                story.append(Spacer(1, 5 * mm))
        else:
            story.append(
                Paragraph(
                    escape("No manhole inspections were recorded."), body_style
                )
            )
            story.append(Spacer(1, 5 * mm))

        # Filter before deciding whether to print the section header at all —
        # once manhole photos are rendered inline above, a manhole's group
        # here always ends up empty. Printing "Photo Evidence" unconditionally
        # (whenever `photo_groups` is non-empty) would leave an orphan heading
        # with nothing under it whenever manholes were the only photos.
        remaining_photo_groups = []
        for title, group in photo_groups.items():
            unique_group: list[Any] = []
            for photo in group:
                source = self._get_media_source(photo)
                if not source or source in rendered_photo_urls:
                    continue
                rendered_photo_urls.add(source)
                unique_group.append(photo)
            if unique_group:
                remaining_photo_groups.append((title, unique_group))

        if remaining_photo_groups:
            story.extend(
                self._repeater_section_header("Photo Evidence", primary_hex, accent_hex)
            )
            for title, unique_group in remaining_photo_groups:
                story.append(Paragraph(escape(title), value_style))
                self._render_photo_grid(unique_group, story, cols=3)
                story.append(Spacer(1, 3 * mm))

        story.extend(self._repeater_section_header("Attestation", primary_hex, accent_hex))
        story.append(
            Table(
                [
                    [
                        Paragraph("Prepared By", label_style),
                        Paragraph("Date Prepared", label_style),
                        Paragraph("SEACOM Attestation", label_style),
                    ],
                    [
                        Paragraph(
                            escape(self._text_value(photos.get("technician_name"))),
                            value_style,
                        ),
                        Paragraph(escape(self._format_datetime(report.created_at)), value_style),
                        Paragraph("Pending / N/A", value_style),
                    ],
                ],
                colWidths=[56 * mm, 56 * mm, 58 * mm],
                style=TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d5dd")),
                        (
                            "INNERGRID",
                            (0, 0),
                            (-1, -1),
                            0.4,
                            colors.HexColor("#d0d5dd"),
                        ),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f4f7")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                ),
            )
        )

    def _render_diesel_body(
        self,
        report: Report,
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """Render diesel report body to match technician-side export layout."""
        data = report.data if isinstance(report.data, dict) else {}
        diesel_fillups = (
            data.get("diesel_fillups")
            if isinstance(data.get("diesel_fillups"), list)
            else []
        )

        body_style = ParagraphStyle(
            "DieselBodyNote",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
            leading=15,
        )

        task = getattr(report, "task", None)
        task_site = getattr(task, "site", None)
        task_site_name = getattr(task_site, "name", None)
        task_site_id = getattr(task_site, "id", None) or getattr(task, "site_id", None)

        def resolve_site_name(entry: dict[str, Any]) -> str:
            explicit_name = entry.get("site_name")
            if isinstance(explicit_name, str) and explicit_name.strip():
                return explicit_name.strip()

            site_id = entry.get("site_id")
            if (
                task_site_name
                and site_id
                and task_site_id
                and str(site_id) == str(task_site_id)
            ):
                return str(task_site_name)

            if task_site_name:
                return str(task_site_name)

            if site_id:
                return str(site_id)
            return "N/A"

        valid_entries = [entry for entry in diesel_fillups if isinstance(entry, dict)]
        total_liters = sum(
            float(entry.get("liters_filled") or 0) for entry in valid_entries
        )
        total_amount = sum(
            float(entry.get("amount_used") or 0) for entry in valid_entries
        )

        primary_sites: list[str] = []
        serviced_generators: list[str] = []
        fill_reasons: list[str] = []
        runtimes: list[int] = []
        detail_rows: list[list[str]] = []

        for index, entry in enumerate(valid_entries, start=1):
            site_name = resolve_site_name(entry)
            if site_name != "N/A" and site_name not in primary_sites:
                primary_sites.append(site_name)

            runtime_minutes = self._parse_diesel_runtime_minutes(
                entry.get("gen_runtime_hours")
            )
            if runtime_minutes is not None:
                runtimes.append(runtime_minutes)

            gen_no = entry.get("gen_no")
            generator_label = f"Gen {gen_no}" if gen_no not in (None, "") else "Gen N/A"
            if generator_label not in serviced_generators:
                serviced_generators.append(generator_label)

            fill_reason = str(entry.get("fill_reason") or "Not specified")
            if fill_reason not in fill_reasons:
                fill_reasons.append(fill_reason)

            detail_rows.append(
                [
                    str(index),
                    site_name,
                    generator_label,
                    self._format_diesel_liters_plain(entry.get("liters_filled")),
                    self._format_diesel_amount(entry.get("amount_used")),
                    self._format_diesel_runtime(entry.get("gen_runtime_hours")),
                    fill_reason,
                ]
            )

        story.extend(
            self._repeater_section_header("1. Diesel Summary", primary_hex, accent_hex)
        )
        story.append(
            self._build_field_metric_cards(
                [
                    ("Fill Entries", str(len(valid_entries))),
                    (
                        "Total Liters",
                        self._format_diesel_liters(total_liters, fixed=True),
                    ),
                    ("Total Spend", self._format_diesel_amount(total_amount)),
                    ("Generators", str(len(serviced_generators))),
                    ("Runtime Records", str(len(runtimes))),
                ],
                accent_hex=accent_hex,
            )
        )
        story.append(Spacer(1, 4 * mm))
        story.append(
            self._build_field_kv_table(
                [
                    ("Week Number", self._format_iso_week(report.created_at)),
                    (
                        "Primary Site",
                        ", ".join(primary_sites)
                        or (str(task_site_name) if task_site_name else "N/A"),
                    ),
                    ("Serviced Generators", ", ".join(serviced_generators) or "N/A"),
                    (
                        "Highest Runtime",
                        self._format_runtime_minutes(
                            max(runtimes) if runtimes else None
                        ),
                    ),
                    ("Fill Reasons", ", ".join(fill_reasons) or "Not specified"),
                ]
            )
        )
        story.append(Spacer(1, 6 * mm))

        story.extend(
            self._repeater_section_header("2. Fill-up Entries", primary_hex, accent_hex)
        )

        if detail_rows:
            story.append(
                self._build_field_data_table(
                    headers=[
                        "#",
                        "Site",
                        "Generator",
                        "Liters",
                        "Amount (R)",
                        "Runtime",
                        "Fill Reason",
                    ],
                    rows=detail_rows,
                    col_widths=[
                        10 * mm,
                        32 * mm,
                        18 * mm,
                        18 * mm,
                        24 * mm,
                        24 * mm,
                        44 * mm,
                    ],
                    primary_hex=primary_hex,
                )
            )
        else:
            story.append(
                Paragraph("<i>No diesel fillup entries recorded.</i>", body_style)
            )

        story.append(Spacer(1, 6 * mm))

        self._render_diesel_attachments(report, story, primary_hex, accent_hex)

    def _render_diesel_history_body(
        self,
        history: "DieselSiteHistory",
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """
        Render a site's full fill-up history: an overview, then one section per
        generator. Unlike the single-fill-up report this spans many reports, so
        each row carries its own date and week.
        """
        body_style = ParagraphStyle(
            "DieselHistoryNote",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
            leading=15,
        )

        def date_label(value: datetime | None) -> str:
            return value.strftime("%d/%m/%Y") if value else "N/A"

        covered = "No fill-ups recorded"
        if history.first_fill_date and history.last_fill_date:
            covered = (
                f"{date_label(history.first_fill_date)} - "
                f"{date_label(history.last_fill_date)}"
            )

        story.extend(
            self._repeater_section_header("1. Site Overview", primary_hex, accent_hex)
        )
        story.append(
            self._build_field_metric_cards(
                [
                    ("Fill Entries", str(history.entry_count)),
                    (
                        "Total Liters",
                        self._format_diesel_liters(history.total_liters, fixed=True),
                    ),
                    ("Total Spend", self._format_diesel_amount(history.total_amount)),
                    ("Generators", str(len(history.generators))),
                ],
                accent_hex=accent_hex,
            )
        )
        story.append(Spacer(1, 4 * mm))

        requested_range = "All time"
        if history.date_from or history.date_to:
            requested_range = (
                f"{date_label(history.date_from) if history.date_from else 'Earliest'}"
                f" - {date_label(history.date_to) if history.date_to else 'Latest'}"
            )

        story.append(
            self._build_field_kv_table(
                [
                    ("Site", history.site_name),
                    ("Requested Range", requested_range),
                    ("History Covered", covered),
                    (
                        "Generators Serviced",
                        ", ".join(f"Gen {gen.gen_no}" for gen in history.generators)
                        or "None",
                    ),
                ]
            )
        )
        story.append(Spacer(1, 6 * mm))

        if not history.generators:
            story.extend(
                self._repeater_section_header(
                    "2. Fill-up History", primary_hex, accent_hex
                )
            )
            story.append(
                Paragraph(
                    "<i>No diesel fill-ups recorded for this site"
                    f"{'' if requested_range == 'All time' else ' in the selected range'}.</i>",
                    body_style,
                )
            )
            return

        for index, generator in enumerate(history.generators, start=2):
            story.extend(
                self._repeater_section_header(
                    f"{index}. Generator {generator.gen_no} History",
                    primary_hex,
                    accent_hex,
                )
            )

            # Date and week share a column, the way the source fill-up log writes
            # them ("20/05/2025 WEEK 20"). Nine separate columns do not fit A4
            # portrait once cell padding is accounted for, and the cover page
            # template is fixed at the 170mm portrait content width.
            rows: list[list[str]] = []
            for entry in generator.entries:
                week = entry.iso_week.replace("WEEK ", "W")
                short_date = (
                    entry.fill_date.strftime("%d/%m/%y") if entry.fill_date else "N/A"
                )
                rows.append(
                    [
                        f"{short_date} {week}",
                        self._format_diesel_liters_plain(entry.liters_filled),
                        self._format_diesel_amount(entry.amount_used),
                        self._format_diesel_runtime(entry.gen_runtime_hours),
                        str(entry.fill_reason or "Not specified"),
                        str(entry.technician_name or "N/A"),
                        str(entry.seacom_ref or "N/A"),
                    ]
                )

            rows.append(
                [
                    "Subtotal",
                    self._format_diesel_liters_plain(generator.total_liters),
                    self._format_diesel_amount(generator.total_amount),
                    self._format_runtime_minutes(generator.highest_runtime_minutes),
                    f"{generator.entry_count} entries",
                    "",
                    "",
                ]
            )

            story.append(
                self._build_field_data_table(
                    headers=[
                        "Date / Week",
                        "Liters",
                        "Amount (R)",
                        "Runtime",
                        "Fill Reason",
                        "Technician",
                        "SEACOM Ref",
                    ],
                    rows=rows,
                    # Sums to the 170mm portrait content width. Free text (fill
                    # reason, technician) may wrap; the reference must not, so it
                    # takes the slack.
                    col_widths=[
                        30 * mm,
                        15 * mm,
                        22 * mm,
                        20 * mm,
                        25 * mm,
                        27 * mm,
                        31 * mm,
                    ],
                    primary_hex=primary_hex,
                )
            )
            story.append(Spacer(1, 6 * mm))

    def generate_diesel_history_pdf(self, history: "DieselSiteHistory") -> BytesIO:
        """Generate the per-site diesel fill-up history PDF."""
        buffer = BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=f"Diesel_History_{history.site_name}",
        )

        story: list = []
        primary_hex, accent_hex = self._cover_palette("diesel")
        generated_display = self._format_datetime(utcnow())
        title = "Diesel Usage History"

        story.extend(
            self._build_field_cover_page(
                report_type_label="Generator Diesel Refill",
                title=title,
                site=history.site_name,
                subtitle=_FIELDCORE_REPORT_LABEL,
                descriptor=(
                    "Complete generator refuelling history for this site, compiled "
                    "for operational review, archive, and audit traceability."
                ),
                detail_items=[
                    ("Report Type", "Diesel Usage History"),
                    ("Site", history.site_name),
                    ("Fill Entries", str(history.entry_count)),
                    (
                        "Total Liters",
                        self._format_diesel_liters(history.total_liters, fixed=True),
                    ),
                    ("Total Spend", self._format_diesel_amount(history.total_amount)),
                    ("Generators", str(len(history.generators))),
                    ("Generated", generated_display),
                ],
                generated_at=generated_display,
                accent_hex=accent_hex,
            )
        )

        story.extend(
            self._build_field_page_header(
                title=title,
                subtitle=f"{_FIELDCORE_REPORT_LABEL} | {history.site_name}",
                accent_hex=accent_hex,
            )
        )

        self._render_diesel_history_body(history, story, primary_hex, accent_hex)

        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                f"Generated on {generated_display} UTC | Site: {history.site_name}",
                self.styles["Footer"],
            )
        )

        doc.build(story)
        buffer.seek(0)
        return buffer

    def _render_diesel_attachments(
        self,
        report: Report,
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """Render diesel report image attachments using the same gallery idea as technician print."""
        attachments = report.attachments if isinstance(report.attachments, dict) else {}
        candidates: list[Any] = []

        files = attachments.get("files")
        if isinstance(files, list):
            candidates.extend(files)
        else:
            for value in attachments.values():
                if isinstance(value, list):
                    candidates.extend(value)
                else:
                    candidates.append(value)

        photos: list[Any] = []
        seen_sources: set[str] = set()
        for item in candidates:
            if not self._is_renderable_image_item(item):
                continue
            source = self._get_media_source(item)
            if not source or source in seen_sources:
                continue
            seen_sources.add(source)
            photos.append(item)

        if not photos:
            return

        story.append(Spacer(1, 16))
        story.extend(
            self._repeater_section_header(
                "Uploaded Attachments", primary_hex, accent_hex
            )
        )
        self._render_photo_grid(photos, story, cols=3)

    # ── Hosted Site Routine (Datacenter / POP) rendering ──────────────────────

    def _hosted_site_section_heading(self, key: str) -> str:
        """Numbered heading matching the shared HOSTED_SITE_SECTIONS order, so
        the PDF, web sheet and mobile form can never fall out of order."""
        for section in HOSTED_SITE_SECTIONS:
            if section.key == key:
                return f"{section.number}. {section.label}"
        return key.replace("_", " ").title()

    def _render_hosted_site_cabinets(
        self,
        cabinets: list[dict[str, Any]],
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """One KeepTogether block per cabinet, in `order`: heading, 5-row check
        table with alarm note, then the PDU and cabinet photo side by side,
        then remarks. Photos are never hoisted into a trailing gallery."""
        empty_style = ParagraphStyle(
            "HostedSiteNoCabinets",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Oblique",
            textColor=colors.HexColor("#718096"),
        )
        if not cabinets:
            story.append(Paragraph("No cabinets recorded.", empty_style))
            return

        ordered = sorted(cabinets, key=lambda c: c.get("order") or 0)

        # Batch-fetch every cabinet photo up front (cached, parallel, deduped)
        # rather than one round trip per cabinet — mirrors _render_photo_grid's
        # own batching, which matters once a walkaround has 20-30 cabinets.
        urls: list[str] = []
        slots: list[tuple[int, str]] = []
        for i, cab in enumerate(ordered):
            for slot in ("pdu_photo", "cabinet_photo"):
                url = self._get_media_source(cab.get(slot))
                if url:
                    urls.append(url)
                    slots.append((i, slot))
        buffers = self._fetch_images_parallel(urls)
        photo_buffers: dict[tuple[int, str], BytesIO | None] = dict(
            zip(slots, buffers)
        )

        heading_style = ParagraphStyle(
            "HostedSiteCabinetHeading",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1b2540"),
        )
        caption_style = ParagraphStyle(
            "HostedSiteCabinetPhotoCaption",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=colors.HexColor("#718096"),
            alignment=TA_CENTER,
        )
        remarks_style = ParagraphStyle(
            "HostedSiteCabinetRemarks",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Oblique",
            textColor=colors.HexColor("#4a5568"),
        )
        cabinet_label_map = {
            key: meta.label for key, meta in CABINET_CHECK_LABELS.items()
        }
        photo_w = 80 * mm
        photo_h = photo_w * 0.68

        for i, cab in enumerate(ordered):
            block: list = []
            order = cab.get("order")
            location = self._text_value(cab.get("location"))
            equipment = cab.get("equipment_hosted")
            heading = f"Cabinet {order} — {location}"
            if equipment:
                heading += f" ({equipment})"
            block.append(Paragraph(escape(heading), heading_style))
            block.append(Spacer(1, 2 * mm))

            check_data = {
                key: {
                    "status": cab.get(key),
                    "issue": cab.get("alarm_note") if key == "visual_alarms" else None,
                }
                for key in (
                    "locked_and_keys",
                    "damages_observed",
                    "clean",
                    "patching_neat",
                    "visual_alarms",
                )
                if cab.get(key) is not None
            }
            block.extend(
                self._render_checklist_table(
                    check_data,
                    cabinet_label_map,
                    primary_hex,
                    polarity_map=CABINET_CHECK_LABELS,
                )
            )
            block.append(Spacer(1, 3 * mm))

            pdu_buf = photo_buffers.get((i, "pdu_photo"))
            cabinet_buf = photo_buffers.get((i, "cabinet_photo"))
            pdu_img = self._fit_photo_image(pdu_buf, photo_w, photo_h) if pdu_buf else None
            cabinet_img = (
                self._fit_photo_image(cabinet_buf, photo_w, photo_h)
                if cabinet_buf
                else None
            )
            img_row = [
                pdu_img or Paragraph("<i>(PDU photo unavailable)</i>", caption_style),
                cabinet_img
                or Paragraph("<i>(cabinet photo unavailable)</i>", caption_style),
            ]
            cap_row = [
                Paragraph("Full PDU Image", caption_style),
                Paragraph("Full Cabinet Image", caption_style),
            ]
            photo_table = Table([img_row, cap_row], colWidths=[85 * mm, 85 * mm])
            photo_table.setStyle(
                TableStyle(
                    [
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ("BOX", (0, 0), (0, -1), 0.5, colors.HexColor("#e2e8f0")),
                        ("BOX", (1, 0), (1, -1), 0.5, colors.HexColor("#e2e8f0")),
                    ]
                )
            )
            block.append(photo_table)

            remarks = (cab.get("remarks") or "").strip()
            if remarks:
                block.append(Spacer(1, 2 * mm))
                block.append(
                    Paragraph(f"<b>Remarks:</b> {escape(remarks)}", remarks_style)
                )

            block.append(Spacer(1, 6 * mm))
            story.append(KeepTogether(block))

    def _render_hosted_site_body(
        self,
        report: Report,
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """Datacenter and POP share this renderer — same source workbook
        template, one version apart. Sections follow HOSTED_SITE_SECTIONS
        order so the PDF, web sheet and mobile form can never disagree.
        Photos are read from data.cabinets[]/.extra_sections[], never from
        attachments.files (the generic attachments fallback is suppressed
        for these two types in generate_report_pdf)."""
        data = report.data if isinstance(report.data, dict) else {}

        body_style = ParagraphStyle(
            "HostedSiteBody",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
            leading=15,
        )

        # 1. Details
        header = data.get("header") if isinstance(data.get("header"), dict) else {}
        story.extend(
            self._repeater_section_header(
                self._hosted_site_section_heading("details"), primary_hex, accent_hex
            )
        )
        story.append(
            self._build_field_kv_table(
                [
                    ("Service Provider", self._text_value(header.get("service_provider"))),
                    ("Routine Type", self._text_value(header.get("routine_type"))),
                    ("Site Name", self._text_value(header.get("site_name"))),
                    ("Technician Name", self._text_value(header.get("technician_name"))),
                    (
                        "Date Routine Performed",
                        self._text_value(header.get("date_routine_performed")),
                    ),
                    ("SNOC Routine Ticket", self._text_value(header.get("snoc_routine_ticket"))),
                    (
                        "Site Owner Access Ticket",
                        self._text_value(header.get("site_owner_access_ticket")),
                    ),
                ]
            )
        )
        story.append(Spacer(1, 6 * mm))

        # 2. Site checklist
        story.extend(
            self._repeater_section_header(
                self._hosted_site_section_heading("site_checks"), primary_hex, accent_hex
            )
        )
        site_checks = (
            data.get("site_checks") if isinstance(data.get("site_checks"), dict) else {}
        )
        site_check_label_map = {key: meta.label for key, meta in SITE_CHECK_LABELS.items()}
        story.extend(
            self._render_checklist_table(
                site_checks,
                site_check_label_map,
                primary_hex,
                polarity_map=SITE_CHECK_LABELS,
            )
        )
        story.append(Spacer(1, 6 * mm))

        # 3. Power readings — two gated blocks; a block not checked "yes"
        # suppresses its reading grid, matching the workbook leaving those
        # rows blank.
        story.extend(
            self._repeater_section_header(
                self._hosted_site_section_heading("power_readings"),
                primary_hex,
                accent_hex,
            )
        )
        power = (
            data.get("power_readings")
            if isinstance(data.get("power_readings"), dict)
            else {}
        )
        rectifier = (
            power.get("rectifier") if isinstance(power.get("rectifier"), dict) else {}
        )
        ups = power.get("ups") if isinstance(power.get("ups"), dict) else {}

        story.append(
            Paragraph(
                f"<b>Rectifier:</b> {str(rectifier.get('status') or 'N/A').upper()}",
                body_style,
            )
        )
        story.append(Spacer(1, 2 * mm))
        if str(rectifier.get("status") or "").strip().lower() == "yes":
            story.append(
                self._build_field_kv_table(
                    [
                        (
                            "A Output Voltage",
                            self._text_value(rectifier.get("a_output_voltage")),
                        ),
                        ("A Load Current", self._text_value(rectifier.get("a_load_current"))),
                        (
                            "A Battery Charging Current",
                            self._text_value(rectifier.get("a_battery_charging_current")),
                        ),
                        (
                            "B Output Voltage",
                            self._text_value(rectifier.get("b_output_voltage")),
                        ),
                        ("B Load Current", self._text_value(rectifier.get("b_load_current"))),
                        (
                            "B Battery Charging Current",
                            self._text_value(rectifier.get("b_battery_charging_current")),
                        ),
                    ]
                )
            )
        story.append(Spacer(1, 4 * mm))

        story.append(
            Paragraph(f"<b>UPS:</b> {str(ups.get('status') or 'N/A').upper()}", body_style)
        )
        story.append(Spacer(1, 2 * mm))
        if str(ups.get("status") or "").strip().lower() == "yes":
            # Reproduces the workbook's own inconsistent visual row order (B
            # above A for usage/load and battery-charge voltage; battery
            # capacity reads A-then-B) so the printed PDF still matches the
            # source document — see DC_POP_REPORTS_IMPLEMENTATION_PLAN.md §4.3.
            story.append(
                self._build_field_kv_table(
                    [
                        ("B Usage/Load (%)", self._text_value(ups.get("b_load_percent"))),
                        ("A Usage/Load (%)", self._text_value(ups.get("a_load_percent"))),
                        (
                            "A Battery Capacity (%)",
                            self._text_value(ups.get("a_battery_capacity_percent")),
                        ),
                        (
                            "B Battery Capacity (%)",
                            self._text_value(ups.get("b_battery_capacity_percent")),
                        ),
                        (
                            "B Battery Charge Voltage",
                            self._text_value(ups.get("b_battery_charge_voltage")),
                        ),
                        (
                            "A Battery Charge Voltage",
                            self._text_value(ups.get("a_battery_charge_voltage")),
                        ),
                    ]
                )
            )
        story.append(Spacer(1, 6 * mm))

        # 4. Cabinets — the core requirement: checks + PDU/cabinet photo
        # together under each cabinet's own heading, never a trailing gallery.
        story.extend(
            self._repeater_section_header(
                self._hosted_site_section_heading("cabinets"), primary_hex, accent_hex
            )
        )
        cabinets = [c for c in (data.get("cabinets") or []) if isinstance(c, dict)]
        self._render_hosted_site_cabinets(cabinets, story, primary_hex, accent_hex)
        story.append(Spacer(1, 6 * mm))

        # 5. Extra sections
        story.extend(
            self._repeater_section_header(
                self._hosted_site_section_heading("extra_sections"),
                primary_hex,
                accent_hex,
            )
        )
        extra_sections = [
            s for s in (data.get("extra_sections") or []) if isinstance(s, dict)
        ]
        if extra_sections:
            for section in sorted(extra_sections, key=lambda s: s.get("order") or 0):
                story.append(
                    Paragraph(
                        f"<b>{escape(self._text_value(section.get('label')))}</b>",
                        body_style,
                    )
                )
                story.append(Spacer(1, 2 * mm))
                photos = (
                    section.get("photos") if isinstance(section.get("photos"), list) else []
                )
                if photos:
                    self._render_photo_grid(photos, story, cols=3)
                remarks = (section.get("remarks") or "").strip()
                if remarks:
                    story.append(Paragraph(f"<i>{escape(remarks)}</i>", body_style))
                story.append(Spacer(1, 4 * mm))
        else:
            story.append(Paragraph("<i>No extra sections captured.</i>", body_style))
        story.append(Spacer(1, 4 * mm))

        # 6. Other issues
        other_issues_meta = next(
            (s for s in HOSTED_SITE_SECTIONS if s.key == "other_issues"), None
        )
        story.extend(
            self._build_narrative_section(
                other_issues_meta.number if other_issues_meta else 6,
                other_issues_meta.label if other_issues_meta else "Other Issues",
                data.get("other_issues"),
                primary_hex,
                accent_hex,
            )
        )

    def _render_repeater_body(
        self,
        report: Report,
        story: list,
        primary_hex: str,
        accent_hex: str,
    ) -> None:
        """Render the full body of a Repeater Site Visit Report with professional tables."""
        data: dict = report.data or {}

        SITE_OBS_LABELS: dict[str, str] = {
            "perimeterFenceGood": "Perimeter Fence in Good Condition",
            "siteYardClean": "Site Yard Clean",
            "containerExteriorClean": "Container Exterior Clean",
            "generatorCanopiesClean": "Generator Canopies Clean",
            "gatesAndDoorsSecure": "Gates and Doors Secure",
            "securityCamerasGood": "Security Cameras Operational",
            "outdoorLightsWorking": "Outdoor Lights Working",
            "areaOutsideClean": "Area Outside Clean",
            "accessRoadSafe": "Access Road Safe",
            "accessGateLocked": "Access Gate Locked",
        }
        CONTAINER_INT_LABELS: dict[str, str] = {
            "wallsAndFloorClean": "Walls and Floor Clean",
            "lightingWorking": "Lighting Working",
            "cableGridGood": "Cable Grid in Good Condition",
            "odfNeat": "ODF Neat and Organised",
            "equipmentCabinetsClean": "Equipment Cabinets Clean",
            "noUnusualAlarms": "No Unusual Alarms Active",
            "cabinetLockedAndKeyed": "Cabinet Locked and Keyed",
            "noCombustibles": "No Combustible Materials Present",
            "noWaterIngressLights": "No Water Ingress (Lighting Area)",
            "noWaterIngressOutdoor": "No Water Ingress (Outdoor Area)",
            "siteRegisterUpdated": "Site Register Updated",
            "noDamageNeeded": "No Damage Requiring Repair",
        }

        _info_lbl_s = ParagraphStyle(
            "RpInfoL",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#2d3748"),
        )
        _info_val_s = ParagraphStyle(
            "RpInfoV",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#4a5568"),
        )
        body_s = ParagraphStyle(
            "RpBody",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica",
            textColor=colors.HexColor("#2d3748"),
            leading=16,
        )
        subhead_s = ParagraphStyle(
            "RpSubhead",
            parent=self.styles["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(primary_hex),
            spaceBefore=2,
            spaceAfter=4,
        )
        photo_note_style = ParagraphStyle(
            "RpPhotoNote",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#4a5568"),
            leading=13,
            spaceAfter=4,
        )

        def _info_table(rows: list[list[str]]) -> Table:
            t = Table(rows, colWidths=[80 * mm, 90 * mm])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#2d3748")),
                        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#4a5568")),
                        ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 0),
                            (-1, -1),
                            [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")],
                        ),
                    ]
                )
            )
            return t

        # ── 1. Routine Information ──────────────────────────────────────────
        def _text_value(value: Any) -> str:
            if value is None:
                return "N/A"
            if isinstance(value, str):
                cleaned = value.strip()
                return cleaned or "N/A"
            if isinstance(value, bool):
                return "Yes" if value else "No"
            return str(value)

        power_systems: dict[str, Any] = data.get("powerSystems") or {}
        ups_a: dict[str, Any] = power_systems.get("upsA") or {}
        ups_b: dict[str, Any] = power_systems.get("upsB") or {}
        rect_a: dict[str, Any] = power_systems.get("rectA") or {}
        rect_b: dict[str, Any] = power_systems.get("rectB") or {}

        story.extend(
            self._repeater_section_header(
                "1. Routine Information", primary_hex, accent_hex
            )
        )
        story.append(
            _info_table(
                [
                    ["Service Provider", report.service_provider or "N/A"],
                    ["Routine Type", _text_value(data.get("routineType"))],
                    [
                        "Date Routine Performed",
                        data.get("dateRoutinePerformed") or "N/A",
                    ],
                    [
                        "NOC Routine Ticket Reference",
                        data.get("nocRoutineTicketReference") or "N/A",
                    ],
                ]
            )
        )
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("UPS Display Panel Readings", subhead_s))
        story.append(
            self._build_field_data_table(
                headers=["Reading", "UPS A", "UPS B"],
                rows=[
                    [
                        "UPS Status",
                        _text_value(ups_a.get("upsStatus")),
                        _text_value(ups_b.get("upsStatus")),
                    ],
                    [
                        "UPS battery charge status %",
                        _text_value(ups_a.get("batteryChargeStatus")),
                        _text_value(ups_b.get("batteryChargeStatus")),
                    ],
                    [
                        "UPS load %",
                        _text_value(ups_a.get("loadPercent")),
                        _text_value(ups_b.get("loadPercent")),
                    ],
                    [
                        "UPS runtime h:m",
                        _text_value(ups_a.get("runtime")),
                        _text_value(ups_b.get("runtime")),
                    ],
                ],
                col_widths=[74 * mm, 48 * mm, 48 * mm],
                primary_hex=primary_hex,
            )
        )
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Rectifier Display Panel Readings", subhead_s))
        story.append(
            self._build_field_data_table(
                headers=["Reading", "Rect A", "Rect B"],
                rows=[
                    [
                        "Rectifier load current",
                        _text_value(rect_a.get("loadCurrent")),
                        _text_value(rect_b.get("loadCurrent")),
                    ],
                    [
                        "Rectifier output voltage",
                        _text_value(rect_a.get("outputVoltage")),
                        _text_value(rect_b.get("outputVoltage")),
                    ],
                    [
                        "Number of installed rectifier modules",
                        _text_value(rect_a.get("installedModules")),
                        _text_value(rect_b.get("installedModules")),
                    ],
                    [
                        "Rectifier modules on-line",
                        _text_value(rect_a.get("modulesOnLine")),
                        _text_value(rect_b.get("modulesOnLine")),
                    ],
                    [
                        "Rectifier battery charge status",
                        _text_value(rect_a.get("batteryChargeStatus")),
                        _text_value(rect_b.get("batteryChargeStatus")),
                    ],
                ],
                col_widths=[74 * mm, 48 * mm, 48 * mm],
                primary_hex=primary_hex,
            )
        )
        story.append(Spacer(1, 6 * mm))

        # ── 2 & 3. Generator Inspections ──────────────────────────────────
        for idx, (sec_title, gen_key) in enumerate(
            [
                ("2. Generator 1 Inspection", "gen1"),
                ("3. Generator 2 Inspection", "gen2"),
            ],
            start=1,
        ):
            gen_data: dict = data.get(gen_key) or {}
            story.extend(
                self._repeater_section_header(sec_title, primary_hex, accent_hex)
            )
            if gen_data:
                story.extend(
                    self._render_generator_table(
                        gen_data, sec_title, primary_hex, accent_hex
                    )
                )
            else:
                story.append(
                    Paragraph("<i>No data recorded for this generator.</i>", body_s)
                )
            story.append(Spacer(1, 6 * mm))

        # ── 4. Site Observations ──────────────────────────────────────────
        # Fall back to the abbreviated keys the mobile app wrote before it
        # was aligned to the canonical schema (see docs/report-schemas.md)
        # so already-submitted reports render instead of showing blank
        # sections.
        site_obs: dict = data.get("siteObservations") or data.get("siteObs") or {}
        story.extend(
            self._repeater_section_header(
                "4. Site Observations", primary_hex, accent_hex
            )
        )
        if site_obs:
            story.extend(
                self._render_checklist_table(site_obs, SITE_OBS_LABELS, primary_hex)
            )
        else:
            story.append(Paragraph("<i>No site observations recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 5. Container Interior ─────────────────────────────────────────
        container: dict = data.get("containerInterior") or data.get("container") or {}
        story.extend(
            self._repeater_section_header(
                "5. Container Interior", primary_hex, accent_hex
            )
        )
        if container:
            story.extend(
                self._render_checklist_table(
                    container, CONTAINER_INT_LABELS, primary_hex
                )
            )
        else:
            story.append(
                Paragraph("<i>No container interior data recorded.</i>", body_s)
            )
        story.append(Spacer(1, 6 * mm))

        # ── 6. Safety Observations ────────────────────────────────────────
        safety: dict = data.get("safetyObservations") or {}
        if not safety and "riskAssessment" in data:
            safety = {"basicRiskAssessmentPerformed": bool(data.get("riskAssessment"))}
        story.extend(
            self._repeater_section_header(
                "6. Safety Observations", primary_hex, accent_hex
            )
        )
        if safety:
            rows: list[list[str]] = [
                [
                    "Basic Risk Assessment Performed",
                    "Yes" if safety.get("basicRiskAssessmentPerformed") else "No",
                ],
            ]
            nearby = safety.get("nearbyConstructionWork") or {}
            if isinstance(nearby, dict):
                rows.append(
                    [
                        "Nearby Construction Work",
                        "Yes" if nearby.get("passed") else "No",
                    ]
                )
                if (nearby.get("issueDescription") or "").strip():
                    rows.append(
                        [
                            "Construction Work Notes",
                            nearby.get("issueDescription") or "",
                        ]
                    )
            story.append(_info_table(rows))
        else:
            story.append(Paragraph("<i>No safety observations recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 7. Environmental Systems ──────────────────────────────────────
        env: dict = data.get("environmentalSystems") or {}
        if not env and isinstance(data.get("env"), dict):
            legacy_env: dict = data["env"]
            env = {
                "airConditioning": {
                    k: legacy_env[k]
                    for k in ("temperature", "cycleSetting")
                    if k in legacy_env
                },
                "fireSystem": {
                    k: legacy_env[k]
                    for k in ("firePanelOk", "fireExtinguisherPressure")
                    if k in legacy_env
                },
                "electricFence": {
                    k: legacy_env[k]
                    for k in (
                        "energizerFunctioning",
                        "fenceFreeFromDebris",
                        "noDisturbedWiring",
                        "wireTensionAcceptable",
                        "alarmTestConfirmed",
                    )
                    if k in legacy_env
                },
                "alarmsAndSensors": {
                    k: legacy_env[k]
                    for k in (
                        "doorAlarmsTestedFront",
                        "doorAlarmsTestedRear",
                        "floodSensorsTestedFront",
                        "floodSensorsTestedRear",
                    )
                    if k in legacy_env
                },
            }
        story.extend(
            self._repeater_section_header(
                "7. Environmental Systems", primary_hex, accent_hex
            )
        )
        if env:
            story.extend(self._render_environmental_systems(env, primary_hex))
        else:
            story.append(
                Paragraph("<i>No environmental systems data recorded.</i>", body_s)
            )
        story.append(Spacer(1, 6 * mm))

        # ── 8. Site Concerns ──────────────────────────────────────────────
        concerns: dict = data.get("siteConcerns") or {}
        if not concerns and isinstance(data.get("concerns"), str) and data["concerns"].strip():
            concerns = {"description": data["concerns"]}
        story.extend(
            self._repeater_section_header("8. Site Concerns", primary_hex, accent_hex)
        )
        concern_desc = (concerns.get("description") or "").strip()
        if concern_desc:
            story.append(Paragraph(concern_desc, body_s))
        else:
            story.append(Paragraph("<i>No site concerns recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 9. Report Pictures ────────────────────────────────────────────
        def _unique_photos(items: Any) -> list[Any]:
            if not isinstance(items, list):
                return []

            seen: set[str] = set()
            out: list[Any] = []
            for item in items:
                if isinstance(item, str):
                    key = item.strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    out.append(item)
                    continue

                if isinstance(item, dict):
                    key = str(
                        item.get("signed_url")
                        or item.get("url")
                        or item.get("public_url")
                        or item.get("file_path")
                        or ""
                    ).strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    out.append(item)
            return out

        picture_groups: dict[str, dict[str, Any]] = {}

        def _add_picture_group(title: str, values: Any, remarks: Any = None) -> None:
            photos = _unique_photos(values)
            if not photos:
                return
            normalized_remarks = str(remarks or "").strip()
            existing = picture_groups.get(title, {"photos": [], "remarks": ""})
            picture_groups[title] = {
                "photos": _unique_photos([*existing.get("photos", []), *photos]),
                "remarks": existing.get("remarks") or normalized_remarks,
            }

        concerns_pictures = (concerns or {}).get("pictures")
        site_pics: dict = data.get("sitePictures") or {}
        _add_picture_group("Site Concerns", concerns_pictures)
        _add_picture_group("Site Pictures", site_pics.get("pictures"))

        picture_categories = (
            site_pics.get("categories") if isinstance(site_pics, dict) else {}
        )
        if isinstance(picture_categories, dict):
            category_labels = {
                "siteViews": "Site - front, rear, left and right view",
                "siteAssets": "Site - rear cage, generators, Gates",
                "generatorControls": "Generators - PLC Gen A, Gen B, battery chargers Gen A and Gen B",
                "fireAndAircon": "Fire Panel, FM200 level display, Aircon controller",
                "rectifierAndOdf": "Display, rectifier B, display, ODF top half, ODF bottom half",
                "cabinetAlarms": "Cabinets with Alarms present",
            }
            for key, title in category_labels.items():
                category_data = picture_categories.get(key)
                if not isinstance(category_data, dict):
                    continue
                _add_picture_group(
                    title,
                    category_data.get("pictures"),
                    category_data.get("remarks"),
                )

        gen1_data: dict = data.get("gen1") or {}
        gen2_data: dict = data.get("gen2") or {}
        _add_picture_group("Generator 1 - Oil Level", gen1_data.get("oilLevelImages"))
        _add_picture_group("Generator 1 - Fuel Level", gen1_data.get("fuelLevelImages"))
        _add_picture_group("Generator 2 - Oil Level", gen2_data.get("oilLevelImages"))
        _add_picture_group("Generator 2 - Fuel Level", gen2_data.get("fuelLevelImages"))

        # The picture groups above are built from the report `data` fields, which
        # store each photo as its public `url` string. Every uploaded photo is
        # ALSO present in `attachments.files` (as a dict) with the same public
        # `url` plus a separate `signed_url`. Re-adding those files here duplicated
        # every photo in the PDF: the per-item dedup keys dicts on `signed_url`
        # first, so it never matched the public-url string already in the group.
        # Only surface attachment files whose image isn't already represented,
        # matching across every URL variant a file can carry.
        seen_photo_keys: set[str] = set()
        for group in picture_groups.values():
            for item in group.get("photos", []):
                if isinstance(item, str):
                    key = item.strip()
                    if key:
                        seen_photo_keys.add(key)
                elif isinstance(item, dict):
                    for field in ("signed_url", "url", "public_url", "file_path"):
                        val = item.get(field)
                        if val:
                            seen_photo_keys.add(str(val).strip())

        attachments = report.attachments if isinstance(report.attachments, dict) else {}
        # `files` is the canonical key (docs/report-schemas.md); `photos` is
        # the legacy mobile key still present on already-submitted reports.
        attachment_files = (
            attachments.get("files") or attachments.get("photos")
            if isinstance(attachments, dict)
            else []
        )
        if isinstance(attachment_files, list):
            for file_item in attachment_files:
                if not isinstance(file_item, dict):
                    continue
                variants = [
                    file_item.get(field)
                    for field in ("url", "signed_url", "public_url", "file_path")
                ]
                if any(v and str(v).strip() in seen_photo_keys for v in variants):
                    continue
                title = str(file_item.get("label") or "Additional Attachments")
                _add_picture_group(title, [file_item])

        if picture_groups:
            story.extend(
                self._repeater_section_header(
                    "9. Report Pictures", primary_hex, accent_hex
                )
            )
            photo_group_title_style = ParagraphStyle(
                "RpPhotoGroupTitle",
                parent=self.styles["Normal"],
                fontSize=10,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor(primary_hex),
                spaceBefore=4,
                spaceAfter=2,
            )
            for title, group in picture_groups.items():
                story.append(Paragraph(title, photo_group_title_style))
                remarks = str(group.get("remarks") or "").strip()
                if remarks:
                    story.append(Paragraph(escape(remarks), photo_note_style))
                self._render_photo_grid(group.get("photos", []), story, cols=3)
                story.append(Spacer(1, 3 * mm))


    # ── SHEQ safety checklists (SHEQ-CHECKLISTS-PLAN.md §1B) ────────────────

    _SHEQ_CHECKLIST_LABELS = {
        SheqChecklistType.VEHICLE_DAILY: "Company Vehicle Daily Checklist",
        SheqChecklistType.JOURNEY_MANAGEMENT: "Journey Management Plan",
        SheqChecklistType.DAILY_RISK_ASSESSMENT: "Daily Risk Assessment",
        SheqChecklistType.TECHNICIAN_MASTER_SAFETY: "Technician Master Safety & Operations Checklist",
    }

    def generate_sheq_checklist_pdf(
        self, submission: Any, site_name: str = "N/A", task_ref: str = "N/A"
    ) -> BytesIO:
        """One body renderer per checklist type behind a shared cover/header,
        following the same pipeline as `generate_report_pdf`. `submission` is
        a `SheqSubmission` ORM instance (typed `Any` to avoid importing the
        SQLModel table class into this module's TYPE_CHECKING-free surface)."""
        buffer = BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=f"SHEQ_{submission.checklist_type.value}_{submission.id}",
        )

        story: list = []
        title = self._SHEQ_CHECKLIST_LABELS.get(submission.checklist_type, "SHEQ Checklist")

        technician_name = "N/A"
        try:
            if submission.technician and submission.technician.user:
                user = submission.technician.user
                technician_name = f"{user.name} {user.surname}"
        except Exception:
            pass

        status_display = submission.status.value.upper()
        performed_display = (
            submission.performed_on.isoformat() if submission.performed_on else "N/A"
        )
        generated_display = self._format_datetime(submission.created_at)
        # No dedicated SHEQ cover art exists yet — falls back to the "base"
        # palette/cover image via _cover_palette/_resolve_cover_image_path.
        primary_hex, accent_hex = self._cover_palette("base")

        story.extend(
            self._build_field_cover_page(
                report_type_label=title,
                title=title,
                site=site_name,
                subtitle=_FIELDCORE_REPORT_LABEL,
                descriptor=(
                    f"SHEQ safety checklist completed by {technician_name}. Structured "
                    "audit document for compliance review and archive."
                ),
                detail_items=[
                    ("Checklist Type", title),
                    ("Status", status_display),
                    ("Technician", technician_name),
                    ("Site", site_name),
                    ("Reference", task_ref),
                    ("Performed On", performed_display),
                    ("Generated", generated_display),
                ],
                generated_at=generated_display,
                accent_hex=accent_hex,
            )
        )
        story.extend(
            self._build_field_page_header(
                title=title,
                subtitle=f"{_FIELDCORE_REPORT_LABEL} | {performed_display}",
                accent_hex=accent_hex,
            )
        )
        story.extend(
            self._build_field_overview_cards(
                [
                    ("Status", status_display),
                    ("Technician", technician_name),
                    ("Site", site_name),
                    ("Performed On", performed_display),
                ],
                accent_hex=accent_hex,
            )
        )
        story.append(
            self._build_field_kv_table(
                [
                    ("Checklist Type", title),
                    ("Reference", task_ref),
                    ("Performed On", performed_display),
                ]
            )
        )
        story.append(Spacer(1, 6 * mm))

        dispatch = {
            SheqChecklistType.VEHICLE_DAILY: self._render_sheq_vehicle_daily_body,
            SheqChecklistType.JOURNEY_MANAGEMENT: self._render_sheq_journey_management_body,
            SheqChecklistType.DAILY_RISK_ASSESSMENT: self._render_sheq_daily_risk_assessment_body,
            SheqChecklistType.TECHNICIAN_MASTER_SAFETY: self._render_sheq_technician_master_safety_body,
        }
        renderer = dispatch.get(submission.checklist_type)
        if renderer:
            renderer(submission, story, primary_hex, accent_hex)

        story.append(Spacer(1, 24))
        story.append(self._create_divider())
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC | "
                f"Submission ID: {str(submission.id)[:8]}",
                self.styles["Footer"],
            )
        )

        doc.build(story)
        buffer.seek(0)
        return buffer

    def _render_sheq_signature_block(
        self,
        signatures: list[dict[str, Any]],
        roles: list[tuple[str, str]],
        story: list,
        primary_hex: str,
    ) -> None:
        """Render one signature per (role, display label) pair, in that fixed
        order. Typed fallbacks render in italic with the §7.3 disclaimer label
        — never presented as equivalent to a drawn mark."""
        label_style = ParagraphStyle(
            "SheqSigLabel", parent=self.styles["Normal"], fontSize=9,
            fontName="Helvetica-Bold", textColor=colors.HexColor("#2d3748"),
        )
        meta_style = ParagraphStyle(
            "SheqSigMeta", parent=self.styles["Normal"], fontSize=8,
            fontName="Helvetica", textColor=colors.HexColor("#718096"),
        )
        typed_style = ParagraphStyle(
            "SheqSigTyped", parent=self.styles["Normal"], fontSize=11,
            fontName="Helvetica-Oblique", textColor=colors.HexColor("#2d3748"),
        )

        by_role: dict[str, dict[str, Any]] = {}
        for record in signatures:
            role = record.get("role")
            if role and role not in by_role:
                by_role[role] = record

        for role_value, display_label in roles:
            record = by_role.get(role_value)
            story.append(Paragraph(display_label, label_style))
            if not record:
                story.append(Paragraph("Not yet signed", meta_style))
            elif record.get("method") == "typed":
                story.append(Paragraph(escape(str(record.get("typed_name") or "")), typed_style))
                story.append(Paragraph("Typed signature — no drawn mark captured", meta_style))
            else:
                url = self._get_media_source(record.get("file_ref") or {})
                buf = self._fetch_image_bytes(url) if url else None
                img = self._fit_photo_image(buf, 55 * mm, 22 * mm) if buf else None
                story.append(img if img is not None else Paragraph("<i>(signature image unavailable)</i>", meta_style))

            if record:
                signer = str(record.get("signer_name") or "")
                signed_at = str(record.get("signed_at") or "")
                meta_bits = [b for b in (signer, signed_at) if b]
                if meta_bits:
                    story.append(Paragraph(escape(" · ".join(meta_bits)), meta_style))
            story.append(Spacer(1, 4 * mm))

    def _render_sheq_vehicle_daily_body(
        self, submission: Any, story: list, primary_hex: str, accent_hex: str
    ) -> None:
        data = submission.data or {}

        story.extend(self._repeater_section_header("A. Pre-Trip Inspection", primary_hex, accent_hex))
        pre_trip = data.get("pre_trip") or {}
        section_data = {}
        polarity: dict[str, Any] = {}
        for key in VEHICLE_PRE_TRIP_LABELS:
            row = pre_trip.get(key) or {}
            section_data[key] = {"status": (row.get("status") or "").lower(), "issue": row.get("remarks")}
            polarity[key] = SimpleNamespace(bad_when="fault")
        story.extend(self._render_checklist_table(section_data, VEHICLE_PRE_TRIP_LABELS, primary_hex, polarity))

        vc = pre_trip.get("vehicle_clean") or {}
        story.extend(
            self._render_checklist_table(
                {"vehicle_clean": {"status": (vc.get("status") or "").lower(), "issue": vc.get("remarks")}},
                {"vehicle_clean": "Vehicle Clean (inside & outside)"},
                primary_hex,
                {"vehicle_clean": SimpleNamespace(bad_when="no")},
            )
        )
        story.append(Spacer(1, 4 * mm))

        story.extend(self._repeater_section_header("B. Post-Trip Check", primary_hex, accent_hex))
        post_trip = data.get("post_trip") or {}
        wl = post_trip.get("warning_lights_during_trip") or {}
        df = post_trip.get("damage_or_fault_noted") or {}
        story.extend(
            self._render_checklist_table(
                {
                    "warning_lights_during_trip": {
                        "status": (wl.get("status") or "").lower(), "issue": wl.get("remarks")
                    },
                    "damage_or_fault_noted": {
                        "status": (df.get("status") or "").lower(), "issue": df.get("remarks")
                    },
                },
                {
                    "warning_lights_during_trip": "Any Warning Lights During Trip",
                    "damage_or_fault_noted": "Damage or Fault Noted",
                },
                primary_hex,
                {
                    "warning_lights_during_trip": SimpleNamespace(bad_when="yes"),
                    "damage_or_fault_noted": SimpleNamespace(bad_when="yes"),
                },
            )
        )
        fuel = post_trip.get("fuel_used_refilled") or {}
        story.append(
            self._build_field_kv_table(
                [
                    ("Fuel Used / Refilled (litres)", self._text_value(fuel.get("liters"))),
                    ("Remarks", self._text_value(fuel.get("remarks"))),
                ]
            )
        )
        story.append(Spacer(1, 4 * mm))

        story.extend(self._repeater_section_header("C. Vehicle Parking Check", primary_hex, accent_hex))
        parking = data.get("parking") or {}
        vp = parking.get("vehicle_parked_securely") or {}
        story.extend(
            self._render_checklist_table(
                {"vehicle_parked_securely": {"status": (vp.get("status") or "").lower(), "issue": vp.get("remarks")}},
                {"vehicle_parked_securely": "Vehicle Parked Securely"},
                primary_hex,
                {"vehicle_parked_securely": SimpleNamespace(bad_when="no")},
            )
        )
        story.append(Spacer(1, 4 * mm))

        damage_photos = (submission.attachments or {}).get("damage_evidence")
        if damage_photos:
            story.extend(self._repeater_section_header("Damage Evidence", primary_hex, accent_hex))
            self._render_photo_grid(damage_photos, story, cols=3)
            story.append(Spacer(1, 4 * mm))

        story.extend(self._repeater_section_header("D. Sign-off", primary_hex, accent_hex))
        self._render_sheq_signature_block(
            submission.signatures or [],
            [("driver", "Driver Signature"), ("supervisor", "Supervisor Signature")],
            story,
            primary_hex,
        )

    def _render_sheq_journey_management_body(
        self, submission: Any, story: list, primary_hex: str, accent_hex: str
    ) -> None:
        data = submission.data or {}
        label_style = ParagraphStyle(
            "SheqJmpLabel", parent=self.styles["Normal"], fontSize=9,
            fontName="Helvetica-Bold", textColor=colors.HexColor("#2d3748"),
        )
        body_style = ParagraphStyle(
            "SheqJmpBody", parent=self.styles["Normal"], fontSize=9,
            fontName="Helvetica", textColor=colors.HexColor("#4a5568"), spaceAfter=6,
        )

        story.extend(self._repeater_section_header("Journey Details", primary_hex, accent_hex))
        story.append(
            self._build_field_kv_table(
                [
                    ("Full Name and Surname", self._text_value(data.get("full_name"))),
                    ("Vehicle Registration Number", self._text_value(data.get("vehicle_registration"))),
                    ("Journey from", self._text_value(data.get("journey_from"))),
                    ("Journey to", self._text_value(data.get("journey_to"))),
                    ("Via which locations", self._text_value(data.get("via_locations"))),
                    ("Estimated distance", self._text_value(data.get("estimated_distance_km"))),
                    ("Estimated driving time", self._text_value(data.get("estimated_driving_time_hrs"))),
                ]
            )
        )
        story.append(Spacer(1, 4 * mm))

        story.extend(self._repeater_section_header("Risk Screening", primary_hex, accent_hex))
        story.append(
            self._build_field_kv_table(
                [
                    ("Will total hours exceed 9hrs? (Y/N)", self._text_value(data.get("exceeds_9hrs"))),
                    (
                        "Will combined driving and working time exceed 12hrs? (Y/N)",
                        self._text_value(data.get("exceeds_12hrs_combined")),
                    ),
                    (
                        "Significant security/medical-response risk? (Y/N)",
                        self._text_value(data.get("security_or_medical_risk")),
                    ),
                ]
            )
        )
        alt = data.get("alternative_arrangements")
        if alt:
            story.append(Paragraph("Alternative travel arrangements / overnight rest location:", label_style))
            story.append(Paragraph(escape(str(alt)), body_style))
        story.append(Spacer(1, 4 * mm))

        routes = data.get("routes") or []
        if routes:
            story.extend(self._repeater_section_header("Primary Route/s & Rest Stops", primary_hex, accent_hex))
            table_data: list = [["Primary Route", "Rest Stops"]]
            for route in routes:
                table_data.append(
                    [self._text_value(route.get("primary_route")), self._text_value(route.get("rest_stops"))]
                )
            routes_tbl = Table(table_data, colWidths=[85 * mm, 85 * mm])
            routes_tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary_hex)),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.append(routes_tbl)
            story.append(Spacer(1, 4 * mm))

        if data.get("locations_to_avoid"):
            story.append(
                Paragraph(
                    "Locations to be avoided or where extra precautions are to be taken:", label_style
                )
            )
            story.append(Paragraph(escape(str(data["locations_to_avoid"])), body_style))
        if data.get("additional_risk_reduction_measures"):
            story.append(Paragraph("Additional Risk Reduction Measures:", label_style))
            story.append(Paragraph(escape(str(data["additional_risk_reduction_measures"])), body_style))
        story.append(Spacer(1, 4 * mm))

        story.extend(self._repeater_section_header("Authorisation & Sign-off", primary_hex, accent_hex))
        email_ref = data.get("email_ack_reference")
        if email_ref:
            story.append(
                self._build_field_kv_table(
                    [
                        ("Supervisor authorisation", "Email acknowledgement"),
                        ("Reference", self._text_value(email_ref)),
                        ("Acknowledged at", self._text_value(data.get("email_ack_at"))),
                    ]
                )
            )
        else:
            self._render_sheq_signature_block(
                submission.signatures or [], [("supervisor", "Supervisor Authorisation")], story, primary_hex
            )
        self._render_sheq_signature_block(
            submission.signatures or [], [("driver", "Journey Completed — Driver Signature")], story, primary_hex
        )
        story.append(
            self._build_field_kv_table(
                [("Updated JMP required?", self._text_value(data.get("updated_jmp_required")))]
            )
        )

    def _render_sheq_daily_risk_assessment_body(
        self, submission: Any, story: list, primary_hex: str, accent_hex: str
    ) -> None:
        data = submission.data or {}
        ack_style = ParagraphStyle(
            "SheqDraAck", parent=self.styles["Normal"], fontSize=9,
            fontName="Helvetica-Oblique", textColor=colors.HexColor("#4a5568"), spaceAfter=8,
        )

        story.extend(self._repeater_section_header("On-Site Risk Assessment", primary_hex, accent_hex))
        story.append(
            self._build_field_kv_table(
                [
                    ("Supervisor", self._text_value(data.get("supervisor_name"))),
                    ("Site", self._text_value(data.get("site"))),
                    ("Task to be done", self._text_value(data.get("task_to_be_done"))),
                ]
            )
        )
        hazards = data.get("hazards") or []
        if hazards:
            table_data: list = [["Hazard", "Action Taken", "Toolbox Talk Discussed"]]
            for hazard in hazards:
                table_data.append(
                    [
                        self._text_value(hazard.get("hazard")),
                        self._text_value(hazard.get("action_taken")),
                        self._text_value(hazard.get("toolbox_talk_discussed")),
                    ]
                )
            hazards_tbl = Table(table_data, colWidths=[60 * mm, 70 * mm, 40 * mm])
            hazards_tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary_hex)),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.append(hazards_tbl)
        story.append(Spacer(1, 4 * mm))

        matrix = data.get("checklist_matrix") or {}
        for group_key, items in DAILY_RISK_MATRIX.items():
            group_title = DAILY_RISK_MATRIX_GROUP_TITLES.get(group_key, group_key)
            story.extend(self._repeater_section_header(group_title, primary_hex, accent_hex))
            group_data = matrix.get(group_key) or {}
            section_data = {}
            polarity: dict[str, Any] = {}
            for item_key in items:
                entry = group_data.get(item_key) or {}
                section_data[item_key] = {
                    "status": (entry.get("answer") or "").lower(), "issue": entry.get("comments")
                }
                polarity[item_key] = SimpleNamespace(bad_when="no")
            story.extend(self._render_checklist_table(section_data, items, primary_hex, polarity))
            story.append(Spacer(1, 3 * mm))

        roster = data.get("roster") or []
        if roster:
            story.extend(self._repeater_section_header("Personnel Roster & PPE", primary_hex, accent_hex))
            roster_signatures = {
                s.get("roster_index"): s
                for s in (submission.signatures or [])
                if s.get("role") == "employee"
            }
            for index, row in enumerate(roster):
                ppe = row.get("ppe") or {}
                block: list = [
                    self._build_field_kv_table(
                        [
                            ("Employee", self._text_value(row.get("employee_name"))),
                            ("Reflector Vest", "Yes" if ppe.get("reflector_vest") else "No"),
                            ("Safety Shoes", "Yes" if ppe.get("safety_shoes") else "No"),
                            ("Gloves", "Yes" if ppe.get("gloves") else "No"),
                        ]
                    )
                ]
                signature = roster_signatures.get(index)
                if signature and signature.get("method") == "typed":
                    block.append(Paragraph(f"Signed: {escape(str(signature.get('typed_name') or ''))}", ack_style))
                elif signature:
                    url = self._get_media_source(signature.get("file_ref") or {})
                    buf = self._fetch_image_bytes(url) if url else None
                    img = self._fit_photo_image(buf, 40 * mm, 16 * mm) if buf else None
                    if img is not None:
                        block.append(img)
                story.append(KeepTogether(block))
                story.append(Spacer(1, 2 * mm))

        story.extend(self._repeater_section_header("Supervisor Acknowledgement", primary_hex, accent_hex))
        story.append(
            Paragraph(
                "I hereby acknowledge that the daily risk assessment and toolbox talk has been "
                "communicated to all personnel on Site.",
                ack_style,
            )
        )
        self._render_sheq_signature_block(
            submission.signatures or [], [("supervisor", "Supervisor Signature")], story, primary_hex
        )

    def _render_sheq_technician_master_safety_body(
        self, submission: Any, story: list, primary_hex: str, accent_hex: str
    ) -> None:
        data = submission.data or {}
        attachments = submission.attachments or {}
        na_style = ParagraphStyle(
            "SheqMasterNA", parent=self.styles["Normal"], fontSize=9,
            fontName="Helvetica-Oblique", textColor=colors.HexColor("#718096"),
        )
        sections = data.get("sections") or {}

        for section_key, rows in MASTER_SAFETY_SECTIONS.items():
            title = MASTER_SAFETY_SECTION_TITLES.get(section_key, section_key)
            section = sections.get(section_key) or {}
            story.extend(self._repeater_section_header(title, primary_hex, accent_hex))

            if section.get("not_applicable"):
                story.append(Paragraph("N/A — not applicable to this technician's trade", na_style))
                story.append(Spacer(1, 3 * mm))
                continue

            section_rows = section.get("rows") or {}
            section_data = {}
            polarity: dict[str, Any] = {}
            for row_id in rows:
                row = section_rows.get(row_id) or {}
                section_data[row_id] = {
                    "status": (row.get("decision") or "").lower(), "issue": row.get("comments")
                }
                polarity[row_id] = SimpleNamespace(bad_when="no-go")
            story.extend(self._render_checklist_table(section_data, rows, primary_hex, polarity))

            photos_for_section: list = []
            for slot_key, slot_label in MASTER_SAFETY_PHOTO_SLOTS.items():
                slot_photos = attachments.get(f"{section_key}.{slot_key}") or []
                for photo in slot_photos:
                    if isinstance(photo, dict) and not photo.get("label"):
                        photo = {**photo, "label": slot_label}
                    photos_for_section.append(photo)
            if photos_for_section:
                self._render_photo_grid(photos_for_section, story, cols=4)
            story.append(Spacer(1, 4 * mm))

        story.extend(self._repeater_section_header("Final Go / No-Go Decision", primary_hex, accent_hex))
        story.append(
            self._build_field_kv_table(
                [
                    ("Decision", self._text_value(data.get("overall_decision"))),
                    ("Reason (if No-Go)", self._text_value(data.get("no_go_reason"))),
                ]
            )
        )
        self._render_sheq_signature_block(
            submission.signatures or [],
            [("technician", "Technician Signature"), ("supervisor", "Supervisor Signature")],
            story,
            primary_hex,
        )


def get_pdf_service() -> PDFService:
    return PDFService()
