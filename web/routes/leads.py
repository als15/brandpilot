"""Leads management page."""

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from web import templates
from web.db import query, execute
from web.brand_switcher import get_dashboard_brand, get_brand_context

router = APIRouter()


def _render_lead_status_control(lead_id: int, selected_status: str) -> HTMLResponse:
    options = []
    for status in ["discovered", "researched", "contacted", "converted"]:
        selected = " selected" if status == selected_status else ""
        options.append(f'<option value="{status}"{selected}>{status.replace("_", " ").title()}</option>')

    return HTMLResponse(
        f'<div class="leads-status-wrap" id="lead-status-{lead_id}">'
        f'<form hx-post="/leads/{lead_id}/status" hx-target="#lead-status-{lead_id}" hx-swap="outerHTML">'
        f'<select name="status" onchange="this.form.requestSubmit()" class="leads-status-select badge-{escape(selected_status)}">'
        f'{"".join(options)}'
        f'</select>'
        f'</form>'
        f'</div>'
    )


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(request: Request):
    from web.routes.dashboard import _global_stats

    brand_id = get_dashboard_brand(request)
    status_filter = request.query_params.get("status", "")
    type_filter = request.query_params.get("type", "")

    conditions = ["brand_id = ?"]
    params = [brand_id]
    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("business_type = ?")
        params.append(type_filter)

    where = "WHERE " + " AND ".join(conditions)

    leads_rows = await query(
        f"SELECT id, created_at, business_name, instagram_handle, business_type, "
        f"location, follower_count, source, status, notes "
        f"FROM leads {where} ORDER BY created_at DESC LIMIT 100",
        tuple(params),
    )

    # Funnel counts
    funnel_rows = await query(
        "SELECT status, COUNT(*) as count FROM leads WHERE brand_id = ? GROUP BY status",
        (brand_id,),
    )
    funnel = {r["status"]: r["count"] for r in funnel_rows}

    # Business types for filter
    types = await query(
        "SELECT DISTINCT business_type FROM leads WHERE brand_id = ? AND business_type IS NOT NULL ORDER BY business_type",
        (brand_id,),
    )

    stats = await _global_stats(brand_id)

    return templates.TemplateResponse(request, "pages/leads.html", {
        "active_page": "leads",
        "stats": stats,
        "leads": leads_rows,
        "funnel": funnel,
        "business_types": [r["business_type"] for r in types],
        "status_filter": status_filter,
        "type_filter": type_filter,
        **get_brand_context(request),
    })


@router.post("/leads/{lead_id}/status", response_class=HTMLResponse)
async def update_lead_status(request: Request, lead_id: int):
    brand_id = get_dashboard_brand(request)
    form = await request.form()
    new_status = form.get("status", "")
    valid = {"discovered", "researched", "contacted", "converted"}
    if new_status not in valid:
        return HTMLResponse('<span class="badge badge-failed">Invalid status</span>')

    await execute(
        "UPDATE leads SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ? AND brand_id = ?",
        (new_status, lead_id, brand_id),
    )
    return _render_lead_status_control(lead_id, new_status)
