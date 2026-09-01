"""Per-person progress dashboard (contract E).

A server-rendered, Valtaris-branded page at /valtaris/dashboard showing the
logged-in worker their own progress: units annotated, reviews validated, and
per-project pending counts. Rendered in Django (no LS React rebuild), reusing the
session login. Reads live annotation data; classifies review vs annotation with
the same rule the bridge uses (review.extract_review).
"""

import html

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from .review import extract_review, portal_id_for_user_id


def _compute(user):
    from tasks.models import Annotation, Task

    anns = list(
        Annotation.objects.filter(completed_by=user).select_related("task", "project")
    )
    annotated = 0
    validated = 0
    annotated_task_ids_by_project = {}
    for a in anns:
        if extract_review(a) is not None:
            validated += 1
        else:
            annotated += 1
            pid = a.project_id or (a.task.project_id if a.task else None)
            if pid:
                annotated_task_ids_by_project.setdefault(pid, set()).add(a.task_id)

    # Per-project pending = tasks in the worker's member projects they haven't
    # annotated yet. Falls back to projects they've already worked in.
    from projects.models import Project, ProjectMember

    member_project_ids = set(
        ProjectMember.objects.filter(user=user, enabled=True).values_list("project_id", flat=True)
    )
    project_ids = member_project_ids | set(annotated_task_ids_by_project.keys())
    projects = []
    for project in Project.objects.filter(id__in=project_ids):
        total = Task.objects.filter(project=project).count()
        mine = len(annotated_task_ids_by_project.get(project.id, set()))
        projects.append(
            {"title": project.title, "total": total, "mine": mine, "pending": max(total - mine, 0)}
        )
    projects.sort(key=lambda p: p["title"] or "")
    return {"annotated": annotated, "validated": validated, "projects": projects}


def _render(user, stats):
    portal_id = portal_id_for_user_id(getattr(user, "id", None))
    name = html.escape(getattr(user, "email", "") or "worker")
    rows = "".join(
        f"<tr><td>{html.escape(p['title'] or '')}</td>"
        f"<td class='num'>{p['mine']}</td><td class='num'>{p['pending']}</td>"
        f"<td class='num'>{p['total']}</td></tr>"
        for p in stats["projects"]
    ) or "<tr><td colspan='4' class='muted'>No projects yet.</td></tr>"
    linked = (
        f"<span class='muted'>Valtaris ID {html.escape(portal_id)}</span>"
        if portal_id
        else "<span class='muted'>Not linked to a Valtaris account</span>"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<title>My Progress — Valtaris Studio</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="icon" type="image/svg+xml" href="/static/images/favicon.svg"/>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet"/>
<style>
  :root {{ --bg:#08090C; --surface:#0E1015; --surface2:#14171F; --ink:#F4F6FA;
           --muted:#A2A9B8; --teal:#2EB395; --line:rgba(255,255,255,.08); }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
          font-family:"Montserrat","Helvetica Neue",Arial,sans-serif; }}
  .wrap {{ max-width:920px; margin:0 auto; padding:40px 24px; }}
  .top {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }}
  h1 {{ font-size:26px; margin:0; }}
  a.back {{ color:var(--teal); text-decoration:none; font-weight:600; font-size:14px; }}
  .sub {{ color:var(--muted); margin:0 0 28px; font-size:14px; }}
  .cards {{ display:flex; gap:16px; margin-bottom:28px; flex-wrap:wrap; }}
  .card {{ background:var(--surface); border:1px solid var(--line); border-radius:14px;
           padding:22px 26px; min-width:180px; }}
  .card .n {{ font-size:38px; font-weight:700; color:var(--teal); line-height:1; }}
  .card .l {{ color:var(--muted); margin-top:8px; font-size:13px; text-transform:uppercase; letter-spacing:.08em; }}
  table {{ width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--line);
           border-radius:14px; overflow:hidden; }}
  th,td {{ padding:12px 16px; text-align:left; border-bottom:1px solid var(--line); font-size:14px; }}
  th {{ color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.06em; font-size:12px; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .muted {{ color:var(--muted); }}
  tr:last-child td {{ border-bottom:none; }}
</style></head><body><div class="wrap">
  <div class="top"><h1>My Progress</h1><a class="back" href="/projects/">← Back to Studio</a></div>
  <p class="sub">{name} · {linked}</p>
  <div class="cards">
    <div class="card"><div class="n">{stats['annotated']}</div><div class="l">Annotated</div></div>
    <div class="card"><div class="n">{stats['validated']}</div><div class="l">Validated (reviews)</div></div>
  </div>
  <table>
    <thead><tr><th>Project</th><th class="num">Done by me</th><th class="num">Pending</th><th class="num">Total</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div></body></html>"""


@login_required
def dashboard(request):
    stats = _compute(request.user)
    return HttpResponse(_render(request.user, stats))
