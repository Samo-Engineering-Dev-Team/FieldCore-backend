import urllib.request
from urllib.parse import urlparse, unquote
from io import BytesIO
from datetime import datetime
from typing import Any
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart

from loguru import logger as LOG
from app.core.settings import app_settings
from app.models import Report
from app.utils.enums import ReportType

# Imported lazily inside generate_incident_report_pdf to avoid circular import at module load
# from app.models import IncidentReport

# ── Incident report palette (module-level so all incident methods share them) ──
_INC_PRIMARY    = "#7f1d1d"   # deep red — accent text and dividers
_INC_LIGHT_RED  = "#fee2e2"   # light red — accent bands, heading highlight
_INC_LIGHT_BG   = "#fef2f2"   # very pale red — sidebar strip
_INC_CHARCOAL   = "#1a1a1a"   # near-black — massive cover title
_INC_WARM_GRAY  = "#4a5568"   # warm gray — body text
_INC_LIGHT_GRAY = "#718096"   # light gray — running header, labels, captions
_INC_DIVIDER    = "#e2e8f0"   # very light gray — thin separator lines
_INC_DARK_LABEL = "#2d3748"   # dark gray — metadata values


class PDFService:
    """Service for generating PDF documents from reports."""

    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
        self.assets_path = Path(__file__).parent.parent / "assets"
        self.supabase_url = (app_settings.SUPABASE_URL or "").rstrip("/")
        self.supabase_service_key = app_settings.SUPABASE_SERVICE_KEY or ""
        self.supabase_bucket = app_settings.SUPABASE_STORAGE_BUCKET or "attachments"
        backend_root = Path(__file__).resolve().parents[2]
        workspace_root = backend_root.parent
        self.cover_search_paths = [
            self.assets_path / "Report" / "coverpages",
            self.assets_path / "Report Cover Pages",
            backend_root / "assets" / "Report" / "coverpages",
            backend_root / "assets" / "Report Cover Pages",
            workspace_root / "seacom-app-frontend" / "src" / "assets" / "Report Cover Pages",
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
        }
        self._first_page_bg_image: Path | None = None
        self._first_page_bg_primary: str = "#0b2265"
        self._first_page_bg_accent: str = "#1a365d"

    def _setup_custom_styles(self):
        """Setup custom paragraph styles for professional PDF design."""
        # Header style with centered alignment
        self.styles.add(ParagraphStyle(
            name='CompanyHeader',
            parent=self.styles['Normal'],
            fontSize=24,
            textColor=colors.HexColor('#0b2265'),
            spaceAfter=4,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))

        # Report title with centered alignment
        self.styles.add(ParagraphStyle(
            name='ReportTitle',
            parent=self.styles['Heading1'],
            fontSize=18,
            spaceAfter=8,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#1a365d'),
            fontName='Helvetica-Bold',
            spaceBefore=12
        ))

        # Section header with centered alignment and rounded effect via styling
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=12,
            spaceBefore=14,
            spaceAfter=10,
            textColor=colors.HexColor('#ffffff'),
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            backColor=colors.HexColor('#1a365d')
        ))

        # Field label
        self.styles.add(ParagraphStyle(
            name='FieldLabel',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#4a5568'),
            spaceAfter=2,
            fontName='Helvetica-Bold'
        ))

        # Field value
        self.styles.add(ParagraphStyle(
            name='FieldValue',
            parent=self.styles['Normal'],
            fontSize=10,
            spaceAfter=6,
            textColor=colors.HexColor('#2d3748'),
            fontName='Helvetica'
        ))

        # Footer style
        self.styles.add(ParagraphStyle(
            name='Footer',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#718096'),
            alignment=TA_CENTER,
            spaceBefore=20
        ))

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
        }
        return palettes.get(cover_key or "base", palettes["base"])

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

    def _draw_first_page_background(self, canv: canvas.Canvas, doc: SimpleDocTemplate) -> None:
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
            subtitle: Smaller descriptor below title (e.g. "Severity: CRITICAL - SAMO TELECOMS x SEACOM")
            details:  List of [label, value] rows for the info table
        """
        elements = []
        primary_color, accent_color = self._cover_palette(cover_key)

        # Load logos
        samo_logo = seacom_logo = None
        try:
            p = self.assets_path / "samo-logo.png"
            if p.exists():
                samo_logo = Image(str(p), width=55 * mm, height=20 * mm)
        except Exception:
            pass
        try:
            p = self.assets_path / "seacom-logo.png"
            if p.exists():
                seacom_logo = Image(str(p), width=55 * mm, height=20 * mm)
        except Exception:
            pass

        # ── Blue header band (logos + brand name) ────────────────────────────
        brand_style = ParagraphStyle(
            'CoverBrand',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#e2e8f0'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
        )
        header_data = [[
            samo_logo or Paragraph("<b>SAMO</b>", self.styles['CompanyHeader']),
            Paragraph("SAMO TELECOMS &amp; SEACOM", brand_style),
            seacom_logo or Paragraph("<b>SEACOM</b>", self.styles['CompanyHeader']),
        ]]
        header_table = Table(header_data, colWidths=[60 * mm, 50 * mm, 60 * mm], rowHeights=[32 * mm])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(primary_color)),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 22 * mm))

        # ── Large report title ───────────────────────────────────────────────
        cover_title_style = ParagraphStyle(
            'CoverTitle',
            parent=self.styles['Normal'],
            fontSize=28,
            textColor=colors.HexColor('#ffffff'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            spaceAfter=6,
        )
        elements.append(Paragraph(title, cover_title_style))

        # ── Subtitle ─────────────────────────────────────────────────────────
        cover_sub_style = ParagraphStyle(
            'CoverSubtitle',
            parent=self.styles['Normal'],
            fontSize=13,
            textColor=colors.HexColor('#e2e8f0'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
        )
        elements.append(Paragraph(subtitle, cover_sub_style))
        elements.append(Spacer(1, 8 * mm))

        # ── Thin navy divider ─────────────────────────────────────────────────
        elements.append(self._create_divider(color_hex=accent_color))
        elements.append(Spacer(1, 8 * mm))

        # ── Details table ────────────────────────────────────────────────────
        if details:
            det_table = Table(details, colWidths=[55 * mm, 115 * mm])
            det_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f0f4f8')),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#2d3748')),
                ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#4a5568')),
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
                ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.HexColor('#ffffff'), colors.HexColor('#f7fafc')]),
            ]))
            elements.append(det_table)

        elements.append(Spacer(1, 30 * mm))

        # ── Confidentiality footer ────────────────────────────────────────────
        conf_style = ParagraphStyle(
            'CoverConf',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#e2e8f0'),
            alignment=TA_CENTER,
            fontName='Helvetica-Oblique',
        )
        elements.append(Paragraph(
            "CONFIDENTIAL - FOR SAMO TELECOMS AND SEACOM USE ONLY",
            conf_style,
        ))
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph(
            f"Generated {datetime.now().strftime('%d %B %Y %H:%M')} UTC",
            conf_style,
        ))

        # ── Start main content on page 2 ─────────────────────────────────────
        elements.append(PageBreak())
        return elements

    # ── Field reports ────────────────────────────────────────────────────────

    def generate_report_pdf(self, report: Report) -> BytesIO:
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
                rightMargin=20*mm,
                leftMargin=20*mm,
                topMargin=20*mm,
                bottomMargin=20*mm,
                title=f"Report_{report.report_type.value}_{report.id}"
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
                        cover_details.append(["Region", report.task.site.region.value.replace("-", " ").title()])
            except Exception:
                pass
            cover_details.append(["Generated", self._format_datetime(report.created_at)])
            report_cover_key = report.report_type.value if getattr(report, "report_type", None) else "base"
            primary_hex, accent_hex = self._cover_palette(report_cover_key)
            story.extend(self._build_cover_page(
                title=f"{report_type_display} Report",
                subtitle="Field Report - SAMO TELECOMS x SEACOM",
                details=cover_details,
                cover_key=report_cover_key,
            ))

            # ── Page 2: banner header ─────────────────────────────────────────
            story.extend(self._build_page_header(
                title=f"{report_type_display} Report",
                subtitle=f"Field Report - SAMO TELECOMS x SEACOM  |  {self._format_datetime(report.created_at)}",
                primary_hex=primary_hex,
                accent_hex=accent_hex,
            ))

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
                        meta_items.append(("Region", report.task.site.region.value.replace("-", " ").title()))
            except Exception:
                pass
            story.extend(self._build_metadata_cards(meta_items, primary_hex))

            # Report Data Section
            if report.data:
                if report.report_type == ReportType.REPEATER:
                    self._render_repeater_body(report, story, primary_hex, accent_hex)
                else:
                    story.extend(self._repeater_section_header("Report Details", primary_hex, accent_hex))
                    story.extend(self._render_report_data(report.data))

            # Attachments Section
            if report.attachments:
                story.append(Spacer(1, 16))
                story.extend(self._repeater_section_header("Attachments", primary_hex, accent_hex))

                attachment_data = [["Field Name", "Value"]]
                for key, value in report.attachments.items():
                    attachment_data.append([key, str(value)[:60]])

                if len(attachment_data) > 1:
                    att_table = Table(attachment_data, colWidths=[140, 330])
                    att_table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                        ('FONTNAME', (1, 1), (-1, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 0), (-1, -1), 9),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#ffffff')),
                        ('TEXTCOLOR', (1, 1), (-1, -1), colors.HexColor('#4a5568')),
                        ('LEFTPADDING', (0, 0), (-1, -1), 10),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                        ('TOPPADDING', (0, 0), (-1, -1), 6),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e0')),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#ffffff'), colors.HexColor('#f7fafc')]),
                    ]))
                    story.append(att_table)

            # Footer
            story.append(Spacer(1, 24))
            story.append(self._create_divider())
            story.append(Spacer(1, 8))
            story.append(Paragraph(
                f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC | "
                f"Report ID: {str(report.id)[:8]}",
                self.styles['Footer']
            ))

            # Build PDF
            self._configure_first_page_background(report_cover_key, primary_hex, accent_hex)
            try:
                doc.build(story, onFirstPage=self._draw_first_page_background)
            finally:
                self._clear_first_page_background()

        except Exception as e:
            raise

        buffer.seek(0)
        return buffer

    # ── Incident reports ─────────────────────────────────────────────────────

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
                return path[len(prefix):]

        return None

    def _fetch_supabase_image_bytes(self, file_path: str) -> BytesIO | None:
        """Download an image from Supabase authenticated endpoint with service key."""
        if not (self.supabase_url and self.supabase_service_key and file_path):
            return None

        auth_url = f"{self.supabase_url}/storage/v1/object/authenticated/{self.supabase_bucket}/{file_path.lstrip('/')}"
        try:
            req = urllib.request.Request(
                auth_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Authorization": f"Bearer {self.supabase_service_key}",
                    "apikey": self.supabase_service_key,
                },
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                return BytesIO(resp.read())
        except Exception:
            return None

    def _fetch_image_bytes(self, url: str) -> BytesIO | None:
        """Download an image and support Supabase private storage fallbacks."""
        if not url:
            return None

        # 1) Direct URL fetch first (works for signed/public links and standard URLs).
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return BytesIO(resp.read())
        except Exception:
            pass

        # 2) Supabase private bucket fallback using service key.
        file_path = self._extract_supabase_file_path(url)
        if file_path:
            fallback = self._fetch_supabase_image_bytes(file_path)
            if fallback:
                return fallback

        LOG.debug("pdf_image_fetch_failed source={}", url[:200])
        return None

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
        section_divider.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 1.4, colors.HexColor(accent_hex)),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(section_title)
        elements.append(section_divider)
        elements.append(Spacer(1, 2 * mm))

        safe_body = (body or "").strip() or "<i>Not provided.</i>"
        body_para = Paragraph(safe_body, body_style)
        body_table = Table([[body_para]], colWidths=[170 * mm])
        body_table.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (0, 0), 10),
            ("RIGHTPADDING", (0, 0), (0, 0), 10),
            ("TOPPADDING", (0, 0), (0, 0), 9),
            ("BOTTOMPADDING", (0, 0), (0, 0), 9),
            ("BACKGROUND", (0, 0), (0, 0), colors.white),
            ("BOX", (0, 0), (0, 0), 0.7, colors.HexColor("#e4ebf9")),
        ]))
        elements.append(body_table)
        elements.append(Spacer(1, 5 * mm))
        return elements

    # ── Incident report: Operations-Report-style cover page ───────────────────

    def _build_incident_cover_page(
        self,
        seacom_ref: str,
        site: str,
        technician: str,
        severity: str,
        report_date: str,
        report_date_obj: "datetime | None" = None,
    ) -> list:
        """
        Build a dark-background-compatible cover page matching the
        SEACOM Operations Report style: logos + pill badge top bar, large white
        title, frosted info boxes at the bottom.
        """
        elements = []

        # ── Local styles (all white text — canvas dark background shows through) ─
        wh = "#ffffff"
        teal_lbl = "#63b3ed"   # label text in info boxes
        box_fill  = "#dce8f5"  # frosted info box fill (light on dark bg)
        box2_fill = "#1e3a5f"  # darker confidentiality box
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

        # ── 2. Top bar: SAMO logo | "INCIDENT REPORT" pill | SEACOM logo ─────
        samo_logo = seacom_logo = None
        try:
            p = self.assets_path / "samo-logo.png"
            if p.exists():
                samo_logo = Image(str(p), width=40 * mm, height=15 * mm)
        except Exception:
            pass
        try:
            p = self.assets_path / "seacom-logo.png"
            if p.exists():
                seacom_logo = Image(str(p), width=45 * mm, height=16 * mm)
        except Exception:
            pass

        # Pill badge (nested table with white border)
        pill_inner = Table(
            [[Paragraph("INCIDENT  REPORT", badge_s)]],
            colWidths=[70 * mm],
        )
        pill_inner.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(badge_bdr)),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))

        top_bar = Table(
            [[
                samo_logo or Paragraph("<b>SAMO</b>", fb_s),
                pill_inner,
                seacom_logo or Paragraph("<b>SEACOM</b>", fb_r_s),
            ]],
            colWidths=[50 * mm, 70 * mm, 50 * mm],
        )
        top_bar.setStyle(TableStyle([
            ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ("ALIGN", (1, 0), (1, 0), "CENTER"),
            ("ALIGN", (2, 0), (2, 0), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(top_bar)

        # ── 3. Vertical space before title ───────────────────────────────────
        elements.append(Spacer(1, 30 * mm))

        # ── 4. Brand line ─────────────────────────────────────────────────────
        elements.append(Paragraph("SAMO  TELECOMS    \u00d7    SEACOM", brand_s))
        elements.append(Spacer(1, 4 * mm))

        # ── 5. Large title ────────────────────────────────────────────────────
        elements.append(Paragraph("Post-Resolution", title1_s))
        elements.append(Paragraph("Incident Report", title2_s))
        elements.append(Spacer(1, 4 * mm))

        # ── 6. Subtitle: severity | site ─────────────────────────────────────
        elements.append(Paragraph(f"{severity}  |  {site}", subtitle_s))
        elements.append(Spacer(1, 28 * mm))

        # ── 7. Frosted info box ────────────────────────────────────────────────
        generated_ts = datetime.now().strftime("%d %B %Y  %H:%M UTC")
        info_data = [
            [
                [Paragraph("INCIDENT REF", lbl_s), Paragraph(seacom_ref, val_s)],
                [Paragraph("SITE", lbl_s), Paragraph(site, val_s)],
            ],
            [
                [Paragraph("TECHNICIAN", lbl_s), Paragraph(technician, val_s)],
                [Paragraph("SEVERITY", lbl_s), Paragraph(severity, val_s)],
            ],
            [
                [Paragraph("REPORT DATE", lbl_s), Paragraph(report_date, val_s)],
                [Paragraph("GENERATED", lbl_s), Paragraph(generated_ts, val_s)],
            ],
        ]
        info_box = Table(info_data, colWidths=[85 * mm, 85 * mm])
        info_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(box_fill)),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(badge_bdr)),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8d4f0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(info_box)
        elements.append(Spacer(1, 3 * mm))

        # ── 8. Confidentiality footer box ─────────────────────────────────────
        conf_box = Table(
            [[Paragraph(
                f"Ref: {seacom_ref}  \u2014  Confidential, Samo Engineering and SEACOM internal use.",
                conf_s,
            )]],
            colWidths=[170 * mm],
        )
        conf_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(box2_fill)),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(conf_box)

        elements.append(PageBreak())
        return elements

    # ── Incident report: dark navy running header bar ─────────────────────────

    def _build_incident_running_header(self, date_str: str) -> list:
        """Build a full-width dark navy header bar matching the Operations Report style."""
        nav = "#1a365d"   # dark navy
        wh  = "#ffffff"

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
            [[
                Paragraph(f"INCIDENT REPORT  |  {date_str}", hdr_l),
                Paragraph("CONFIDENTIAL", hdr_r),
            ]],
            colWidths=[120 * mm, 50 * mm],
        )
        hdr_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(nav)),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return [hdr_tbl, Spacer(1, 8 * mm)]

    # ── Incident report: 4-card KPI row ───────────────────────────────────────

    def _build_incident_kpi_cards(
        self,
        site: str,
        seacom_ref: str,
        technician: str,
        severity: str,
    ) -> list:
        """Build a 4-card KPI row matching the Operations Report style."""
        nav  = "#1a365d"  # dark navy — values
        teal = "#2b6cb0"  # medium blue — left accent strip
        gray = "#718096"  # gray — labels
        bord = "#cbd5e0"  # border

        # Severity colour
        sev_upper = severity.upper()
        if "CRITICAL" in sev_upper:
            sev_color = "#c53030"
        elif "HIGH" in sev_upper:
            sev_color = "#d69e2e"
        elif "MINOR" in sev_upper or "LOW" in sev_upper:
            sev_color = "#276749"
        else:
            sev_color = nav

        lbl_s = ParagraphStyle(
            "IncKpiLbl_local",
            parent=self.styles["Normal"],
            fontSize=7.5,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(gray),
        )
        val_nav_s = ParagraphStyle(
            "IncKpiValNav_local",
            parent=self.styles["Normal"],
            fontSize=14,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(nav),
            leading=17,
        )
        val_sev_s = ParagraphStyle(
            "IncKpiValSev_local",
            parent=self.styles["Normal"],
            fontSize=14,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor(sev_color),
            leading=17,
        )

        cards = [
            [Paragraph("SITE", lbl_s), Paragraph(site, val_nav_s)],
            [Paragraph("INCIDENT REF", lbl_s), Paragraph(seacom_ref, val_nav_s)],
            [Paragraph("TECHNICIAN", lbl_s), Paragraph(technician, val_nav_s)],
            [Paragraph("SEVERITY", lbl_s), Paragraph(severity, val_sev_s)],
        ]

        tbl = Table([cards], colWidths=[42 * mm, 42 * mm, 43 * mm, 43 * mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (0, -1), 0.5, colors.HexColor(bord)),
            ("BOX", (1, 0), (1, -1), 0.5, colors.HexColor(bord)),
            ("BOX", (2, 0), (2, -1), 0.5, colors.HexColor(bord)),
            ("BOX", (3, 0), (3, -1), 0.5, colors.HexColor(bord)),
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(teal)),
            ("LINEBEFORE", (1, 0), (1, -1), 3, colors.HexColor(teal)),
            ("LINEBEFORE", (2, 0), (2, -1), 3, colors.HexColor(teal)),
            ("LINEBEFORE", (3, 0), (3, -1), 3, colors.HexColor(teal)),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return [tbl, Spacer(1, 7 * mm)]

    # ── Incident report: bordered narrative section box ───────────────────────

    def _build_incident_narrative_section(
        self,
        number: int,
        label: str,
        body: str | None,
    ) -> list:
        """
        Build a numbered narrative section matching the Operations Report
        bordered-box style: teal-accented header row + white body.
        """
        nav  = "#1a365d"  # dark navy — header label text
        teal = "#2b6cb0"  # medium blue — header bg, number, border top
        hdr_bg = "#ebf4ff"  # light teal-blue — header row fill
        body_c = "#2d3748"  # body text
        bord = "#cbd5e0"    # outer box border

        num_s = ParagraphStyle(
            f"IncNarNum{number}_local",
            parent=self.styles["Normal"],
            fontSize=13,
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
            leading=15,
        )

        safe_body = (body or "").strip() or "<i>Not provided.</i>"

        # Header row: number | section label
        hdr_row = Table(
            [[Paragraph(f"{number:02d}", num_s), Paragraph(label.upper(), head_s)]],
            colWidths=[16 * mm, 154 * mm],
        )
        hdr_row.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(hdr_bg)),
            ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.HexColor(teal)),
            ("LINEBELOW", (0, 0), (-1, 0), 1.0, colors.HexColor(teal)),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ]))

        # Body row
        body_row = Table(
            [[Paragraph(safe_body, body_s)]],
            colWidths=[170 * mm],
        )
        body_row.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))

        # Outer wrapper to apply the border box across both rows
        outer = Table(
            [[hdr_row], [body_row]],
            colWidths=[170 * mm],
        )
        outer.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(bord)),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

        return [outer, Spacer(1, 5 * mm)]

    def generate_incident_report_pdf(self, report: "IncidentReport") -> BytesIO:  # type: ignore[name-defined]
        """Generate a professional PDF for an incident report, matching the Operations Report style."""
        from app.models.incident_report import IncidentReport  # noqa: F401

        # ── Derived metadata ──────────────────────────────────────────────────
        inc_severity = str(getattr(report, "severity", "minor")).upper() \
            if hasattr(report, "severity") else "N/A"
        seacom_ref = getattr(report, "seacom_ref", None) \
            or str(report.incident_id)[:8].upper()
        report_date_str = (
            report.report_date.strftime("%d %B %Y").upper()
            if report.report_date else "N/A"
        )

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=18 * mm,
            bottomMargin=20 * mm,
            title=f"Incident_Report_{report.id}",
        )

        story: list = []

        # ── Page 1: cover page ────────────────────────────────────────────────
        story.extend(self._build_incident_cover_page(
            seacom_ref=seacom_ref,
            site=report.site_name or "N/A",
            technician=report.technician_name or "N/A",
            severity=inc_severity,
            report_date=report_date_str,
            report_date_obj=report.report_date,
        ))

        # ── Page 2+: dark navy running header bar ─────────────────────────────
        story.extend(self._build_incident_running_header(report_date_str))

        # ── Section heading: INCIDENT SUMMARY ────────────────────────────────
        sec_head_s = ParagraphStyle(
            "IncSecHead_local",
            parent=self.styles["Normal"],
            fontSize=26,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1a365d"),
            leading=30,
        )
        sec_head_tbl = Table(
            [[Paragraph("INCIDENT SUMMARY", sec_head_s)]],
            colWidths=[170 * mm],
        )
        sec_head_tbl.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 1.5, colors.HexColor("#2b6cb0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(sec_head_tbl)
        story.append(Spacer(1, 6 * mm))

        # ── 4-card KPI row ────────────────────────────────────────────────────
        story.extend(self._build_incident_kpi_cards(
            site=report.site_name or "N/A",
            seacom_ref=seacom_ref,
            technician=report.technician_name or "N/A",
            severity=inc_severity,
        ))

        # ── Narrative sections ────────────────────────────────────────────────
        narrative_sections = [
            (1, "Introduction",        report.introduction),
            (2, "Problem Statement",   report.problem_statement),
            (3, "Findings on Site",    report.findings),
            (4, "Actions Taken",       report.actions_taken),
            (5, "Root Cause Analysis", report.root_cause_analysis),
            (6, "Conclusion",          report.conclusion),
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

        photo_buffers: list[tuple[str, BytesIO]] = []
        for photo in photos_raw:
            url = photo.get("url") or photo.get("public_url")
            if not url:
                continue
            buf = self._fetch_image_bytes(url)
            if buf:
                photo_buffers.append((photo.get("original_name") or "Photo", buf))

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
                [[Paragraph("07", _ph_num_s), Paragraph(
                    f"SITE PHOTOS  \u2014  {count_label} IMAGE{'S' if count_label != 1 else ''}",
                    _ph_lbl_s,
                )]],
                colWidths=[16 * mm, 154 * mm],
            )
            _ph_hdr.setStyle(TableStyle([
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
            ]))
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

            rows = [photo_buffers[i: i + COLS] for i in range(0, len(photo_buffers), COLS)]
            for row_items in rows:
                img_row: list = []
                cap_row: list = []
                for name, buf in row_items:
                    try:
                        buf.seek(0)
                        img = Image(ImageReader(buf), width=PHOTO_W, height=PHOTO_H)
                        img.hAlign = "CENTER"
                        img_row.append(img)
                        cap_row.append(Paragraph(name[:35], caption_s))
                    except Exception:
                        img_row.append(Paragraph("<i>(unavailable)</i>", caption_s))
                        cap_row.append(Paragraph("", caption_s))

                while len(img_row) < COLS:
                    img_row.append(Spacer(PHOTO_W, PHOTO_H))
                    cap_row.append(Paragraph("", caption_s))

                col_widths = [PHOTO_W + 2 * mm] * COLS
                img_table = Table([img_row], colWidths=col_widths)
                img_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor(_INC_DIVIDER)),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor(_INC_DIVIDER)),
                ]))
                story.append(img_table)

                cap_table = Table([cap_row], colWidths=col_widths)
                cap_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]))
                story.append(cap_table)

            story.append(Spacer(1, 4 * mm))

        # ── Signature block: PREPARED BY / APPROVED BY ───────────────────────
        story.append(Spacer(1, 8 * mm))

        sig_hdr_s = ParagraphStyle(
            "IncSigHdr_local",
            parent=self.styles["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#ffffff"),
        )
        sig_name_s = ParagraphStyle(
            "IncSigName_local",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1a365d"),
            spaceAfter=4,
        )
        sig_line_s = ParagraphStyle(
            "IncSigLine_local",
            parent=self.styles["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#4a5568"),
        )
        sig_table = Table(
            [[
                [
                    Paragraph("PREPARED BY", sig_hdr_s),
                    Spacer(1, 4),
                    Paragraph(report.technician_name or "N/A", sig_name_s),
                    Paragraph("Signature:  _________________________________", sig_line_s),
                    Spacer(1, 4),
                    Paragraph("Date:  _____  /  _____  /  __________", sig_line_s),
                ],
                [
                    Paragraph("APPROVED BY", sig_hdr_s),
                    Spacer(1, 4),
                    Paragraph("___________________________", sig_name_s),
                    Paragraph("Signature:  _________________________________", sig_line_s),
                    Spacer(1, 4),
                    Paragraph("Date:  _____  /  _____  /  __________", sig_line_s),
                ],
            ]],
            colWidths=[85 * mm, 85 * mm],
        )
        sig_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#2b6cb0")),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#2b6cb0")),
            ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#cbd5e0")),
            ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#cbd5e0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.append(sig_table)

        # ── Footer ────────────────────────────────────────────────────────────
        story.append(Spacer(1, 8 * mm))
        story.append(self._create_divider(color_hex="#cbd5e0"))
        story.append(Spacer(1, 3))
        footer_s = ParagraphStyle(
            "IncFooter_local",
            parent=self.styles["Normal"],
            fontSize=7,
            fontName="Helvetica",
            textColor=colors.HexColor("#718096"),
            alignment=TA_CENTER,
        )
        story.append(Paragraph(
            f"CONFIDENTIAL \u2014 SAMO TELECOMS \u00d7 SEACOM  \u2502  "
            f"Generated {datetime.now().strftime('%d %B %Y %H:%M')} UTC  \u2502  "
            f"Ref: {seacom_ref}",
            footer_s,
        ))

        self._configure_first_page_background("incident", _INC_PRIMARY, "#991b1b")
        try:
            doc.build(story, onFirstPage=self._draw_first_page_background)
        finally:
            self._clear_first_page_background()
        buffer.seek(0)
        return buffer

    # ── Executive summary PDF (management) ───────────────────────────────────

    def generate_executive_summary_pdf(
        self,
        month_label: str,
        sla_compliance: float,
        total_incidents: int,
        total_tasks: int,
        monthly_incidents: list[dict],   # [{month: str, count: int}]
        technician_performance: list[dict],  # [{name: str, incidents: int, tasks: int}]
        regional_performance: list[dict],    # [{region: str, compliance: float}]
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
        story.extend(self._build_cover_page(
            title="Executive Management Report",
            subtitle=f"{month_label} - SAMO TELECOMS x SEACOM",
            details=cover_details,
            cover_key="executive",
        ))

        # ── Page 2: banner header ─────────────────────────────────────────
        exec_primary, exec_accent = self._cover_palette("executive")
        story.extend(self._build_page_header(
            title="Executive Management Report",
            subtitle=f"{month_label}  |  SAMO TELECOMS x SEACOM",
            primary_hex=exec_primary,
            accent_hex=exec_accent,
        ))

        # ── KPI summary row ───────────────────────────────────────────────────
        story.append(Paragraph("Key Performance Indicators", self.styles["SectionHeader"]))
        kpi_data = [
            ["Metric", "Value"],
            ["SLA Compliance", f"{sla_compliance:.1f}%"],
            ["Total Incidents", str(total_incidents)],
            ["Total Tasks", str(total_tasks)],
        ]
        kpi_table = Table(kpi_data, colWidths=[235, 235])
        kpi_table.setStyle(TableStyle([
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
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")]),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 16))

        # ── Monthly incident trend bar chart ──────────────────────────────────
        if monthly_incidents:
            story.append(Paragraph("Monthly Incident Trend (Last 6 Months)", self.styles["SectionHeader"]))
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
            bc.categoryAxis.categoryNames = [entry.get("month", "") for entry in monthly_incidents]
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
            story.append(Paragraph("Technician Activity (Incidents + Tasks)", self.styles["SectionHeader"]))
            story.append(Spacer(1, 8))

            names = [e.get("name", "Unknown")[:18] for e in technician_performance[:8]]
            totals = [e.get("incidents", 0) + e.get("tasks", 0) for e in technician_performance[:8]]

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
            story.append(Paragraph("Regional SLA Compliance", self.styles["SectionHeader"]))
            reg_data = [["Region", "Compliance %"]]
            for row in regional_performance:
                reg_data.append([
                    (row.get("region") or "N/A").replace("_", " ").title(),
                    f"{float(row.get('compliance') or 0):.1f}%",
                ])
            reg_table = Table(reg_data, colWidths=[300, 170])
            reg_table.setStyle(TableStyle([
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
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")]),
            ]))
            story.append(reg_table)
            story.append(Spacer(1, 16))

        # ── Footer ─────────────────────────────────────────────────────────────
        story.append(self._create_divider())
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC | "
            f"Executive Summary - {month_label}",
            self.styles["Footer"],
        ))

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
        [ samo logo | title + subtitle | seacom logo ]
        """
        samo_logo = seacom_logo = None
        try:
            p = self.assets_path / "samo-logo.png"
            if p.exists():
                samo_logo = Image(str(p), width=46 * mm, height=16 * mm)
        except Exception:
            pass
        try:
            p = self.assets_path / "seacom-logo.png"
            if p.exists():
                seacom_logo = Image(str(p), width=46 * mm, height=16 * mm)
        except Exception:
            pass

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
            [[
                samo_logo or Paragraph("<b>SAMO</b>", fallback_s),
                [Paragraph(title, title_s), Spacer(1, 2), Paragraph(subtitle, sub_s)],
                seacom_logo or Paragraph("<b>SEACOM</b>", fallback_s),
            ]],
            colWidths=[46 * mm, 78 * mm, 46 * mm],
        )
        banner.setStyle(TableStyle([
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
        ]))
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
            table_data.append([
                Paragraph(left_lbl.upper(), label_s),
                Paragraph(right_lbl.upper() if right_lbl else "", label_s),
            ])
            style_cmds += [
                ("BACKGROUND", (0, row_i), (-1, row_i), colors.HexColor("#eef2f8")),
                ("TOPPADDING", (0, row_i), (-1, row_i), 8),
                ("BOTTOMPADDING", (0, row_i), (-1, row_i), 2),
            ]
            row_i += 1

            # Value row
            table_data.append([
                Paragraph(left_val or "N/A", val_s),
                Paragraph(right_val or "", val_s),
            ])
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
        divider = Table([[""],], colWidths=[470])
        divider.setStyle(TableStyle([
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.HexColor(color_hex)),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return divider

    def _format_report_type(self, report_type: ReportType) -> str:
        """Format report type enum to display string."""
        return report_type.value.replace("-", " ").title()

    def _format_datetime(self, dt: datetime | None) -> str:
        """Format datetime to display string."""
        if dt is None:
            return "N/A"
        return dt.strftime("%Y-%m-%d %H:%M:%S")

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
                elements.append(Paragraph(
                    f"{indent}<b>{formatted_key}:</b>",
                    self.styles['FieldValue']
                ))
                elements.extend(self._render_report_data(value, level + 1))
            elif isinstance(value, list):
                elements.append(Paragraph(
                    f"{indent}<b>{formatted_key}:</b>",
                    self.styles['FieldValue']
                ))
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        elements.append(Paragraph(
                            f"{indent}    Item {i + 1}:",
                            self.styles['FieldValue']
                        ))
                        elements.extend(self._render_report_data(item, level + 2))
                    else:
                        elements.append(Paragraph(
                            f"{indent}    - {item}",
                            self.styles['FieldValue']
                        ))
            elif isinstance(value, bool):
                display_value = "Yes" if value else "No"
                elements.append(Paragraph(
                    f"{indent}<b>{formatted_key}:</b> {display_value}",
                    self.styles['FieldValue']
                ))
            else:
                display_value = str(value) if value is not None else "N/A"
                elements.append(Paragraph(
                    f"{indent}<b>{formatted_key}:</b> {display_value}",
                    self.styles['FieldValue']
                ))

        return elements


    # ── Repeater report rendering ─────────────────────────────────────────────

    def _repeater_section_header(self, title: str, primary_hex: str, accent_hex: str) -> list:
        """Render a styled section header for repeater report sections."""
        badge_style = ParagraphStyle(
            "RptSecBadge",
            parent=self.styles["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=colors.white,
        )
        header = Table([[Paragraph(title, badge_style)]], colWidths=[170 * mm])
        header.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(primary_hex)),
            ("LINEBELOW", (0, 0), (-1, -1), 2, colors.HexColor(accent_hex)),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        return [header, Spacer(1, 3 * mm)]

    def _render_checklist_table(
        self,
        section_data: dict[str, Any],
        label_map: dict[str, str],
        primary_hex: str = "#0e7490",
    ) -> list:
        """Render a dict of CheckWithIssue objects as a color-coded checklist table."""
        GREEN = "#166534"
        RED = "#991b1b"

        hdr_s = ParagraphStyle("CkH", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)
        lbl_s = ParagraphStyle("CkL", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#2d3748"))
        res_s = ParagraphStyle("CkR", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=colors.white)
        iss_s = ParagraphStyle("CkI", parent=self.styles["Normal"], fontSize=8, fontName="Helvetica-Oblique", textColor=colors.HexColor("#4a5568"))

        table_data: list = [[
            Paragraph("Check Item", hdr_s),
            Paragraph("Result", hdr_s),
            Paragraph("Issue / Notes", hdr_s),
        ]]
        pass_flags: list[bool] = []

        for key, value in section_data.items():
            label = label_map.get(key, key.replace("_", " ").title())
            if isinstance(value, dict):
                passed = bool(value.get("passed", True))
                issue = (value.get("issueDescription") or "").strip()
            elif isinstance(value, bool):
                passed = value
                issue = ""
            else:
                continue
            pass_flags.append(passed)
            result_text = "PASS" if passed else "FAIL"
            table_data.append([
                Paragraph(label, lbl_s),
                Paragraph(result_text, res_s),
                Paragraph(issue or "N/A", iss_s),
            ])

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
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")]),
        ]
        for i, passed in enumerate(pass_flags, start=1):
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

        lbl_s = ParagraphStyle("GnL", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", textColor=colors.HexColor("#2d3748"))
        val_s = ParagraphStyle("GnV", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#4a5568"))
        bool_s = ParagraphStyle("GnB", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=colors.white)

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
                table_data.append([Paragraph(field_label, lbl_s), Paragraph(text, bool_s)])
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
                table_data.append([Paragraph(field_label, lbl_s), Paragraph(f"{value}{unit}", val_s)])
            elif isinstance(value, str) and value:
                table_data.append([Paragraph(field_label, lbl_s), Paragraph(value, val_s)])

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
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")]),
        ]
        for row_i, color in bool_row_colors.items():
            style_cmds.append(("BACKGROUND", (1, row_i), (1, row_i), colors.HexColor(color)))

        tbl = Table(table_data, colWidths=[105 * mm, 65 * mm])
        tbl.setStyle(TableStyle(style_cmds))
        return [tbl]

    def _render_environmental_systems(self, env: dict[str, Any], primary_hex: str) -> list:
        """Render environmental systems (AC, fire, electric fence, alarms) as sub-tables."""
        elements: list = []

        sub_lbl = ParagraphStyle("EnvSL", parent=self.styles["Normal"], fontSize=10, fontName="Helvetica-Bold", textColor=colors.HexColor(primary_hex), spaceBefore=8, spaceAfter=3)
        kv_lbl = ParagraphStyle("EnvKL", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", textColor=colors.HexColor("#2d3748"))
        kv_val = ParagraphStyle("EnvKV", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#4a5568"))

        def _kv(rows: list[list[str]]) -> Table:
            t = Table(rows, colWidths=[100 * mm, 70 * mm])
            t.setStyle(TableStyle([
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
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")]),
            ]))
            return t

        def yn(v: Any) -> str:
            return "Yes" if v else "No"

        ac = env.get("airConditioning") or {}
        if ac:
            elements.append(Paragraph("Air Conditioning", sub_lbl))
            elements.append(_kv([
                ["Temperature", f"{ac.get('temperature', 'N/A')} deg C"],
                ["Cycle Setting", str(ac.get("cycleSetting") or "N/A")],
                ["Aircon Panel OK", yn(ac.get("airconPanelOk"))],
            ]))

        fire = env.get("fireSystem") or {}
        if fire:
            elements.append(Paragraph("Fire System", sub_lbl))
            elements.append(_kv([
                ["Fire Panel OK", yn(fire.get("firePanelOk"))],
                ["Fire Extinguisher Pressure OK", yn(fire.get("fireExtinguisherPressure"))],
            ]))

        fence = env.get("electricFence") or {}
        if fence:
            elements.append(Paragraph("Electric Fence", sub_lbl))
            elements.append(_kv([
                ["Energizer Functioning", yn(fence.get("energizerFunctioning"))],
                ["Fence Free from Debris", yn(fence.get("fenceFreeFromDebris"))],
                ["No Disturbed Wiring", yn(fence.get("noDisturbedWiring"))],
                ["Wire Tension Acceptable", yn(fence.get("wireTensionAcceptable"))],
                ["Alarm Test Confirmed", yn(fence.get("alarmTestConfirmed"))],
            ]))

        alarms = env.get("alarmsAndSensors") or {}
        if alarms:
            elements.append(Paragraph("Alarms &amp; Sensors", sub_lbl))
            elements.append(_kv([
                ["Door Alarms Tested (Front)", yn(alarms.get("doorAlarmsTestedFront"))],
                ["Door Alarms Tested (Rear)", yn(alarms.get("doorAlarmsTestedRear"))],
                ["Flood Sensors Tested (Front)", yn(alarms.get("floodSensorsTestedFront"))],
                ["Flood Sensors Tested (Rear)", yn(alarms.get("floodSensorsTestedRear"))],
            ]))

        return elements

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

        photo_buffers: list[tuple[str, BytesIO | None]] = []
        for photo in photos:
            if isinstance(photo, str):
                url, name = photo, "Photo"
            elif isinstance(photo, dict):
                url = (
                    photo.get("signed_url")
                    or photo.get("url")
                    or photo.get("public_url")
                    or photo.get("file_path")
                    or ""
                )
                name = photo.get("original_name") or photo.get("name") or "Photo"
            else:
                continue
            buf = self._fetch_image_bytes(url) if url else None
            photo_buffers.append((name, buf))

        col_widths = [PHOTO_W + 2 * mm] * cols
        for chunk in [photo_buffers[i: i + cols] for i in range(0, len(photo_buffers), cols)]:
            img_row: list = []
            cap_row: list = []
            for name, buf in chunk:
                if buf:
                    try:
                        buf.seek(0)
                        img = Image(ImageReader(buf), width=PHOTO_W, height=PHOTO_H)
                        img.hAlign = "CENTER"
                        img_row.append(img)
                    except Exception:
                        img_row.append(Paragraph("<i>(unavailable)</i>", caption_style))
                else:
                    img_row.append(Paragraph("<i>(unavailable)</i>", caption_style))
                cap_row.append(Paragraph((name or "")[:35], caption_style))

            while len(img_row) < cols:
                img_row.append(Spacer(PHOTO_W, PHOTO_H))
                cap_row.append(Paragraph("", caption_style))

            img_tbl = Table([img_row], colWidths=col_widths)
            img_tbl.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ]))
            story.append(img_tbl)

            cap_tbl = Table([cap_row], colWidths=col_widths)
            cap_tbl.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(cap_tbl)

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

        info_lbl_s = ParagraphStyle("RpInfoL", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica-Bold", textColor=colors.HexColor("#2d3748"))
        info_val_s = ParagraphStyle("RpInfoV", parent=self.styles["Normal"], fontSize=9, fontName="Helvetica", textColor=colors.HexColor("#4a5568"))
        body_s = ParagraphStyle("RpBody", parent=self.styles["Normal"], fontSize=10, fontName="Helvetica", textColor=colors.HexColor("#2d3748"), leading=16)

        def _info_table(rows: list[list[str]]) -> Table:
            t = Table(rows, colWidths=[80 * mm, 90 * mm])
            t.setStyle(TableStyle([
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
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#ffffff"), colors.HexColor("#f7fafc")]),
            ]))
            return t

        # ── 1. Routine Information ──────────────────────────────────────────
        story.extend(self._repeater_section_header("1. Routine Information", primary_hex, accent_hex))
        story.append(_info_table([
            ["Date Routine Performed", data.get("dateRoutinePerformed") or "N/A"],
            ["NOC Routine Ticket Reference", data.get("nocRoutineTicketReference") or "N/A"],
        ]))
        story.append(Spacer(1, 6 * mm))

        # ── 2 & 3. Generator Inspections ──────────────────────────────────
        for idx, (sec_title, gen_key) in enumerate([
            ("2. Generator 1 Inspection", "gen1"),
            ("3. Generator 2 Inspection", "gen2"),
        ], start=1):
            gen_data: dict = data.get(gen_key) or {}
            story.extend(self._repeater_section_header(sec_title, primary_hex, accent_hex))
            if gen_data:
                story.extend(self._render_generator_table(gen_data, sec_title, primary_hex, accent_hex))
            else:
                story.append(Paragraph("<i>No data recorded for this generator.</i>", body_s))
            story.append(Spacer(1, 6 * mm))

        # ── 4. Site Observations ──────────────────────────────────────────
        site_obs: dict = data.get("siteObservations") or {}
        story.extend(self._repeater_section_header("4. Site Observations", primary_hex, accent_hex))
        if site_obs:
            story.extend(self._render_checklist_table(site_obs, SITE_OBS_LABELS, primary_hex))
        else:
            story.append(Paragraph("<i>No site observations recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 5. Container Interior ─────────────────────────────────────────
        container: dict = data.get("containerInterior") or {}
        story.extend(self._repeater_section_header("5. Container Interior", primary_hex, accent_hex))
        if container:
            story.extend(self._render_checklist_table(container, CONTAINER_INT_LABELS, primary_hex))
        else:
            story.append(Paragraph("<i>No container interior data recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 6. Safety Observations ────────────────────────────────────────
        safety: dict = data.get("safetyObservations") or {}
        story.extend(self._repeater_section_header("6. Safety Observations", primary_hex, accent_hex))
        if safety:
            rows: list[list[str]] = [
                ["Basic Risk Assessment Performed", "Yes" if safety.get("basicRiskAssessmentPerformed") else "No"],
            ]
            nearby = safety.get("nearbyConstructionWork") or {}
            if isinstance(nearby, dict):
                rows.append(["Nearby Construction Work", "Yes" if nearby.get("passed") else "No"])
                if (nearby.get("issueDescription") or "").strip():
                    rows.append(["Construction Work Notes", nearby.get("issueDescription") or ""])
            story.append(_info_table(rows))
        else:
            story.append(Paragraph("<i>No safety observations recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 7. Environmental Systems ──────────────────────────────────────
        env: dict = data.get("environmentalSystems") or {}
        story.extend(self._repeater_section_header("7. Environmental Systems", primary_hex, accent_hex))
        if env:
            story.extend(self._render_environmental_systems(env, primary_hex))
        else:
            story.append(Paragraph("<i>No environmental systems data recorded.</i>", body_s))
        story.append(Spacer(1, 6 * mm))

        # ── 8. Site Concerns ──────────────────────────────────────────────
        concerns: dict = data.get("siteConcerns") or {}
        story.extend(self._repeater_section_header("8. Site Concerns", primary_hex, accent_hex))
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

        picture_groups: dict[str, list[Any]] = {}

        def _add_picture_group(title: str, values: Any) -> None:
            photos = _unique_photos(values)
            if not photos:
                return
            existing = picture_groups.get(title, [])
            picture_groups[title] = _unique_photos([*existing, *photos])

        concerns_pictures = (concerns or {}).get("pictures")
        site_pics: dict = data.get("sitePictures") or {}
        _add_picture_group("Site Concerns", concerns_pictures)
        _add_picture_group("Site Pictures", site_pics.get("pictures"))

        gen1_data: dict = data.get("gen1") or {}
        gen2_data: dict = data.get("gen2") or {}
        _add_picture_group("Generator 1 - Oil Level", gen1_data.get("oilLevelImages"))
        _add_picture_group("Generator 1 - Fuel Level", gen1_data.get("fuelLevelImages"))
        _add_picture_group("Generator 2 - Oil Level", gen2_data.get("oilLevelImages"))
        _add_picture_group("Generator 2 - Fuel Level", gen2_data.get("fuelLevelImages"))

        attachments = report.attachments if isinstance(report.attachments, dict) else {}
        attachment_files = attachments.get("files") if isinstance(attachments, dict) else []
        if isinstance(attachment_files, list):
            for file_item in attachment_files:
                if not isinstance(file_item, dict):
                    continue
                title = str(file_item.get("label") or "Additional Attachments")
                _add_picture_group(title, [file_item])

        if picture_groups:
            story.extend(self._repeater_section_header("9. Report Pictures", primary_hex, accent_hex))
            photo_group_title_style = ParagraphStyle(
                "RpPhotoGroupTitle",
                parent=self.styles["Normal"],
                fontSize=10,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor(primary_hex),
                spaceBefore=4,
                spaceAfter=2,
            )
            for title, photos in picture_groups.items():
                story.append(Paragraph(title, photo_group_title_style))
                self._render_photo_grid(photos, story, cols=3)
                story.append(Spacer(1, 3 * mm))


def get_pdf_service() -> PDFService:
    return PDFService()
