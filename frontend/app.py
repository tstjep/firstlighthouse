#!/usr/bin/env python3
"""
firstlighthouse campaign editor — FastAPI + Jinja2
Stateless server-side rendering, no websockets, runs on AWS App Runner.

Start:
    PYTHONPATH="" ./venv/bin/python -m uvicorn frontend.app:app --reload --port 8000
"""

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Request
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from campaign import Campaign, Region, LinkedInConfig, RatingConfig, Segment, SearchConfig, ContactConfig, Signal
from run_manager import MODES, start_run, load_state, tail_log, validate_run_request
from store import ResultStore, to_waalaxy_csv, to_lemlist_csv
from suggest_signals import suggest as suggest_signals, suggest_more as suggest_more_signals
from suggest_roles import suggest as suggest_roles

logger = logging.getLogger(__name__)

app = FastAPI(title="firstlighthouse")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ── Form helpers ────────────────────────────────────────────────────────────────

def _str(form: FormData, key: str, default: str = "") -> str:
    v = form.get(key, default)
    return str(v).strip() if v is not None else default


def _int(form: FormData, key: str, default: int = 0) -> int:
    try:
        return int(_str(form, key) or default)
    except (ValueError, TypeError):
        return default


def _bool(form: FormData, key: str) -> bool:
    return form.get(key) == "1"


def _csv(form: FormData, key: str) -> list[str]:
    return [item.strip() for item in _str(form, key).split(",") if item.strip()]


def _lines(form: FormData, key: str) -> list[str]:
    return [line.strip() for line in _str(form, key).splitlines() if line.strip()]


def _parse_campaign(form: FormData, campaign_id: str) -> Campaign:
    n_signals  = max(0, _int(form, "signal_count"))
    n_segments = max(0, _int(form, "segment_count"))

    signals = []
    for i in range(n_signals):
        raw_key = _str(form, f"signals.{i}.key")
        name    = _str(form, f"signals.{i}.name")
        key     = raw_key or name.lower().replace(" ", "_").replace("-", "_") or f"signal_{i}"
        signals.append(Signal(
            key=key,
            name=name,
            description=_str(form, f"signals.{i}.description"),
            llm_definition=_str(form, f"signals.{i}.llm_definition"),
            keywords=_csv(form, f"signals.{i}.keywords"),
            points=_int(form, f"signals.{i}.points", default=1),
        ))

    segments = []
    for i in range(n_segments):
        segments.append(Segment(
            name=_str(form, f"segments.{i}.name"),
            description=_str(form, f"segments.{i}.description"),
            icp_context=_str(form, f"segments.{i}.icp_context"),
            enrich_context=_str(form, f"segments.{i}.enrich_context"),
            signals_enabled=_bool(form, f"segments.{i}.signals_enabled"),
            rating_enabled=_bool(form, f"segments.{i}.rating_enabled"),
            search=SearchConfig(
                tld_queries=_lines(form, f"segments.{i}.search.tld_queries"),
                extra_queries=_lines(form, f"segments.{i}.search.extra_queries"),
            ),
            contact=ContactConfig(
                roles=_csv(form, f"segments.{i}.contact.roles"),
            ),
        ))

    raw_id      = _str(form, "campaign_id") or _str(form, "name", "campaign").lower().replace(" ", "-")
    resolved_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw_id).strip("-") or "my-campaign"

    return Campaign(
        id=resolved_id if campaign_id == "__new__" else campaign_id,
        name=_str(form, "name"),
        product_context=_str(form, "product_context"),
        spreadsheet_id=_str(form, "spreadsheet_id"),
        credentials_file=_str(form, "credentials_file") or "melt2.json",
        export_format=_str(form, "export_format") or "waalaxy",
        region=Region(
            label=_str(form, "region.label"),
            country_code=_str(form, "region.country_code") or "gb",
            country_restrict=_str(form, "region.country_restrict") or "countryGB",
            tld=_str(form, "region.tld") or "co.uk",
        ),
        linkedin=LinkedInConfig(
            li_at=_str(form, "linkedin.li_at"),
            jsessionid=_str(form, "linkedin.jsessionid"),
        ),
        signals=signals,
        segments=segments,
        rating=RatingConfig(
            contact_threshold=_int(form, "rating.contact_threshold", default=8),
            sweet_spot_sizes=_csv(form, "rating.sweet_spot_sizes"),
        ),
    )


def _validate(campaign: Campaign) -> list[str]:
    errors = []
    if not campaign.name.strip():
        errors.append("Campaign name is required.")
    seen_keys: set[str] = set()
    for i, sig in enumerate(campaign.signals):
        if not sig.name:
            errors.append(f"Signal #{i + 1} needs a name.")
        if sig.key in seen_keys:
            errors.append(f"Signal #{i + 1}: duplicate key '{sig.key}'.")
        seen_keys.add(sig.key)
    seen_names: set[str] = set()
    for i, seg in enumerate(campaign.segments):
        if not seg.name:
            errors.append(f"Segment #{i + 1} needs a name.")
        if seg.name in seen_names:
            errors.append(f"Segment #{i + 1}: duplicate name '{seg.name}'.")
        seen_names.add(seg.name)
    return errors


def _render_editor(request: Request, campaign: Campaign,
                   errors: list[str] | None = None,
                   flash: dict | None = None) -> HTMLResponse:
    action_url = f"/campaigns/{campaign.id}/edit" if campaign.id != "__new__" else "/campaigns/new"
    return templates.TemplateResponse(request, "editor.html", {
        "campaign":      campaign,
        "campaign_json": json.dumps(campaign.model_dump(), indent=2, ensure_ascii=False),
        "action_url":    action_url,
        "errors":        errors or [],
        "flash":         flash,
    })


def _apply_structural_action(campaign: Campaign, action: str) -> Campaign:
    if action == "add_signal":
        campaign.signals.append(Signal(key="", name="", points=1))
    elif action == "add_segment":
        campaign.segments.append(Segment(name=""))
    elif action.startswith("remove_signal_"):
        idx = _safe_idx(action, "remove_signal_", len(campaign.signals))
        if idx is not None:
            campaign.signals.pop(idx)
    elif action.startswith("remove_segment_"):
        idx = _safe_idx(action, "remove_segment_", len(campaign.segments))
        if idx is not None:
            campaign.segments.pop(idx)
    return campaign


def _safe_idx(action: str, prefix: str, length: int) -> int | None:
    try:
        idx = int(action[len(prefix):])
        return idx if 0 <= idx < length else None
    except (ValueError, IndexError):
        return None


# ── Default signals for new campaigns ──────────────────────────────────────────

_DEFAULT_SIGNALS = [
    Signal(
        key="tech_stack",
        name="Tech-forward",
        description="Uses modern SaaS / cloud tools",
        llm_definition=(
            "Mark Yes if there is evidence the company uses modern software tools — "
            "e.g. job postings mention SaaS products, cloud platforms, or digital workflows; "
            "their website references integrations or an API; or they list tools like Salesforce, "
            "HubSpot, Slack, Notion, AWS, etc. Mark No if the company appears to rely on legacy "
            "systems, paper processes, or shows no sign of tech adoption."
        ),
        keywords=["SaaS", "cloud", "API", "software", "digital", "platform", "integration"],
        points=2,
    ),
    Signal(
        key="growth",
        name="Growing",
        description="Active hiring or recent expansion",
        llm_definition=(
            "Mark Yes if the company shows clear signs of growth: active job postings (especially "
            "in sales, ops, or product), recent funding rounds, new office openings, or press "
            "coverage about expansion. Mark No if hiring appears frozen, the company is downsizing, "
            "or there are no recent signals of growth."
        ),
        keywords=["hiring", "we're growing", "join our team", "funding", "Series A", "expansion"],
        points=2,
    ),
    Signal(
        key="decision_maker_reachable",
        name="Reachable buyer",
        description="Decision-maker visible and contactable",
        llm_definition=(
            "Mark Yes if a clear decision-maker (founder, CEO, Head of Operations, VP, Director) "
            "is visible on LinkedIn or the company website and the company is small enough "
            "(under ~500 employees) that outreach is likely to reach them directly. "
            "Mark No if the company is a large enterprise where the buyer is buried in org layers, "
            "or if no named leadership is visible."
        ),
        keywords=["founder", "CEO", "director", "head of", "VP", "owner"],
        points=1,
    ),
]


# ── Routes: campaign list ───────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def campaigns_list(request: Request, saved: str = ""):
    campaigns = Campaign.list_all()
    flash = {"type": "success", "message": "Campaign saved."} if saved else None
    return templates.TemplateResponse(request, "campaigns.html", {
        "campaigns": campaigns,
        "flash":     flash,
    })


# ── Routes: new campaign ────────────────────────────────────────────────────────

@app.get("/campaigns/new", response_class=HTMLResponse)
async def new_campaign_get(request: Request):
    draft = Campaign(id="my-campaign", name="", signals=list(_DEFAULT_SIGNALS),
                     rating=RatingConfig(contact_threshold=8))
    return _render_editor(request, draft)


@app.post("/campaigns/new", response_class=HTMLResponse)
async def new_campaign_post(request: Request):
    form   = await request.form()
    action = _str(form, "action", "save")
    campaign = _parse_campaign(form, "__new__")

    if action == "save":
        errors = _validate(campaign)
        if errors:
            return _render_editor(request, campaign, errors=errors)
        campaign.save()
        return RedirectResponse(f"/?saved={campaign.id}", status_code=303)

    return _render_editor(request, _apply_structural_action(campaign, action))


# ── Routes: edit campaign ───────────────────────────────────────────────────────

@app.get("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def edit_campaign_get(request: Request, campaign_id: str):
    try:
        campaign = Campaign.load(campaign_id)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/", status_code=303)
    return _render_editor(request, campaign)


@app.post("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def edit_campaign_post(request: Request, campaign_id: str):
    form   = await request.form()
    action = _str(form, "action", "save")
    campaign = _parse_campaign(form, campaign_id)

    if action == "save":
        errors = _validate(campaign)
        if errors:
            return _render_editor(request, campaign, errors=errors)
        campaign.save()
        return RedirectResponse(f"/?saved={campaign.id}", status_code=303)

    return _render_editor(request, _apply_structural_action(campaign, action))


# ── Routes: delete campaign ─────────────────────────────────────────────────────

@app.post("/campaigns/{campaign_id}/delete")
async def delete_campaign(campaign_id: str):
    try:
        Campaign.load(campaign_id).delete()
    except (FileNotFoundError, ValueError):
        pass
    return RedirectResponse("/", status_code=303)


# ── Routes: signal suggestions ─────────────────────────────────────────────────

@app.post("/campaigns/{campaign_id}/suggest-signals")
async def suggest_signals_route(request: Request, campaign_id: str):
    try:
        body = await request.json()
        icp  = str(body.get("product_context", "")).strip()
    except Exception:
        return JSONResponse({"error": "Invalid request body"}, status_code=400)

    if not icp:
        return JSONResponse({"error": "ICP description is required"}, status_code=422)

    try:
        signals = await suggest_signals(icp)
        return JSONResponse({"signals": signals})
    except Exception as exc:
        logger.error("suggest_signals_route error: %s", exc)
        return JSONResponse({"error": "Signal suggestion failed. Check your LLM provider config."}, status_code=500)


@app.post("/campaigns/{campaign_id}/suggest-more-signals")
async def suggest_more_signals_route(request: Request, campaign_id: str):
    try:
        body             = await request.json()
        icp              = str(body.get("product_context", "")).strip()
        existing_signals = body.get("existing_signals", [])
        if not isinstance(existing_signals, list):
            existing_signals = []
    except Exception:
        return JSONResponse({"error": "Invalid request body"}, status_code=400)

    if not icp:
        return JSONResponse({"error": "ICP description is required"}, status_code=422)

    try:
        signals = await suggest_more_signals(icp, existing_signals=existing_signals)
        return JSONResponse({"signals": signals})
    except Exception as exc:
        logger.error("suggest_more_signals_route error: %s", exc)
        return JSONResponse({"error": "Signal suggestion failed. Check your LLM provider config."}, status_code=500)


@app.post("/campaigns/{campaign_id}/suggest-roles")
async def suggest_roles_route(request: Request, campaign_id: str):
    try:
        body             = await request.json()
        icp              = str(body.get("product_context", "")).strip()
        existing_signals = body.get("existing_signals", [])
        if not isinstance(existing_signals, list):
            existing_signals = []
    except Exception:
        return JSONResponse({"error": "Invalid request body"}, status_code=400)

    if not icp:
        return JSONResponse({"error": "ICP description is required"}, status_code=422)

    try:
        roles = await suggest_roles(icp, existing_signals=existing_signals)
        return JSONResponse({"roles": roles})
    except Exception as exc:
        logger.error("suggest_roles_route error: %s", exc)
        return JSONResponse({"error": "Role suggestion failed. Check your LLM provider config."}, status_code=500)


# ── Routes: run campaign ────────────────────────────────────────────────────────

@app.get("/campaigns/{campaign_id}/run", response_class=HTMLResponse)
async def run_page(request: Request, campaign_id: str):
    try:
        campaign = Campaign.load(campaign_id)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "run.html", {
        "campaign": campaign,
        "modes":    MODES,
        "state":    load_state(campaign_id),
    })


@app.post("/campaigns/{campaign_id}/run")
async def run_start(request: Request, campaign_id: str):
    try:
        campaign = Campaign.load(campaign_id)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/", status_code=303)

    form    = await request.form()
    segment = _str(form, "segment")
    mode    = _str(form, "mode", "search")

    errors = validate_run_request(campaign_id, segment, mode, campaign.segment_names())
    if errors:
        return templates.TemplateResponse(request, "run.html", {
            "campaign": campaign,
            "modes":    MODES,
            "state":    load_state(campaign_id),
            "flash":    {"type": "error", "message": " · ".join(errors)},
        }, status_code=422)

    start_run(campaign_id, segment, mode)
    return RedirectResponse(f"/campaigns/{campaign_id}/run", status_code=303)


@app.get("/campaigns/{campaign_id}/run/status")
async def run_status(campaign_id: str):
    state = load_state(campaign_id)
    if not state:
        return JSONResponse({"status": "idle", "lines": []})
    return JSONResponse({
        "status":       state.status,
        "current_step": state.current_step,
        "mode":         state.mode,
        "segment":      state.segment,
        "started_at":   state.started_at,
        "finished_at":  state.finished_at,
        "error":        state.error,
        "lines":        tail_log(campaign_id, state.run_id),
    })


# ── Routes: results ─────────────────────────────────────────────────────────────

@app.get("/campaigns/{campaign_id}/results", response_class=HTMLResponse)
async def results_redirect(campaign_id: str):
    try:
        campaign = Campaign.load(campaign_id)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/", status_code=303)
    first = campaign.segments[0].name if campaign.segments else None
    if not first:
        return RedirectResponse(f"/campaigns/{campaign_id}/edit", status_code=303)
    return RedirectResponse(f"/campaigns/{campaign_id}/results/{first}", status_code=303)


@app.get("/campaigns/{campaign_id}/results/{segment}", response_class=HTMLResponse)
async def results_view(request: Request, campaign_id: str, segment: str):
    try:
        campaign = Campaign.load(campaign_id)
    except (FileNotFoundError, ValueError):
        return RedirectResponse("/", status_code=303)
    store        = ResultStore(campaign_id)
    all_segments = store.all_segments()
    # If the requested segment doesn't exist in results yet, show empty state
    # but ensure it's a valid campaign segment
    valid_segments = campaign.segment_names()
    if segment not in valid_segments and valid_segments:
        return RedirectResponse(
            f"/campaigns/{campaign_id}/results/{valid_segments[0]}", status_code=303
        )
    return templates.TemplateResponse(request, "results.html", {
        "campaign":       campaign,
        "all_segments":   all_segments,
        "active_segment": segment,
        "rows":           all_segments.get(segment, []),
    })


# ── Routes: CSV export ──────────────────────────────────────────────────────────

@app.get("/campaigns/{campaign_id}/results/{segment}/export/waalaxy")
async def export_waalaxy(campaign_id: str, segment: str):
    rows     = ResultStore(campaign_id).get_segment(segment)
    filename = f"{campaign_id}-{segment}-waalaxy.csv"
    return StreamingResponse(
        iter([to_waalaxy_csv(rows)]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/campaigns/{campaign_id}/results/{segment}/export/lemlist")
async def export_lemlist(campaign_id: str, segment: str):
    rows     = ResultStore(campaign_id).get_segment(segment)
    filename = f"{campaign_id}-{segment}-lemlist.csv"
    return StreamingResponse(
        iter([to_lemlist_csv(rows)]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
