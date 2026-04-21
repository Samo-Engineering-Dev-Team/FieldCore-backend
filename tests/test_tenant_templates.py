from sqlmodel import SQLModel, Session, create_engine

import app.services.email as email_module
from app.api.v1.template import router as template_router
from app.models import TenantTemplate
from app.services.auth import require_platform_admin
from app.services.email import EmailService
from app.services.pdf import PDFService
from app.services.template import get_template_service


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[TenantTemplate.__table__])
    return Session(engine)


def test_template_resolver_uses_latest_tenant_version_then_platform_fallback() -> None:
    service = get_template_service()

    with _session() as session:
        session.add(
            TenantTemplate(
                template_name="email.task_completed",
                tenant_id=None,
                version=1,
                content="platform default",
            )
        )
        session.add(
            TenantTemplate(
                template_name="email.task_completed",
                tenant_id="tenant-alpha",
                version=1,
                content="tenant old",
            )
        )
        session.add(
            TenantTemplate(
                template_name="email.task_completed",
                tenant_id="tenant-alpha",
                version=2,
                content="tenant latest",
            )
        )
        session.commit()

        tenant_template = service.resolve_template(
            session,
            "tenant-alpha",
            "email.task_completed",
        )
        assert tenant_template.content == "tenant latest"
        assert tenant_template.source == "tenant"
        assert tenant_template.version == 2

        fallback_template = service.resolve_template(
            session,
            "tenant-beta",
            "email.task_completed",
        )
        assert fallback_template.content == "platform default"
        assert fallback_template.source == "platform"
        assert fallback_template.tenant_id is None


def test_template_preview_renders_resolved_content() -> None:
    service = get_template_service()

    with _session() as session:
        session.add(
            TenantTemplate(
                template_name="email.custom",
                tenant_id=None,
                version=1,
                content={
                    "subject": "Hello {{name}}",
                    "html": "<p>{{nested.value}}</p>",
                },
            )
        )
        session.commit()

        preview = service.preview_template(
            session,
            tenant_id="tenant-alpha",
            template_name="email.custom",
            context={"name": "Ada", "nested": {"value": 42}},
        )

        assert preview.source == "platform"
        assert preview.rendered_content == {
            "subject": "Hello Ada",
            "html": "<p>42</p>",
        }


def test_email_generation_uses_tenant_template(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def capture(to, subject, html):
        captured["to"] = list(to)
        captured["subject"] = subject
        captured["html"] = html

    monkeypatch.setattr(email_module, "_fire_and_forget", capture)

    with _session() as session:
        session.add(
            TenantTemplate(
                template_name="email.task_completed",
                tenant_id="tenant-alpha",
                version=1,
                content={
                    "subject": "Tenant {{site_name}} done",
                    "body_html": "<p>{{technician_name}} finished {{task_type_label}}</p>",
                },
            )
        )
        session.commit()

        EmailService.send_task_completed(
            ref_no="REF-1",
            site_name="Site A",
            technician_name="Ada Admin",
            task_type="routine_maintenance",
            completed_at="Now",
            recipients=["ops@example.com"],
            session=session,
            tenant_id="tenant-alpha",
        )

    assert captured["to"] == ["ops@example.com"]
    assert captured["subject"] == "Tenant Site A done"
    assert "Ada Admin finished Routine Maintenance" in str(captured["html"])


def test_pdf_service_resolves_tenant_branding_template() -> None:
    with _session() as session:
        session.add(
            TenantTemplate(
                template_name="pdf.branding",
                tenant_id="tenant-alpha",
                version=1,
                content={
                    "brand": "ACME Field",
                    "report_label": "ACME Field Report",
                    "confidential": "ACME confidential",
                },
            )
        )
        session.commit()

        service = PDFService()
        service.configure_templates(session, "tenant-alpha")

        assert service._brand_name() == "ACME Field"
        assert service._brand_report_label() == "ACME Field Report"
        assert service._brand_confidential() == "ACME confidential"


def test_template_preview_router_requires_platform_admin() -> None:
    assert any(
        getattr(dependency, "dependency", None) == require_platform_admin
        for dependency in template_router.dependencies
    )
