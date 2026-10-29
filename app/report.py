"""Human-readable report rendered purely from claim_packet.json."""

from jinja2 import Environment

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contents claim {{ p.sweep.id }}</title>
<style>
body{font:14px/1.45 system-ui,sans-serif;margin:24px;color:#1b1f24;background:#fff}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border:1px solid #e1e4e8;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f6f8fa}td.n{text-align:right;white-space:nowrap}.muted{color:#6a737d}.flag{color:#b35900;font-weight:600}
.kpi{display:inline-block;margin:0 18px 8px 0}.kpi b{display:block;font-size:20px}
.cust{background:#fff4e0;color:#7a4a00;border:1px solid #f1d29a;border-radius:8px;padding:10px 12px;margin:12px 0}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 6px;border-radius:999px;background:#fff4e0;color:#7a4a00}
</style></head><body>
{% if p.packet_type == "customer_advised" %}
<h1>Customer-advised claim packet <span class="muted" style="font-size:14px">version {{ p.customer_version }}</span></h1>
<div class="cust"><b>Contains values stated by the policyholder.</b> Lines marked <span class="tag">customer-advised</span> were
changed at the policyholder's request and are not independently verified; the system's own values are kept in the JSON
(<code>system_values</code>). The verified packet (claim_packet.json) is unchanged: verified books replacement
{{ "{:,.0f}".format(p.based_on.verified_totals.books_replacement_cost) }} {{ p.totals.currency }}, used value
{{ "{:,.0f}".format(p.based_on.verified_totals.books_used_value) }} {{ p.totals.currency }}.</div>
{% else %}
<h1>Home library contents claim</h1>
{% endif %}
<p class="muted">Sweep {{ p.sweep.id }} · captured {{ p.sweep.captured_at }} · {{ p.sweep.duration_s }} s · {{ p.sweep.country }} / {{ p.sweep.currency }} · {{ p.sweep.frames_saved }} frames saved, {{ p.sweep.keyframes_analysed }} analysed</p>
{% set t = p.totals %}
<div>
<span class="kpi"><b>{{ t.book_count }}</b>books ({{ t.books_identified }} identified, {{ t.books_unidentified }} unidentified, {{ t.books_needs_appraisal }} appraisal)</span>
<span class="kpi"><b>{{ "{:,.0f}".format(t.books_replacement_cost) }} {{ t.currency }}</b>books replacement</span>
<span class="kpi"><b>{{ "{:,.0f}".format(t.books_used_value) }} {{ t.currency }}</b>books used value</span>
<span class="kpi"><b>{{ "{:,.0f}".format(t.items_replacement_cost_low) }}–{{ "{:,.0f}".format(t.items_replacement_cost_high) }} {{ t.currency }}</b>other contents</span>
<span class="kpi"><b>{{ t.shelf_run_m }} m</b>shelf run</span>
<span class="kpi"><b>{{ t.excluded_from_totals }}</b>lines not in totals</span>
</div>

<h2>Room</h2>
{% set r = p.room %}
{% if r.floor_area_m2 %}
<table><tr><th>Length</th><th>Width</th><th>Height</th><th>Floor</th><th>Walls (gross)</th><th>Shelved wall</th><th>Confidence</th></tr>
<tr><td>{{ r.length_m }} m</td><td>{{ r.width_m }} m</td><td>{{ r.height_m }} m</td>
<td>{{ r.floor_area_m2 }} m² / {{ r.floor_area_ft2 }} ft²</td><td>{{ r.wall_area_m2 }} m² / {{ r.wall_area_ft2 }} ft²</td>
<td>{{ r.shelved_wall_area_m2 }} m² / {{ r.shelved_wall_area_ft2 }} ft²</td><td>{{ r.confidence }}</td></tr></table>
{% else %}<p class="flag">Room not measured.</p>{% endif %}
<p class="muted">Scale: {{ r.scale_method }}. {{ r.shape_handling }} {{ r.notes|join("; ") }}.
Frames: {% for f in r.frame_refs %}<a href="{{ f }}">{{ f }}</a> {% endfor %}</p>

<h2>Books</h2>
<table><tr><th>ID</th><th>Status</th><th>Title / author</th><th>Spine read</th><th class="n">H × T cm</th><th class="n">Replacement</th><th class="n">Used</th><th>Evidence</th></tr>
{% for b in p.books %}<tr>
<td>{{ b.id }}</td><td{% if b.status != "identified" %} class="flag"{% endif %}>{{ b.status }}{% if b.id_confidence %} ({{ b.id_confidence }}){% endif %}</td>
<td>{{ b.title }}<br><span class="muted">{{ b.author }}{% if b.edition %} · {{ b.edition }}{% endif %}{% if b.isbn %} · ISBN {{ b.isbn }}{% endif %}</span>
{% if b.customer_advised %}<br><span class="tag">customer-advised</span> <span class="muted">"{{ b.user_comment }}"</span>{% endif %}</td>
<td class="muted">{{ b.spine_text_read.title_text }} {{ b.spine_text_read.author_text }}</td>
<td class="n">{{ b.spine_height_cm if b.spine_height_cm is not none else "—" }} × {{ b.spine_thickness_cm if b.spine_thickness_cm is not none else "—" }}</td>
{% for m in [b.replacement_cost, b.used_value] %}<td class="n">{% if m.amount is not none %}{% if m.url %}<a href="{{ m.url }}">{{ "{:,.0f}".format(m.amount) }}</a>{% else %}{{ "{:,.0f}".format(m.amount) }} <span class="tag">stated</span>{% endif %}{% if m.converted %}*{% endif %}{% else %}—{% endif %}</td>{% endfor %}
<td>{% if b.frame_ref %}<a href="{{ b.frame_ref }}">frame @{{ b.frame_t_s }}s</a>{% else %}<span class="muted">added by policyholder</span>{% endif %}{% if b.catalogue.url %} · <a href="{{ b.catalogue.url }}">{{ b.catalogue.source }}</a>{% endif %}</td>
</tr>{% endfor %}</table>
<p class="muted">* converted from another currency at the dated ECB rate. Every price links to the page it came from; retrieval dates are in the JSON.</p>

<h2>Other contents</h2>
<table><tr><th>ID</th><th>Category</th><th>Description</th><th>Brand / model</th><th>Approx. W×H cm</th><th>Status</th><th class="n">Replacement</th><th>Evidence</th></tr>
{% for i in p['items'] %}<tr><td>{{ i.id }}</td><td>{{ i.category }}</td><td>{{ i.description }}<br><span class="muted">{{ i.material }}</span>
{% if i.customer_advised %}<br><span class="tag">customer-advised</span> <span class="muted">"{{ i.user_comment }}"</span>{% endif %}</td>
<td>{{ i.brand_model or "—" }}</td><td>{{ i.dimensions_cm.w or "—" }} × {{ i.dimensions_cm.h or "—" }}</td>
<td{% if i.status == "needs_appraisal" %} class="flag"{% endif %}>{{ i.status }}</td>
<td class="n">{% if i.replacement_cost.low is not none %}<a href="{{ i.replacement_cost.url }}">{{ "{:,.0f}".format(i.replacement_cost.low) }}</a>–<a href="{{ i.replacement_cost.high_url }}">{{ "{:,.0f}".format(i.replacement_cost.high) }}</a>{% else %}—{% endif %}</td>
<td><a href="{{ i.frame_ref }}">frame @{{ i.frame_t_s }}s</a></td></tr>{% endfor %}</table>

<h2>Review queue ({{ p.review_queue|length }})</h2>
<table><tr><th>Ref</th><th>Reason</th></tr>{% for q in p.review_queue %}<tr><td>{{ q.ref_id }}</td><td>{{ q.reason }}</td></tr>{% endfor %}</table>
{% if p.excluded %}<h2>Excluded by policyholder</h2><table>{% for e in p.excluded %}<tr><td>{{ e.ref_id }}</td><td>{{ e.reason }}</td></tr>{% endfor %}</table>{% endif %}

<h2>Locale check</h2><p class="muted">{{ p.locale_comparison.note }}</p>
<table><tr><th>Title</th>{% for c in [p.sweep.country, second] %}<th class="n">{{ c }} new</th><th class="n">{{ c }} used</th>{% endfor %}</tr>
{% for b in p.locale_comparison.books %}<tr><td>{{ b.title }}</td>
{% for c in [p.sweep.country, second] %}{% for f in ["replacement_cost", "used_value"] %}<td class="n">{% set m = b[c][f] %}{% if m and m.amount is not none %}<a href="{{ m.url }}">{{ "{:,.2f}".format(m.amount) }}</a>{% else %}—{% endif %}</td>{% endfor %}{% endfor %}
</tr>{% endfor %}</table>

<h2>Method, latency and cost</h2>
<ul>{% for k, v in p.method.items() %}<li><b>{{ k }}</b>: {{ v }}</li>{% endfor %}</ul>
<table><tr><th>Stage</th><th class="n">Calls</th><th class="n">Total s</th><th class="n">Mean s</th></tr>
{% for k, v in p.metrics.latency_s.items() %}<tr><td>{{ k }}</td><td class="n">{{ v.calls }}</td><td class="n">{{ v.total }}</td><td class="n">{{ v.mean }}</td></tr>{% endfor %}</table>
<p class="muted">Time to packet after sweep end: {{ p.metrics.time_to_packet_s }} s. Vision: {{ p.metrics.vision }}. Pricing: {{ p.metrics.pricing }}.
Cost: {{ p.metrics.estimated_cost_usd if p.metrics.estimated_cost_usd is not none else "n/a" }} — {{ p.metrics.cost_note }}.</p>
</body></html>"""

_env = Environment(autoescape=True)
_template = _env.from_string(TEMPLATE)


def render(packet: dict) -> str:
    second = next((k for k in (packet["locale_comparison"]["books"][0].keys() if packet["locale_comparison"]["books"] else [])
                   if k not in ("title", "author", packet["sweep"]["country"])), "")
    return _template.render(p=packet, second=second)
